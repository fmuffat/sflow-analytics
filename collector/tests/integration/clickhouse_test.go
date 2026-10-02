//go:build integration

// Package integration runs the collector against a real ClickHouse server.
//
//	scripts/integration-test.sh
//
// or, with ClickHouse reachable on 127.0.0.1:9000:
//
//	SFLOW_IT_CLICKHOUSE_PASSWORD=... go test -tags integration -count=1 ./tests/integration
package integration

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/netip"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/ClickHouse/clickhouse-go/v2/lib/driver"

	"sflow-analytics/collector/internal/exporters"
	"sflow-analytics/collector/internal/metrics"
	"sflow-analytics/collector/internal/normalize"
	"sflow-analytics/collector/internal/server"
	"sflow-analytics/collector/internal/sflowgen"
	"sflow-analytics/collector/internal/storage/clickhouse"
)

var quiet = slog.New(slog.NewTextHandler(io.Discard, nil))

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func setup(t *testing.T, retention int) (driver.Conn, clickhouse.Config) {
	t.Helper()
	cfg := clickhouse.Config{
		Addr:          env("SFLOW_IT_CLICKHOUSE_ADDR", "127.0.0.1:9000"),
		Username:      env("SFLOW_IT_CLICKHOUSE_USER", "sflow"),
		Password:      os.Getenv("SFLOW_IT_CLICKHOUSE_PASSWORD"),
		Database:      fmt.Sprintf("sflow_it_%d", time.Now().UnixNano()),
		RetentionDays: retention,
	}
	conn, err := clickhouse.Open(cfg)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if err := conn.Ping(ctx); err != nil {
		t.Fatalf("clickhouse not reachable at %s: %v", cfg.Addr, err)
	}
	t.Cleanup(func() {
		_ = conn.Exec(context.Background(), "DROP DATABASE IF EXISTS "+cfg.Database)
		conn.Close()
	})
	return conn, cfg
}

func waitFor(t *testing.T, what string, timeout time.Duration, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for !cond() {
		if time.Now().After(deadline) {
			t.Fatalf("timeout waiting for %s", what)
		}
		time.Sleep(50 * time.Millisecond)
	}
}

func ttlOf(t *testing.T, conn driver.Conn, db, table string) string {
	var q string
	if err := conn.QueryRow(context.Background(), "SELECT create_table_query FROM system.tables WHERE database = ? AND name = ?", db, table).Scan(&q); err != nil {
		t.Fatal(err)
	}
	i := strings.Index(q, "TTL")
	return q[i:]
}

func TestMigrationsAreIdempotentAndApplyRetention(t *testing.T) {
	conn, cfg := setup(t, 30)
	ctx := context.Background()
	if err := clickhouse.Migrate(ctx, conn, cfg, quiet); err != nil {
		t.Fatal(err)
	}
	if ttl := ttlOf(t, conn, cfg.Database, "flow_records"); !strings.Contains(ttl, "toIntervalDay(30)") {
		t.Errorf("ttl = %s", ttl)
	}
	cfg.RetentionDays = 7
	if err := clickhouse.Migrate(ctx, conn, cfg, quiet); err != nil {
		t.Fatalf("second migration: %v", err)
	}
	for _, table := range []string{"flow_records", "interface_counters"} {
		if ttl := ttlOf(t, conn, cfg.Database, table); !strings.Contains(ttl, "toIntervalDay(7)") {
			t.Errorf("%s ttl after change = %s", table, ttl)
		}
	}
}

// TestEndToEnd: UDP datagram -> collector -> ClickHouse row (spec section 34).
func TestEndToEnd(t *testing.T) {
	conn, cfg := setup(t, 30)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if err := clickhouse.Migrate(ctx, conn, cfg, quiet); err != nil {
		t.Fatal(err)
	}

	m := metrics.New()
	reg := exporters.NewRegistry(time.Minute)
	sink := clickhouse.NewSink(conn, cfg.Database, clickhouse.SinkOptions{BatchSize: 100, FlushInterval: 100 * time.Millisecond, QueueRows: 1000, MaxPendingRows: 1000}, quiet, m)
	sink.SetReady()
	srv := server.New(server.Options{Address: "127.0.0.1:0", Workers: 2, QueueSize: 100}, quiet, m, reg, sink)
	done := make(chan error, 1)
	go func() { done <- srv.Run(ctx) }()
	<-srv.Ready()

	udp, err := net.Dial("udp", srv.LocalAddr().String())
	if err != nil {
		t.Fatal(err)
	}
	for _, fx := range sflowgen.Fixtures() {
		udp.Write(fx.Data)
	}
	udp.Close()

	// 9 flow records + 2 interface counter records in the fixtures.
	waitFor(t, "rows inserted", 10*time.Second, func() bool { return m.RowsInserted.Load() == 11 })
	cancel()
	<-done
	if err := sink.Close(); err != nil {
		t.Fatal(err)
	}

	var count uint64
	if err := conn.QueryRow(context.Background(), "SELECT count() FROM "+cfg.Database+".flow_records").Scan(&count); err != nil || count != 9 {
		t.Fatalf("flow_records count = %d, err %v", count, err)
	}

	var (
		src, exp    netip.Addr
		dport, vlan *uint16
		in, out     *uint32
		mac         *string
		estBytes    uint64
		rate        uint32
		ts          time.Time
	)
	row := conn.QueryRow(context.Background(), `SELECT src_ip, exporter_ip, dst_port, vlan, input_ifindex, output_ifindex,
		src_mac, estimated_bytes, sampling_rate, timestamp
		FROM `+cfg.Database+`.flow_records WHERE dst_port = 443 AND vlan = 120 AND ip_version = 4 AND sample_sequence = 1`)
	if err := row.Scan(&src, &exp, &dport, &vlan, &in, &out, &mac, &estBytes, &rate, &ts); err != nil {
		t.Fatal(err)
	}
	if src.Unmap().String() != "10.20.30.42" || exp.Unmap().String() != "127.0.0.1" || *in != 24 || *out != 48 ||
		*mac != "00:11:22:33:44:55" || estBytes != 1550336 || rate != 1024 || time.Since(ts) > time.Minute {
		t.Errorf("row: src=%s exp=%s in=%d out=%d mac=%s est=%d rate=%d ts=%s", src, exp, *in, *out, *mac, estBytes, rate, ts)
	}

	var v6 uint64
	conn.QueryRow(context.Background(), "SELECT count() FROM "+cfg.Database+".flow_records WHERE src_ip = toIPv6('2001:db8:10::42')").Scan(&v6)
	if v6 != 2 { // ipv6_udp + the IPv6 sample inside multi_sample
		t.Errorf("ipv6 rows = %d, want 2", v6)
	}
	var cnt uint64
	conn.QueryRow(context.Background(), "SELECT count() FROM "+cfg.Database+".interface_counters WHERE ifindex = 48 AND speed_bps = 10000000000 AND oper_up").Scan(&cnt)
	if cnt != 1 {
		t.Errorf("interface counters rows = %d, want 1", cnt)
	}

	// Inventory survives a restart.
	if err := clickhouse.SaveInventory(context.Background(), conn, cfg.Database, reg.Snapshot(true), time.Now().UTC()); err != nil {
		t.Fatal(err)
	}
	list, err := clickhouse.LoadInventory(context.Background(), conn, cfg.Database)
	if err != nil {
		t.Fatal(err)
	}
	fresh := exporters.NewRegistry(time.Minute)
	if n := fresh.Restore(list, time.Now()); n != 1 {
		t.Fatalf("restored %d exporters, want 1", n)
	}
	e, ok := fresh.Get("127.0.0.1/10.0.0.10/0")
	if !ok || len(e.Interfaces) < 4 {
		t.Errorf("restored exporter = %+v", e)
	}

	// Storage statistics.
	g := clickhouse.NewGuard(conn, cfg.Database, 30, 85, quiet)
	g.Check(context.Background())
	st := g.Stats()
	if !st.Available || st.FlowRecordsRows != 9 || st.OldestFlowRecord == nil || st.DiskTotalBytes == 0 || st.EstimatedRetentionDays != nil /* < 1 h of data */ {
		t.Errorf("storage stats = %+v", st)
	}
}

// TestSinkRetriesWhileDatabaseUnavailable: rows are kept and inserted once
// the schema exists, and failures are counted.
func TestSinkRetriesWhileDatabaseUnavailable(t *testing.T) {
	conn, cfg := setup(t, 30)
	m := metrics.New()
	sink := clickhouse.NewSink(conn, cfg.Database, clickhouse.SinkOptions{BatchSize: 5, FlushInterval: 50 * time.Millisecond, QueueRows: 100, MaxPendingRows: 100}, quiet, m)
	sink.SetReady()

	src := netip.MustParseAddr("10.1.1.1")
	flows := make([]normalize.FlowRecord, 12)
	for i := range flows {
		flows[i] = normalize.FlowRecord{Timestamp: time.Now().UTC(), ExporterIP: src, AgentIP: src, SrcIP: &src, SamplingRate: 1, SampledPacketSize: 100, EstimatedBytes: 100, EstimatedPackets: 1}
	}
	sink.WriteFlows(context.Background(), flows)
	waitFor(t, "insert failures", 5*time.Second, func() bool { return m.SinkErrors.Load() > 0 })
	if m.RowsInserted.Load() != 0 || m.PendingRows.Load() != 12 {
		t.Fatalf("inserted=%d pending=%d", m.RowsInserted.Load(), m.PendingRows.Load())
	}

	if err := clickhouse.Migrate(context.Background(), conn, cfg, quiet); err != nil {
		t.Fatal(err)
	}
	waitFor(t, "retried inserts", 40*time.Second, func() bool { return m.RowsInserted.Load() == 12 })
	if m.PendingRows.Load() != 0 || m.RowsDropped.Load() != 0 {
		t.Errorf("pending=%d dropped=%d", m.PendingRows.Load(), m.RowsDropped.Load())
	}
	sink.Close()
}

// TestSinkBoundsMemory: with the database down, the oldest batches are
// dropped once MaxPendingRows is exceeded.
func TestSinkBoundsMemory(t *testing.T) {
	conn, cfg := setup(t, 30) // database never created
	m := metrics.New()
	sink := clickhouse.NewSink(conn, cfg.Database, clickhouse.SinkOptions{BatchSize: 10, FlushInterval: 20 * time.Millisecond, QueueRows: 1000, MaxPendingRows: 30}, quiet, m)
	sink.SetReady()
	src := netip.MustParseAddr("10.1.1.1")
	for i := 0; i < 10; i++ {
		batch := make([]normalize.FlowRecord, 10)
		for j := range batch {
			batch[j] = normalize.FlowRecord{Timestamp: time.Now().UTC(), ExporterIP: src, AgentIP: src}
		}
		sink.WriteFlows(context.Background(), batch)
		time.Sleep(30 * time.Millisecond)
	}
	waitFor(t, "bounded pending", 5*time.Second, func() bool { return m.PendingRows.Load() <= 30 && m.RowsDropped.Load() >= 70 })
	sink.Close()
}
