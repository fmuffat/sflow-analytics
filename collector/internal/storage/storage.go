// Package storage defines where normalized records go. Phase 1 ships a
// structured-log sink; the ClickHouse sink is added in Phase 2.
package storage

import (
	"context"
	"errors"
	"log/slog"
	"sync"
	"sync/atomic"
	"time"

	"sflow-analytics/collector/internal/normalize"
)

// Sink receives normalized records. Implementations must be safe for
// concurrent use and must not block the decode path for long.
type Sink interface {
	WriteFlows(ctx context.Context, flows []normalize.FlowRecord) error
	WriteCounters(ctx context.Context, counters []normalize.InterfaceCounters) error
	Close() error
}

// LogSink writes flow records as structured log lines, rate-limited so that
// a busy exporter cannot flood the logs.
type LogSink struct {
	log        *slog.Logger
	limiter    *RateLimiter
	suppressed atomic.Uint64
}

// NewLogSink logs at most perSecond flow records per second (0 disables flow logging).
func NewLogSink(log *slog.Logger, perSecond int) *LogSink {
	return &LogSink{log: log, limiter: NewRateLimiter(perSecond, time.Second)}
}

// WriteFlows implements Sink.
func (s *LogSink) WriteFlows(ctx context.Context, flows []normalize.FlowRecord) error {
	for i := range flows {
		if !s.limiter.Allow(time.Now()) {
			s.suppressed.Add(uint64(len(flows) - i))
			return nil
		}
		f := &flows[i]
		s.log.LogAttrs(ctx, slog.LevelInfo, "flow",
			slog.String("exporter_ip", f.ExporterIP.String()),
			slog.String("agent_ip", f.AgentIP.String()),
			slog.Any("record", f),
		)
	}
	return nil
}

// WriteCounters implements Sink. Counter records are logged at debug level.
func (s *LogSink) WriteCounters(ctx context.Context, counters []normalize.InterfaceCounters) error {
	if !s.log.Enabled(ctx, slog.LevelDebug) {
		return nil
	}
	for i := range counters {
		c := &counters[i]
		s.log.LogAttrs(ctx, slog.LevelDebug, "interface counters",
			slog.String("exporter_ip", c.ExporterIP.String()),
			slog.Any("record", c),
		)
	}
	return nil
}

// Suppressed returns how many flow records were not logged due to rate limiting.
func (s *LogSink) Suppressed() uint64 { return s.suppressed.Load() }

// Close implements Sink.
func (s *LogSink) Close() error { return nil }

// Fanout sends records to several sinks.
type Fanout []Sink

func (f Fanout) WriteFlows(ctx context.Context, flows []normalize.FlowRecord) error {
	var errs []error
	for _, s := range f {
		errs = append(errs, s.WriteFlows(ctx, flows))
	}
	return errors.Join(errs...)
}

func (f Fanout) WriteCounters(ctx context.Context, c []normalize.InterfaceCounters) error {
	var errs []error
	for _, s := range f {
		errs = append(errs, s.WriteCounters(ctx, c))
	}
	return errors.Join(errs...)
}

func (f Fanout) Close() error {
	var errs []error
	for _, s := range f {
		errs = append(errs, s.Close())
	}
	return errors.Join(errs...)
}

// DiscardSink drops everything (flow logging disabled).
type DiscardSink struct{}

func (DiscardSink) WriteFlows(context.Context, []normalize.FlowRecord) error { return nil }
func (DiscardSink) WriteCounters(context.Context, []normalize.InterfaceCounters) error {
	return nil
}
func (DiscardSink) Close() error { return nil }

// MemorySink keeps everything in memory. Intended for tests.
type MemorySink struct {
	mu       sync.Mutex
	Flows    []normalize.FlowRecord
	Counters []normalize.InterfaceCounters
}

func (m *MemorySink) WriteFlows(_ context.Context, f []normalize.FlowRecord) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.Flows = append(m.Flows, f...)
	return nil
}

func (m *MemorySink) WriteCounters(_ context.Context, c []normalize.InterfaceCounters) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.Counters = append(m.Counters, c...)
	return nil
}

func (m *MemorySink) Close() error { return nil }

// Len returns the number of flows and counters stored.
func (m *MemorySink) Len() (flows, counters int) {
	m.mu.Lock()
	defer m.mu.Unlock()
	return len(m.Flows), len(m.Counters)
}

// RateLimiter allows up to n events per window (fixed window).
type RateLimiter struct {
	mu     sync.Mutex
	n      int
	window time.Duration
	start  time.Time
	used   int
}

// NewRateLimiter creates a limiter; n <= 0 never allows.
func NewRateLimiter(n int, window time.Duration) *RateLimiter {
	return &RateLimiter{n: n, window: window}
}

// Allow reports whether one more event is allowed at time now.
func (r *RateLimiter) Allow(now time.Time) bool {
	if r.n <= 0 {
		return false
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if now.Sub(r.start) >= r.window {
		r.start, r.used = now, 0
	}
	if r.used >= r.n {
		return false
	}
	r.used++
	return true
}
