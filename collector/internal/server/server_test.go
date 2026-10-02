package server

import (
	"context"
	"io"
	"log/slog"
	"net"
	"testing"
	"time"

	"sflow-analytics/collector/internal/exporters"
	"sflow-analytics/collector/internal/metrics"
	"sflow-analytics/collector/internal/sflowgen"
	"sflow-analytics/collector/internal/storage"
)

// TestUDPEndToEnd sends synthetic, garbage and unsupported datagrams over a
// real UDP socket and checks records, counters and exporter discovery.
func TestUDPEndToEnd(t *testing.T) {
	m := metrics.New()
	reg := exporters.NewRegistry(time.Minute)
	sink := &storage.MemorySink{}
	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	srv := New(Options{Address: "127.0.0.1:0", Workers: 2, QueueSize: 128}, log, m, reg, sink)

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- srv.Run(ctx) }()
	select {
	case <-srv.Ready():
	case err := <-done:
		t.Fatalf("server exited: %v", err)
	}

	conn, err := net.Dial("udp", srv.LocalAddr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()

	var sent int
	for _, fx := range sflowgen.Fixtures() {
		if _, err := conn.Write(fx.Data); err != nil {
			t.Fatal(err)
		}
		sent++
	}
	for _, g := range [][]byte{{1, 2, 3}, make([]byte, 500)} {
		conn.Write(g)
		sent++
	}

	deadline := time.Now().Add(3 * time.Second)
	for m.DatagramsReceived.Load() < uint64(sent) && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	cancel()
	if err := <-done; err != nil {
		t.Fatalf("Run: %v", err)
	}

	// Fixture flows: 1+1+1+3+1+0+1+1(truncated keeps 1) = 9 flow records.
	flows, counters := sink.Len()
	s := m.Snapshot(time.Now())
	t.Logf("snapshot: %+v", s)
	if s.DatagramsReceived != uint64(sent) {
		t.Errorf("datagrams = %d, want %d", s.DatagramsReceived, sent)
	}
	if flows != 9 || counters != 2 {
		t.Errorf("sink flows=%d counters=%d, want 9 and 2", flows, counters)
	}
	// v4 fixture + zero-filled datagram (version 0).
	if s.UnsupportedVersion != 2 {
		t.Errorf("unsupported version = %d", s.UnsupportedVersion)
	}
	// truncated fixture + 3-byte garbage.
	if s.MalformedDatagrams != 2 || s.TruncatedDatagrams != 1 {
		t.Errorf("malformed=%d truncated=%d", s.MalformedDatagrams, s.TruncatedDatagrams)
	}
	if s.UnsupportedSamples != 1 || s.UnsupportedRecords != 1 {
		t.Errorf("unsupported samples=%d records=%d", s.UnsupportedSamples, s.UnsupportedRecords)
	}
	if total, _ := reg.Counts(); total != 1 {
		t.Errorf("exporters = %d, want 1 (all fixtures share agent 10.0.0.10)", total)
	}
}
