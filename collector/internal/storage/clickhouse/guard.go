package clickhouse

import (
	"context"
	"log/slog"
	"math"
	"sync"
	"sync/atomic"
	"time"

	"github.com/ClickHouse/clickhouse-go/v2/lib/driver"
)

// StorageStats describes disk and table usage.
type StorageStats struct {
	UpdatedAt              time.Time  `json:"updated_at"`
	Available              bool       `json:"available"` // false when ClickHouse could not be queried
	DiskTotalBytes         uint64     `json:"disk_total_bytes"`
	DiskFreeBytes          uint64     `json:"disk_free_bytes"`
	DiskUsedPercent        float64    `json:"disk_used_percent"`
	DiskMaxUsagePercent    float64    `json:"disk_max_usage_percent"`
	DatabaseBytes          uint64     `json:"database_bytes"`
	FlowRecordsBytes       uint64     `json:"flow_records_bytes"`
	FlowRecordsRows        uint64     `json:"flow_records_rows"`
	OldestFlowRecord       *time.Time `json:"oldest_flow_record"`
	RetentionDays          int        `json:"retention_days"`
	DiskCapacityDays       *float64   `json:"disk_capacity_days"`       // days of flows the disk budget can hold at the observed rate
	EstimatedRetentionDays *float64   `json:"estimated_retention_days"` // min(retention_days, disk_capacity_days)
	PartitionsDropped      uint64     `json:"partitions_dropped_by_disk_guard"`
	InsertsSuspended       bool       `json:"inserts_suspended"` // disk above the hard limit
}

// Guard periodically measures storage and, when disk usage exceeds the
// budget, drops the oldest daily partitions (never today's) so that
// telemetry cannot fill the disk.
type Guard struct {
	conn           driver.Conn
	db             string
	retentionDays  int
	maxUsedPercent float64
	log            *slog.Logger

	mu       sync.RWMutex
	stats    StorageStats
	dropped  uint64
	critical atomic.Bool
	lastWarn time.Time
}

// Critical reports that disk usage is above the hard limit even after
// dropping old partitions; the sink stops inserting while it is true.
func (g *Guard) Critical() bool { return g.critical.Load() }

// hardLimit is the usage at which inserts are suspended.
func (g *Guard) hardLimit() float64 { return math.Min(g.maxUsedPercent+10, 98) }

// NewGuard creates a guard; maxUsedPercent is the disk usage budget (e.g. 85).
func NewGuard(conn driver.Conn, db string, retentionDays int, maxUsedPercent float64, log *slog.Logger) *Guard {
	return &Guard{conn: conn, db: db, retentionDays: retentionDays, maxUsedPercent: maxUsedPercent, log: log,
		stats: StorageStats{RetentionDays: retentionDays, DiskMaxUsagePercent: maxUsedPercent}}
}

// Stats returns the last measurement.
func (g *Guard) Stats() StorageStats {
	g.mu.RLock()
	defer g.mu.RUnlock()
	return g.stats
}

// Run measures every interval until ctx is cancelled.
func (g *Guard) Run(ctx context.Context, interval time.Duration) {
	g.Check(ctx)
	t := time.NewTicker(interval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			g.Check(ctx)
		}
	}
}

// Check measures storage once and enforces the disk budget.
func (g *Guard) Check(ctx context.Context) {
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()

	s, err := g.measure(ctx)
	if err != nil {
		g.mu.Lock()
		g.stats.Available = false
		g.stats.UpdatedAt = time.Now().UTC()
		g.mu.Unlock()
		return
	}
	// Drop at most a few partitions per check; merges free space asynchronously.
	for i := 0; i < 3 && s.DiskUsedPercent > g.maxUsedPercent; i++ {
		if !g.dropOldest(ctx, s.DiskUsedPercent) {
			break
		}
		if s, err = g.measure(ctx); err != nil {
			break
		}
	}
	critical := s.DiskUsedPercent >= g.hardLimit()
	if g.critical.Swap(critical) != critical || (critical && time.Since(g.lastWarn) > 10*time.Minute) {
		g.lastWarn = time.Now()
		if critical {
			g.log.Error("disk guard: disk usage above hard limit, suspending telemetry inserts",
				"disk_used_percent", s.DiskUsedPercent, "hard_limit_percent", g.hardLimit())
		} else {
			g.log.Info("disk guard: disk usage back under hard limit, inserts resumed", "disk_used_percent", s.DiskUsedPercent)
		}
	} else if s.DiskUsedPercent > g.maxUsedPercent && time.Since(g.lastWarn) > 10*time.Minute {
		g.lastWarn = time.Now()
		g.log.Warn("disk guard: disk usage above budget and no older partition to drop",
			"disk_used_percent", s.DiskUsedPercent, "limit_percent", g.maxUsedPercent)
	}
	g.mu.Lock()
	s.PartitionsDropped = g.dropped
	s.InsertsSuspended = critical
	g.stats = s
	g.mu.Unlock()
}

func (g *Guard) measure(ctx context.Context) (StorageStats, error) {
	s := StorageStats{UpdatedAt: time.Now().UTC(), Available: true, RetentionDays: g.retentionDays, DiskMaxUsagePercent: g.maxUsedPercent}

	// The disk holding ClickHouse data (the "default" disk).
	if err := g.conn.QueryRow(ctx, "SELECT total_space, free_space FROM system.disks WHERE name = 'default'").
		Scan(&s.DiskTotalBytes, &s.DiskFreeBytes); err != nil {
		return s, err
	}
	if s.DiskTotalBytes > 0 {
		s.DiskUsedPercent = math.Round(float64(s.DiskTotalBytes-s.DiskFreeBytes)/float64(s.DiskTotalBytes)*1000) / 10
	}
	if err := g.conn.QueryRow(ctx, "SELECT sum(bytes_on_disk) FROM system.parts WHERE active AND database = ?", g.db).
		Scan(&s.DatabaseBytes); err != nil {
		return s, err
	}
	var partitions uint64
	if err := g.conn.QueryRow(ctx, `SELECT sum(bytes_on_disk), sum(rows), uniqExact(partition)
		FROM system.parts WHERE active AND database = ? AND table = 'flow_records'`, g.db).
		Scan(&s.FlowRecordsBytes, &s.FlowRecordsRows, &partitions); err != nil {
		return s, err
	}
	if s.FlowRecordsRows > 0 {
		var oldest time.Time
		if err := g.conn.QueryRow(ctx, "SELECT min(timestamp) FROM "+g.db+".flow_records").Scan(&oldest); err == nil {
			o := oldest.UTC()
			s.OldestFlowRecord = &o
			// Extrapolate the daily volume only once an hour of data is stored.
			if stored := time.Since(oldest); stored >= time.Hour {
				perDay := float64(s.FlowRecordsBytes) / (stored.Hours() / 24)
				budget := float64(s.DiskTotalBytes)*g.maxUsedPercent/100 - float64(s.DiskTotalBytes-s.DiskFreeBytes) + float64(s.FlowRecordsBytes)
				if perDay > 0 {
					capacity := math.Round(math.Max(budget, 0)/perDay*10) / 10
					est := math.Min(capacity, float64(g.retentionDays))
					s.DiskCapacityDays, s.EstimatedRetentionDays = &capacity, &est
				}
			}
		}
	}
	return s, nil
}

// dropOldest drops the oldest daily partition of each telemetry table,
// keeping at least today's data. It reports whether anything was dropped.
func (g *Guard) dropOldest(ctx context.Context, used float64) bool {
	today := time.Now().UTC().Format("2006-01-02")
	dropped := false
	for _, table := range retentionTables {
		var partition string
		err := g.conn.QueryRow(ctx, `SELECT partition FROM system.parts
			WHERE active AND database = ? AND table = ? AND partition < ?
			GROUP BY partition ORDER BY partition LIMIT 1`, g.db, table, today).Scan(&partition)
		if err != nil || partition == "" {
			continue
		}
		if err := g.conn.Exec(ctx, "ALTER TABLE "+g.db+"."+table+" DROP PARTITION ?", partition); err != nil {
			g.log.Error("disk guard: drop partition failed", "table", table, "partition", partition, "error", err.Error())
			continue
		}
		g.mu.Lock()
		g.dropped++
		g.mu.Unlock()
		dropped = true
		g.log.Warn("disk guard: dropped oldest partition to protect disk space",
			"table", table, "partition", partition, "disk_used_percent", used, "limit_percent", g.maxUsedPercent)
	}
	return dropped
}
