// Package server runs the UDP listener and the decode worker pool.
package server

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"net/netip"
	"sync"
	"time"

	"sflow-analytics/collector/internal/decoder"
	"sflow-analytics/collector/internal/exporters"
	"sflow-analytics/collector/internal/metrics"
	"sflow-analytics/collector/internal/storage"
)

const maxDatagramSize = 65535

// Options configures a Server.
type Options struct {
	Address         string // UDP host:port
	Workers         int
	QueueSize       int
	ReadBufferBytes int
}

// Server receives sFlow datagrams and pushes normalized records to a Sink.
type Server struct {
	opts     Options
	log      *slog.Logger
	metrics  *metrics.Collector
	registry *exporters.Registry
	sink     storage.Sink

	errLimiter *storage.RateLimiter

	mu    sync.Mutex
	conn  *net.UDPConn
	ready chan struct{}
}

type datagram struct {
	data []byte
	src  netip.Addr
	at   time.Time
}

// New creates a Server.
func New(opts Options, log *slog.Logger, m *metrics.Collector, reg *exporters.Registry, sink storage.Sink) *Server {
	return &Server{
		opts: opts, log: log, metrics: m, registry: reg, sink: sink,
		errLimiter: storage.NewRateLimiter(10, time.Minute),
		ready:      make(chan struct{}),
	}
}

// Ready is closed once the socket is bound.
func (s *Server) Ready() <-chan struct{} { return s.ready }

// LocalAddr returns the bound UDP address (valid after Ready).
func (s *Server) LocalAddr() net.Addr {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.conn == nil {
		return nil
	}
	return s.conn.LocalAddr()
}

// Run listens until ctx is cancelled, then drains the queue and returns.
func (s *Server) Run(ctx context.Context) error {
	addr, err := net.ResolveUDPAddr("udp", s.opts.Address)
	if err != nil {
		return fmt.Errorf("resolve %s: %w", s.opts.Address, err)
	}
	conn, err := net.ListenUDP("udp", addr)
	if err != nil {
		return fmt.Errorf("listen %s: %w", s.opts.Address, err)
	}
	if s.opts.ReadBufferBytes > 0 {
		if err := conn.SetReadBuffer(s.opts.ReadBufferBytes); err != nil {
			s.log.Warn("could not set UDP read buffer", "bytes", s.opts.ReadBufferBytes, "error", err)
		}
	}
	s.mu.Lock()
	s.conn = conn
	s.mu.Unlock()
	close(s.ready)
	s.log.Info("sflow listener started", "address", conn.LocalAddr().String(), "workers", s.opts.Workers)

	queue := make(chan datagram, s.opts.QueueSize)
	var wg sync.WaitGroup
	for i := 0; i < s.opts.Workers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for d := range queue {
				s.process(ctx, d)
			}
		}()
	}

	go func() {
		<-ctx.Done()
		conn.Close()
	}()

	buf := make([]byte, maxDatagramSize)
	for {
		n, src, err := conn.ReadFromUDPAddrPort(buf)
		if err != nil {
			if ctx.Err() != nil || errors.Is(err, net.ErrClosed) {
				break
			}
			s.log.Warn("udp read error", "error", err)
			continue
		}
		now := time.Now().UTC()
		s.metrics.DatagramsReceived.Add(1)
		s.metrics.BytesReceived.Add(uint64(n))
		s.metrics.MarkPacket(now)

		data := make([]byte, n)
		copy(data, buf[:n])
		select {
		case queue <- datagram{data: data, src: src.Addr().Unmap(), at: now}:
		default:
			s.metrics.DatagramsDropped.Add(1)
		}
	}

	close(queue)
	wg.Wait()
	s.log.Info("sflow listener stopped")
	return nil
}

// process decodes one datagram. It must never let a panic escape.
func (s *Server) process(ctx context.Context, d datagram) {
	defer func() {
		if p := recover(); p != nil {
			s.metrics.MalformedDatagrams.Add(1)
			s.logError("panic while processing datagram", d.src, fmt.Errorf("%v", p))
		}
	}()
	s.Handle(ctx, d.data, d.src, d.at)
}

// Handle decodes and dispatches one datagram synchronously. It is exported
// so that replay tooling and tests can bypass the UDP socket.
func (s *Server) Handle(ctx context.Context, data []byte, src netip.Addr, at time.Time) {
	res, err := decoder.Decode(data, src, at)
	if err != nil {
		if errors.Is(err, decoder.ErrUnsupportedVersion) {
			s.metrics.UnsupportedVersion.Add(1)
		} else {
			s.metrics.MalformedDatagrams.Add(1)
		}
		s.logError("dropping datagram", src, err)
		return
	}
	if res.Truncated {
		s.metrics.TruncatedDatagrams.Add(1)
		s.metrics.MalformedDatagrams.Add(1)
	}

	m := s.metrics
	m.SamplesReceived.Add(uint64(res.Samples))
	m.FlowSamples.Add(uint64(res.FlowSamples))
	m.CounterSamples.Add(uint64(res.CounterSamples))
	m.DropSamples.Add(uint64(res.DropSamples))
	m.UnsupportedSamples.Add(uint64(res.UnsupportedSamples))
	m.UnsupportedRecords.Add(uint64(res.UnsupportedRecords))
	m.SampleErrors.Add(uint64(res.SampleErrors))
	m.FlowRecords.Add(uint64(len(res.Flows)))

	obs := exporters.Observation{
		Time:           at,
		Sequence:       res.Meta.SequenceNumber,
		Samples:        res.Samples,
		FlowSamples:    res.FlowSamples,
		CounterSamples: res.CounterSamples,
		Errors:         res.SampleErrors,
	}
	if res.Truncated {
		obs.Errors++
	}
	seen := make(map[uint32]struct{})
	addIf := func(p *uint32) {
		if p != nil {
			if _, ok := seen[*p]; !ok {
				seen[*p] = struct{}{}
				obs.IfIndexes = append(obs.IfIndexes, *p)
			}
		}
	}
	for i := range res.Flows {
		if res.Flows[i].SamplingRate != 0 {
			obs.SamplingRate = res.Flows[i].SamplingRate
		}
		addIf(res.Flows[i].InputIfIndex)
		addIf(res.Flows[i].OutputIfIndex)
	}
	for i := range res.Counters {
		c := &res.Counters[i]
		obs.IfStatus = append(obs.IfStatus, exporters.InterfaceStatus{IfIndex: c.IfIndex, SpeedBps: c.SpeedBps, OperUp: c.OperUp})
	}

	key := exporters.Key{ExporterIP: src, AgentIP: res.Meta.AgentIP, SubAgentID: res.Meta.SubAgentID}
	if s.registry.Observe(key, obs) {
		s.log.Info("new exporter discovered",
			"exporter_id", key.ID(), "exporter_ip", src.String(),
			"agent_ip", res.Meta.AgentIP.String(), "agent_sub_id", res.Meta.SubAgentID)
	}

	if len(res.Flows) > 0 {
		if err := s.sink.WriteFlows(ctx, res.Flows); err != nil {
			m.SinkErrors.Add(1)
			s.logError("sink write failed", src, err)
		}
	}
	if len(res.Counters) > 0 {
		if err := s.sink.WriteCounters(ctx, res.Counters); err != nil {
			m.SinkErrors.Add(1)
			s.logError("sink write failed", src, err)
		}
	}
}

// logError logs at most a few errors per minute; everything is still counted.
func (s *Server) logError(msg string, src netip.Addr, err error) {
	if s.errLimiter.Allow(time.Now()) {
		s.log.Warn(msg, "exporter_ip", src.String(), "error", err.Error())
	}
}
