package clickhouse

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"sync"
	"sync/atomic"
	"time"

	"github.com/ClickHouse/clickhouse-go/v2/lib/driver"

	"sflow-analytics/collector/internal/metrics"
	"sflow-analytics/collector/internal/normalize"
)

// SinkOptions tunes batching and buffering.
type SinkOptions struct {
	BatchSize      int           // rows per INSERT (flush earlier on FlushInterval)
	FlushInterval  time.Duration // max time a row waits before being sent
	QueueRows      int           // rows buffered between decoders and the writer
	MaxPendingRows int           // rows kept for retry while the database is unavailable
}

// Sink batches records and inserts them into ClickHouse. Writes never block
// the decode path: when buffers are full, rows are dropped and counted.
type Sink struct {
	conn driver.Conn
	db   string
	opts SinkOptions
	log  *slog.Logger
	m    *metrics.Collector

	flows    chan normalize.FlowRecord
	counters chan normalize.InterfaceCounters
	ready    atomic.Bool
	suspend  func() bool // when true, batches are dropped instead of inserted (disk critical)
	stop     chan struct{}
	done     chan struct{}
	once     sync.Once

	// Owned by the run goroutine.
	fbuf         []normalize.FlowRecord
	cbuf         []normalize.InterfaceCounters
	pending      []pendingBatch
	pendingRows  int
	nextTry      time.Time
	backoff      time.Duration
	lastErrorLog time.Time
}

// errBadRow marks a batch that the client refused to encode.
var errBadRow = errors.New("row rejected")

type pendingBatch struct {
	flows    []normalize.FlowRecord
	counters []normalize.InterfaceCounters
}

func (b pendingBatch) rows() int { return len(b.flows) + len(b.counters) }

// NewSink starts the writer goroutine. Rows are buffered until SetReady is
// called (schema created).
func NewSink(conn driver.Conn, database string, opts SinkOptions, log *slog.Logger, m *metrics.Collector) *Sink {
	if opts.BatchSize <= 0 {
		opts.BatchSize = 5000
	}
	if opts.FlushInterval <= 0 {
		opts.FlushInterval = time.Second
	}
	if opts.QueueRows <= 0 {
		opts.QueueRows = 200_000
	}
	if opts.MaxPendingRows < opts.BatchSize {
		opts.MaxPendingRows = opts.BatchSize
	}
	s := &Sink{
		conn: conn, db: database, opts: opts, log: log, m: m,
		flows:    make(chan normalize.FlowRecord, opts.QueueRows),
		counters: make(chan normalize.InterfaceCounters, opts.QueueRows/10+1),
		stop:     make(chan struct{}),
		done:     make(chan struct{}),
		backoff:  time.Second,
	}
	go s.run()
	return s
}

// SetSuspendCheck installs a check (typically Guard.Critical) consulted before
// each flush. Must be called before SetReady.
func (s *Sink) SetSuspendCheck(f func() bool) { s.suspend = f }

// SetReady allows inserts to start.
func (s *Sink) SetReady() { s.ready.Store(true) }

// WriteFlows implements storage.Sink.
func (s *Sink) WriteFlows(_ context.Context, flows []normalize.FlowRecord) error {
	for i := range flows {
		select {
		case s.flows <- flows[i]:
			s.m.PendingRows.Add(1)
		default:
			// Queue full: counted in db_rows_dropped, reported by the stats log.
			s.m.RowsDropped.Add(uint64(len(flows) - i))
			return nil
		}
	}
	return nil
}

// WriteCounters implements storage.Sink.
func (s *Sink) WriteCounters(_ context.Context, counters []normalize.InterfaceCounters) error {
	for i := range counters {
		select {
		case s.counters <- counters[i]:
			s.m.PendingRows.Add(1)
		default:
			s.m.RowsDropped.Add(uint64(len(counters) - i))
			return nil
		}
	}
	return nil
}

// Close flushes what it can within 15 s and stops the writer.
func (s *Sink) Close() error {
	s.once.Do(func() { close(s.stop) })
	select {
	case <-s.done:
	case <-time.After(20 * time.Second):
		return fmt.Errorf("clickhouse sink: timeout while flushing")
	}
	return nil
}

func (s *Sink) run() {
	defer close(s.done)
	tick := time.NewTicker(s.opts.FlushInterval)
	defer tick.Stop()
	for {
		select {
		case f := <-s.flows:
			s.fbuf = append(s.fbuf, f)
			if len(s.fbuf) >= s.opts.BatchSize {
				s.seal()
				s.tryFlush(time.Now())
			}
		case c := <-s.counters:
			s.cbuf = append(s.cbuf, c)
			if len(s.cbuf) >= s.opts.BatchSize {
				s.seal()
				s.tryFlush(time.Now())
			}
		case now := <-tick.C:
			s.seal()
			s.tryFlush(now)
		case <-s.stop:
			s.drain()
			return
		}
	}
}

// seal moves the current buffers into the retry-capable pending queue.
func (s *Sink) seal() {
	if len(s.fbuf) > 0 {
		s.pending = append(s.pending, pendingBatch{flows: s.fbuf})
		s.pendingRows += len(s.fbuf)
		s.fbuf = nil
	}
	if len(s.cbuf) > 0 {
		s.pending = append(s.pending, pendingBatch{counters: s.cbuf})
		s.pendingRows += len(s.cbuf)
		s.cbuf = nil
	}
	// Bound memory while the database is down: drop the oldest batches.
	for s.pendingRows > s.opts.MaxPendingRows && len(s.pending) > 1 {
		n := s.pending[0].rows()
		s.pending = s.pending[1:]
		s.pendingRows -= n
		s.m.RowsDropped.Add(uint64(n))
		s.m.PendingRows.Add(-int64(n))
	}
}

func (s *Sink) tryFlush(now time.Time) {
	if !s.ready.Load() || now.Before(s.nextTry) {
		return
	}
	if s.suspend != nil && s.suspend() {
		// Disk critical: do not write, do not accumulate either.
		s.m.RowsDropped.Add(uint64(s.pendingRows))
		s.m.PendingRows.Add(-int64(s.pendingRows))
		s.pending, s.pendingRows = nil, 0
		return
	}
	for len(s.pending) > 0 {
		b := s.pending[0]
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		err := s.insert(ctx, b)
		cancel()
		if errors.Is(err, errBadRow) {
			// Retrying cannot succeed: drop the batch instead of blocking the queue.
			s.m.SinkErrors.Add(1)
			s.log.Error("dropping batch rejected by the client", "rows", b.rows(), "error", err.Error())
			s.pending = s.pending[1:]
			s.pendingRows -= b.rows()
			s.m.RowsDropped.Add(uint64(b.rows()))
			s.m.PendingRows.Add(-int64(b.rows()))
			continue
		}
		if err != nil {
			s.m.SinkErrors.Add(1)
			s.nextTry = now.Add(s.backoff)
			if s.backoff < 30*time.Second {
				s.backoff *= 2
			}
			if time.Since(s.lastErrorLog) > 30*time.Second {
				s.lastErrorLog = time.Now()
				s.log.Warn("clickhouse insert failed, will retry", "rows", b.rows(),
					"pending_rows", s.pendingRows, "retry_in", s.backoff.String(), "error", err.Error())
			}
			return
		}
		n := b.rows()
		s.pending = s.pending[1:]
		s.pendingRows -= n
		s.m.RowsInserted.Add(uint64(n))
		s.m.PendingRows.Add(-int64(n))
		s.backoff = time.Second
	}
}

// drain empties the channels and makes a last flush attempt.
func (s *Sink) drain() {
	for {
		select {
		case f := <-s.flows:
			s.fbuf = append(s.fbuf, f)
			continue
		case c := <-s.counters:
			s.cbuf = append(s.cbuf, c)
			continue
		default:
		}
		break
	}
	s.seal()
	s.nextTry = time.Time{}
	s.tryFlush(time.Now())
	if s.pendingRows > 0 {
		s.log.Warn("clickhouse sink stopped with unsent rows", "rows", s.pendingRows)
		s.m.RowsDropped.Add(uint64(s.pendingRows))
	}
}

func (s *Sink) insert(ctx context.Context, b pendingBatch) error {
	if len(b.flows) > 0 {
		return s.insertFlows(ctx, b.flows)
	}
	return s.insertCounters(ctx, b.counters)
}

func (s *Sink) insertFlows(ctx context.Context, rows []normalize.FlowRecord) error {
	batch, err := s.conn.PrepareBatch(ctx, "INSERT INTO "+s.db+`.flow_records (timestamp, exporter_ip, agent_ip,
		agent_sub_id, sample_sequence, source_id_type, source_id_index, input_ifindex, output_ifindex,
		src_mac, dst_mac, ether_type, vlan, ip_version, src_ip, dst_ip, ip_protocol, src_port, dst_port,
		tcp_flags, sampled_packet_size, sampling_rate, estimated_bytes, estimated_packets)`)
	if err != nil {
		return err
	}
	defer batch.Abort()
	for i := range rows {
		r := &rows[i]
		if err := batch.Append(r.Timestamp, r.ExporterIP, r.AgentIP, r.AgentSubID, r.SampleSequence,
			uint8(r.SourceIDType), r.SourceIDIndex, r.InputIfIndex, r.OutputIfIndex,
			r.SrcMAC, r.DstMAC, r.EtherType, r.VLAN, r.IPVersion, r.SrcIP, r.DstIP,
			r.IPProtocol, r.SrcPort, r.DstPort, r.TCPFlags,
			r.SampledPacketSize, r.SamplingRate, r.EstimatedBytes, r.EstimatedPackets); err != nil {
			return fmt.Errorf("%w: flow: %v", errBadRow, err)
		}
	}
	return batch.Send()
}

func (s *Sink) insertCounters(ctx context.Context, rows []normalize.InterfaceCounters) error {
	batch, err := s.conn.PrepareBatch(ctx, "INSERT INTO "+s.db+`.interface_counters (timestamp, exporter_ip,
		agent_ip, agent_sub_id, ifindex, if_type, speed_bps, direction, admin_up, oper_up,
		in_octets, in_ucast, in_multicast, in_broadcast, in_discards, in_errors,
		out_octets, out_ucast, out_multicast, out_broadcast, out_discards, out_errors)`)
	if err != nil {
		return err
	}
	defer batch.Abort()
	for i := range rows {
		c := &rows[i]
		if err := batch.Append(c.Timestamp, c.ExporterIP, c.AgentIP, c.AgentSubID, c.IfIndex, c.IfType,
			c.SpeedBps, uint8(c.Direction), c.AdminUp, c.OperUp,
			c.InOctets, c.InUcast, c.InMulticast, c.InBroadcast, c.InDiscards, c.InErrors,
			c.OutOctets, c.OutUcast, c.OutMulti, c.OutBcast, c.OutDiscards, c.OutErrors); err != nil {
			return fmt.Errorf("%w: counters: %v", errBadRow, err)
		}
	}
	return batch.Send()
}
