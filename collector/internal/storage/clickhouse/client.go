// Package clickhouse persists normalized records, the exporter inventory and
// storage statistics in ClickHouse.
package clickhouse

import (
	"context"
	"embed"
	"fmt"
	"log/slog"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	ch "github.com/ClickHouse/clickhouse-go/v2"
	"github.com/ClickHouse/clickhouse-go/v2/lib/driver"
)

//go:embed migrations/*.sql
var migrationFS embed.FS

// Config holds connection and retention settings.
type Config struct {
	Addr          string // host:port (native protocol)
	Database      string
	Username      string
	Password      string
	RetentionDays int
}

var identRe = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*$`)

// Open creates a connection pool. It does not require the server to be up.
func Open(cfg Config) (driver.Conn, error) {
	if !identRe.MatchString(cfg.Database) {
		return nil, fmt.Errorf("invalid database name %q", cfg.Database)
	}
	return ch.Open(&ch.Options{
		Addr:            []string{cfg.Addr},
		Auth:            ch.Auth{Database: "default", Username: cfg.Username, Password: cfg.Password},
		DialTimeout:     5 * time.Second,
		MaxOpenConns:    8,
		MaxIdleConns:    4,
		ConnMaxLifetime: time.Hour,
		Compression:     &ch.Compression{Method: ch.CompressionLZ4},
	})
}

// Migrate creates the schema (idempotent) and applies the retention period.
func Migrate(ctx context.Context, conn driver.Conn, cfg Config, log *slog.Logger) error {
	files, err := migrationFS.ReadDir("migrations")
	if err != nil {
		return err
	}
	names := make([]string, 0, len(files))
	for _, f := range files {
		names = append(names, f.Name())
	}
	sort.Strings(names)
	for _, name := range names {
		b, err := migrationFS.ReadFile("migrations/" + name)
		if err != nil {
			return err
		}
		for i, stmt := range splitStatements(render(string(b), cfg)) {
			if err := conn.Exec(ctx, stmt); err != nil {
				return fmt.Errorf("migration %s statement %d: %w", name, i+1, err)
			}
		}
	}
	return applyRetention(ctx, conn, cfg, log)
}

func render(sql string, cfg Config) string {
	return strings.NewReplacer(
		"{{DB}}", cfg.Database,
		"{{RETENTION_DAYS}}", strconv.Itoa(cfg.RetentionDays),
	).Replace(sql)
}

// splitStatements splits on ';' at end of line and drops comment-only chunks.
func splitStatements(sql string) []string {
	var out []string
	var cur strings.Builder
	for _, line := range strings.Split(sql, "\n") {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "--") {
			continue
		}
		cur.WriteString(line)
		cur.WriteString("\n")
		if strings.HasSuffix(trimmed, ";") {
			if s := strings.TrimSuffix(strings.TrimSpace(cur.String()), ";"); s != "" {
				out = append(out, s)
			}
			cur.Reset()
		}
	}
	if s := strings.TrimSpace(cur.String()); s != "" {
		out = append(out, s)
	}
	return out
}

var retentionTables = []string{"flow_records", "interface_counters"}

// applyRetention changes the TTL only when it differs from the configured
// value, without rewriting existing parts (expired daily partitions are
// dropped by TTL merges).
func applyRetention(ctx context.Context, conn driver.Conn, cfg Config, log *slog.Logger) error {
	want := fmt.Sprintf("toIntervalDay(%d)", cfg.RetentionDays)
	for _, table := range retentionTables {
		var create string
		row := conn.QueryRow(ctx, "SELECT create_table_query FROM system.tables WHERE database = ? AND name = ?", cfg.Database, table)
		if err := row.Scan(&create); err != nil {
			return fmt.Errorf("read ttl of %s: %w", table, err)
		}
		if strings.Contains(create, want) {
			continue
		}
		q := fmt.Sprintf("ALTER TABLE %s.%s MODIFY TTL toDateTime(timestamp) + INTERVAL %d DAY DELETE", cfg.Database, table, cfg.RetentionDays)
		if err := conn.Exec(ch.Context(ctx, ch.WithSettings(ch.Settings{"materialize_ttl_after_modify": 0})), q); err != nil {
			return fmt.Errorf("set retention on %s: %w", table, err)
		}
		log.Info("retention updated", "table", table, "retention_days", cfg.RetentionDays)
	}
	return nil
}

// WaitReady pings and migrates until it succeeds or ctx ends, logging
// failures at a bounded rate. It returns nil once the schema is ready.
func WaitReady(ctx context.Context, conn driver.Conn, cfg Config, log *slog.Logger) error {
	delay := time.Second
	lastLog := time.Time{}
	for {
		err := conn.Ping(ctx)
		if err == nil {
			err = Migrate(ctx, conn, cfg, log)
			if err == nil {
				log.Info("clickhouse ready", "addr", cfg.Addr, "database", cfg.Database, "retention_days", cfg.RetentionDays)
				return nil
			}
		}
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if time.Since(lastLog) > time.Minute {
			log.Warn("clickhouse not ready, retrying", "addr", cfg.Addr, "error", err.Error())
			lastLog = time.Now()
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(delay):
		}
		if delay < 30*time.Second {
			delay *= 2
		}
	}
}
