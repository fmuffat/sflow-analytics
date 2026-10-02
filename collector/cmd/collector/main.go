// Command collector receives sFlow v5 datagrams, decodes and normalizes them,
// tracks exporters and counters, and hands records to a storage sink.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"sflow-analytics/collector/internal/config"
	"sflow-analytics/collector/internal/exporters"
	"sflow-analytics/collector/internal/httpapi"
	"sflow-analytics/collector/internal/metrics"
	"sflow-analytics/collector/internal/server"
	"sflow-analytics/collector/internal/storage"
)

// version is set at build time with -ldflags "-X main.version=...".
var version = "dev"

func main() {
	healthcheck := flag.Bool("healthcheck", false, "query the local /healthz endpoint and exit (for container health checks)")
	flag.Parse()

	cfg, err := config.Load()
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	if *healthcheck {
		os.Exit(runHealthcheck(cfg.HTTPAddress))
	}

	log := newLogger(cfg)
	if err := run(cfg, log); err != nil {
		log.Error("collector stopped with error", "error", err)
		os.Exit(1)
	}
}

func newLogger(cfg config.Config) *slog.Logger {
	opts := &slog.HandlerOptions{Level: cfg.LogLevel}
	var h slog.Handler = slog.NewJSONHandler(os.Stdout, opts)
	if cfg.LogFormat == "text" {
		h = slog.NewTextHandler(os.Stdout, opts)
	}
	return slog.New(h).With("service", "collector")
}

func run(cfg config.Config, log *slog.Logger) error {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	m := metrics.New()
	reg := exporters.NewRegistry(cfg.InactiveTimeout)

	var sinks storage.Fanout
	var logSink *storage.LogSink
	if cfg.LogFlows {
		logSink = storage.NewLogSink(log, cfg.LogFlowsPerSecond)
		sinks = append(sinks, logSink)
	}

	var storageStats func() any
	var db *database
	if cfg.ClickHouseEnabled {
		var err error
		db, err = openDatabase(cfg, log, m)
		if err != nil {
			return err
		}
		sinks = append(sinks, db.sink)
		storageStats = func() any { return db.status() }
	} else {
		log.Warn("clickhouse disabled: flows are not stored (CLICKHOUSE_ENABLED=false)")
	}
	var sink storage.Sink = storage.DiscardSink{}
	if len(sinks) > 0 {
		sink = sinks
	}

	srv := server.New(server.Options{
		Address:         cfg.UDPAddress(),
		Workers:         cfg.Workers,
		QueueSize:       cfg.QueueSize,
		ReadBufferBytes: cfg.ReadBufferBytes,
	}, log, m, reg, sink)

	httpSrv := &http.Server{
		Addr:              cfg.HTTPAddress,
		Handler:           httpapi.Handler(httpapi.Info{Version: version, ListenAddress: cfg.UDPAddress() + "/udp"}, m, reg, storageStats),
		ReadHeaderTimeout: 5 * time.Second,
	}
	go func() {
		log.Info("status http endpoint started", "address", cfg.HTTPAddress)
		if err := httpSrv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Error("status http endpoint failed", "error", err)
		}
	}()

	go housekeeping(ctx, cfg, log, m, reg, logSink)
	if db != nil {
		go db.start(ctx, reg)
	}

	log.Info("collector starting", "version", version, "listen", cfg.UDPAddress(),
		"inactive_timeout", cfg.InactiveTimeout.String(), "log_flows", cfg.LogFlows)
	err := srv.Run(ctx)

	shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_ = httpSrv.Shutdown(shutdownCtx)
	if db != nil {
		db.saveInventory(reg)
	}
	if cerr := sink.Close(); cerr != nil {
		log.Warn("sink close", "error", cerr)
	}
	return err
}

// housekeeping refreshes rates and exporter status every 5s and logs a
// summary every StatsInterval.
func housekeeping(ctx context.Context, cfg config.Config, log *slog.Logger, m *metrics.Collector, reg *exporters.Registry, logSink *storage.LogSink) {
	tick := time.NewTicker(5 * time.Second)
	defer tick.Stop()
	lastSummary := time.Now()
	for {
		select {
		case <-ctx.Done():
			return
		case now := <-tick.C:
			m.Tick(now)
			for _, t := range reg.Tick(now) {
				log.Info("exporter status changed", "exporter_id", t.Exporter.ID,
					"exporter_ip", t.Exporter.ExporterIP.String(), "from", t.From, "to", t.To,
					"last_seen", t.Exporter.LastSeen)
			}
			if cfg.StatsInterval > 0 && now.Sub(lastSummary) >= cfg.StatsInterval {
				lastSummary = now
				s := m.Snapshot(now)
				total, active := reg.Counts()
				attrs := []any{
					"datagrams", s.DatagramsReceived, "datagrams_per_sec", round(s.DatagramsPerSec),
					"samples", s.SamplesReceived, "samples_per_sec", round(s.SamplesPerSec),
					"flow_samples", s.FlowSamples, "counter_samples", s.CounterSamples,
					"malformed", s.MalformedDatagrams, "unsupported_version", s.UnsupportedVersion,
					"unsupported_records", s.UnsupportedRecords, "dropped_queue_full", s.DatagramsDropped,
					"db_rows_inserted", s.RowsInserted, "db_rows_pending", s.PendingRows,
					"db_rows_dropped", s.RowsDropped, "db_insert_failures", s.SinkErrors,
					"exporters_total", total, "exporters_active", active,
				}
				if logSink != nil {
					attrs = append(attrs, "flow_logs_suppressed", logSink.Suppressed())
				}
				log.Info("collector stats", attrs...)
			}
		}
	}
}

func round(f float64) float64 { return float64(int64(f*10+0.5)) / 10 }

func runHealthcheck(addr string) int {
	host := addr
	if strings.HasPrefix(host, ":") {
		host = "127.0.0.1" + host
	}
	client := &http.Client{Timeout: 3 * time.Second}
	resp, err := client.Get("http://" + host + "/healthz")
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return 1
	}
	return 0
}
