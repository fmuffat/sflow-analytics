// Package config loads collector settings from environment variables.
package config

import (
	"fmt"
	"log/slog"
	"os"
	"runtime"
	"strconv"
	"strings"
	"time"
)

// Config holds all collector settings.
type Config struct {
	ListenAddress   string        // SFLOW_LISTEN_ADDRESS
	Port            int           // SFLOW_PORT
	Workers         int           // SFLOW_WORKERS
	QueueSize       int           // SFLOW_QUEUE_SIZE
	ReadBufferBytes int           // SFLOW_READ_BUFFER_BYTES
	InactiveTimeout time.Duration // EXPORTER_INACTIVE_TIMEOUT

	HTTPAddress   string        // COLLECTOR_HTTP_ADDRESS
	StatsInterval time.Duration // COLLECTOR_STATS_LOG_INTERVAL

	LogLevel          slog.Level // LOG_LEVEL
	LogFormat         string     // LOG_FORMAT: json | text
	LogFlows          bool       // SFLOW_LOG_FLOWS
	LogFlowsPerSecond int        // SFLOW_LOG_FLOWS_PER_SECOND

	ClickHouseEnabled  bool          // CLICKHOUSE_ENABLED
	ClickHouseHost     string        // CLICKHOUSE_HOST
	ClickHousePort     int           // CLICKHOUSE_PORT (native protocol)
	ClickHouseDatabase string        // CLICKHOUSE_DATABASE
	ClickHouseUser     string        // CLICKHOUSE_USER
	ClickHousePassword string        // CLICKHOUSE_PASSWORD
	RetentionDays      int           // RETENTION_DAYS
	BatchSize          int           // CLICKHOUSE_BATCH_SIZE
	FlushInterval      time.Duration // CLICKHOUSE_FLUSH_INTERVAL
	SinkQueueRows      int           // CLICKHOUSE_QUEUE_ROWS
	MaxPendingRows     int           // CLICKHOUSE_MAX_PENDING_ROWS
	DiskMaxUsage       float64       // DISK_MAX_USAGE_PERCENT
	InventoryInterval  time.Duration // INVENTORY_SAVE_INTERVAL
}

// ClickHouseAddr is the native protocol address.
func (c Config) ClickHouseAddr() string {
	return fmt.Sprintf("%s:%d", c.ClickHouseHost, c.ClickHousePort)
}

// UDPAddress is the listen address in host:port form.
func (c Config) UDPAddress() string {
	return fmt.Sprintf("%s:%d", c.ListenAddress, c.Port)
}

// Load reads the environment, applying defaults and validating values.
func Load() (Config, error) {
	return load(os.Getenv)
}

func load(getenv func(string) string) (Config, error) {
	p := parser{getenv: getenv}
	c := Config{
		ListenAddress:     p.str("SFLOW_LISTEN_ADDRESS", "0.0.0.0"),
		Port:              p.int("SFLOW_PORT", 6343),
		Workers:           p.int("SFLOW_WORKERS", runtime.NumCPU()),
		QueueSize:         p.int("SFLOW_QUEUE_SIZE", 8192),
		ReadBufferBytes:   p.int("SFLOW_READ_BUFFER_BYTES", 8<<20),
		InactiveTimeout:   p.dur("EXPORTER_INACTIVE_TIMEOUT", 5*time.Minute),
		HTTPAddress:       p.str("COLLECTOR_HTTP_ADDRESS", ":8081"),
		StatsInterval:     p.dur("COLLECTOR_STATS_LOG_INTERVAL", time.Minute),
		LogFormat:         strings.ToLower(p.str("LOG_FORMAT", "json")),
		LogFlows:          p.bool("SFLOW_LOG_FLOWS", false),
		LogFlowsPerSecond: p.int("SFLOW_LOG_FLOWS_PER_SECOND", 20),

		ClickHouseEnabled:  p.bool("CLICKHOUSE_ENABLED", true),
		ClickHouseHost:     p.str("CLICKHOUSE_HOST", "clickhouse"),
		ClickHousePort:     p.int("CLICKHOUSE_PORT", 9000),
		ClickHouseDatabase: p.str("CLICKHOUSE_DATABASE", "sflow"),
		ClickHouseUser:     p.str("CLICKHOUSE_USER", "sflow"),
		ClickHousePassword: p.getenv("CLICKHOUSE_PASSWORD"),
		RetentionDays:      p.int("RETENTION_DAYS", 90),
		BatchSize:          p.int("CLICKHOUSE_BATCH_SIZE", 5000),
		FlushInterval:      p.dur("CLICKHOUSE_FLUSH_INTERVAL", time.Second),
		SinkQueueRows:      p.int("CLICKHOUSE_QUEUE_ROWS", 200_000),
		MaxPendingRows:     p.int("CLICKHOUSE_MAX_PENDING_ROWS", 500_000),
		DiskMaxUsage:       p.float("DISK_MAX_USAGE_PERCENT", 85),
		InventoryInterval:  p.dur("INVENTORY_SAVE_INTERVAL", 30*time.Second),
	}
	if err := c.LogLevel.UnmarshalText([]byte(p.str("LOG_LEVEL", "info"))); err != nil {
		p.errs = append(p.errs, fmt.Sprintf("LOG_LEVEL: %v", err))
	}

	if c.Port < 1 || c.Port > 65535 {
		p.errs = append(p.errs, "SFLOW_PORT must be 1-65535")
	}
	if c.Workers < 1 {
		p.errs = append(p.errs, "SFLOW_WORKERS must be >= 1")
	}
	if c.QueueSize < 1 {
		p.errs = append(p.errs, "SFLOW_QUEUE_SIZE must be >= 1")
	}
	if c.InactiveTimeout <= 0 {
		p.errs = append(p.errs, "EXPORTER_INACTIVE_TIMEOUT must be > 0")
	}
	if c.StatsInterval < 0 {
		p.errs = append(p.errs, "COLLECTOR_STATS_LOG_INTERVAL must be >= 0")
	}
	if c.LogFormat != "json" && c.LogFormat != "text" {
		p.errs = append(p.errs, "LOG_FORMAT must be json or text")
	}
	if c.RetentionDays < 1 || c.RetentionDays > 3650 {
		p.errs = append(p.errs, "RETENTION_DAYS must be 1-3650")
	}
	if c.BatchSize < 1 || c.SinkQueueRows < 1 || c.MaxPendingRows < 1 {
		p.errs = append(p.errs, "CLICKHOUSE_BATCH_SIZE, CLICKHOUSE_QUEUE_ROWS and CLICKHOUSE_MAX_PENDING_ROWS must be >= 1")
	}
	if c.FlushInterval <= 0 || c.InventoryInterval <= 0 {
		p.errs = append(p.errs, "CLICKHOUSE_FLUSH_INTERVAL and INVENTORY_SAVE_INTERVAL must be > 0")
	}
	if c.DiskMaxUsage < 10 || c.DiskMaxUsage > 95 {
		p.errs = append(p.errs, "DISK_MAX_USAGE_PERCENT must be 10-95")
	}
	if len(p.errs) > 0 {
		return c, fmt.Errorf("invalid configuration: %s", strings.Join(p.errs, "; "))
	}
	return c, nil
}

type parser struct {
	getenv func(string) string
	errs   []string
}

func (p *parser) str(key, def string) string {
	if v := strings.TrimSpace(p.getenv(key)); v != "" {
		return v
	}
	return def
}

func (p *parser) int(key string, def int) int {
	v := strings.TrimSpace(p.getenv(key))
	if v == "" {
		return def
	}
	n, err := strconv.Atoi(v)
	if err != nil {
		p.errs = append(p.errs, fmt.Sprintf("%s: %q is not an integer", key, v))
		return def
	}
	return n
}

func (p *parser) float(key string, def float64) float64 {
	v := strings.TrimSpace(p.getenv(key))
	if v == "" {
		return def
	}
	f, err := strconv.ParseFloat(v, 64)
	if err != nil {
		p.errs = append(p.errs, fmt.Sprintf("%s: %q is not a number", key, v))
		return def
	}
	return f
}

func (p *parser) bool(key string, def bool) bool {
	v := strings.TrimSpace(p.getenv(key))
	if v == "" {
		return def
	}
	b, err := strconv.ParseBool(v)
	if err != nil {
		p.errs = append(p.errs, fmt.Sprintf("%s: %q is not a boolean", key, v))
		return def
	}
	return b
}

func (p *parser) dur(key string, def time.Duration) time.Duration {
	v := strings.TrimSpace(p.getenv(key))
	if v == "" {
		return def
	}
	d, err := time.ParseDuration(v)
	if err != nil {
		p.errs = append(p.errs, fmt.Sprintf("%s: %q is not a duration (e.g. 30s, 5m)", key, v))
		return def
	}
	return d
}
