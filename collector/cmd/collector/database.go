package main

import (
	"context"
	"log/slog"
	"sync/atomic"
	"time"

	"github.com/ClickHouse/clickhouse-go/v2/lib/driver"

	"sflow-analytics/collector/internal/config"
	"sflow-analytics/collector/internal/exporters"
	"sflow-analytics/collector/internal/metrics"
	"sflow-analytics/collector/internal/storage/clickhouse"
)

// database ties together the ClickHouse connection, sink, disk guard and
// inventory persistence. Flow collection never waits for it.
type database struct {
	cfg   config.Config
	chCfg clickhouse.Config
	log   *slog.Logger
	conn  driver.Conn
	sink  *clickhouse.Sink
	guard *clickhouse.Guard
	ready atomic.Bool
}

func openDatabase(cfg config.Config, log *slog.Logger, m *metrics.Collector) (*database, error) {
	chCfg := clickhouse.Config{
		Addr: cfg.ClickHouseAddr(), Database: cfg.ClickHouseDatabase,
		Username: cfg.ClickHouseUser, Password: cfg.ClickHousePassword, RetentionDays: cfg.RetentionDays,
	}
	conn, err := clickhouse.Open(chCfg)
	if err != nil {
		return nil, err
	}
	d := &database{cfg: cfg, chCfg: chCfg, log: log, conn: conn}
	d.sink = clickhouse.NewSink(conn, cfg.ClickHouseDatabase, clickhouse.SinkOptions{
		BatchSize: cfg.BatchSize, FlushInterval: cfg.FlushInterval,
		QueueRows: cfg.SinkQueueRows, MaxPendingRows: cfg.MaxPendingRows,
	}, log, m)
	d.guard = clickhouse.NewGuard(conn, cfg.ClickHouseDatabase, cfg.RetentionDays, cfg.DiskMaxUsage, log)
	d.sink.SetSuspendCheck(d.guard.Critical)
	return d, nil
}

// start waits for ClickHouse, restores the inventory, then enables inserts,
// the disk guard and periodic inventory saves.
func (d *database) start(ctx context.Context, reg *exporters.Registry) {
	if err := clickhouse.WaitReady(ctx, d.conn, d.chCfg, d.log); err != nil {
		return
	}
	lctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	list, err := clickhouse.LoadInventory(lctx, d.conn, d.cfg.ClickHouseDatabase)
	cancel()
	if err != nil {
		d.log.Warn("could not load exporter inventory", "error", err.Error())
	} else if n := reg.Restore(list, time.Now()); n > 0 {
		d.log.Info("exporter inventory restored", "exporters", n)
	}

	// Measure disk before accepting inserts, so a full disk is caught first.
	d.guard.Check(ctx)
	d.sink.SetReady()
	d.ready.Store(true)
	go d.guard.Run(ctx, time.Minute)

	t := time.NewTicker(d.cfg.InventoryInterval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			d.saveInventory(reg)
		}
	}
}

func (d *database) saveInventory(reg *exporters.Registry) {
	if !d.ready.Load() {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := clickhouse.SaveInventory(ctx, d.conn, d.cfg.ClickHouseDatabase, reg.Snapshot(true), time.Now().UTC()); err != nil {
		d.log.Warn("could not save exporter inventory", "error", err.Error())
	}
}

type dbStatus struct {
	Enabled  bool   `json:"enabled"`
	Ready    bool   `json:"ready"`
	Address  string `json:"address"`
	Database string `json:"database"`
	clickhouse.StorageStats
}

func (d *database) status() any {
	return dbStatus{
		Enabled: true, Ready: d.ready.Load(), Address: d.chCfg.Addr, Database: d.chCfg.Database,
		StorageStats: d.guard.Stats(),
	}
}
