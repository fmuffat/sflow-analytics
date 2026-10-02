package config

import (
	"strings"
	"testing"
	"time"
)

func env(m map[string]string) func(string) string {
	return func(k string) string { return m[k] }
}

func TestDefaults(t *testing.T) {
	c, err := load(env(nil))
	if err != nil {
		t.Fatal(err)
	}
	if c.UDPAddress() != "0.0.0.0:6343" || c.InactiveTimeout != 5*time.Minute || c.LogFormat != "json" || c.LogFlows ||
		c.RetentionDays != 90 || c.ClickHouseAddr() != "clickhouse:9000" || !c.ClickHouseEnabled || c.DiskMaxUsage != 85 {
		t.Errorf("defaults = %+v", c)
	}
}

func TestOverrides(t *testing.T) {
	c, err := load(env(map[string]string{
		"SFLOW_LISTEN_ADDRESS": "127.0.0.1", "SFLOW_PORT": "16343",
		"EXPORTER_INACTIVE_TIMEOUT": "90s", "LOG_LEVEL": "debug", "SFLOW_LOG_FLOWS": "false", "RETENTION_DAYS": "7",
	}))
	if err != nil {
		t.Fatal(err)
	}
	if c.UDPAddress() != "127.0.0.1:16343" || c.InactiveTimeout != 90*time.Second || c.LogFlows || c.LogLevel.String() != "DEBUG" || c.RetentionDays != 7 {
		t.Errorf("config = %+v", c)
	}
}

func TestInvalidValuesAreReportedTogether(t *testing.T) {
	_, err := load(env(map[string]string{"SFLOW_PORT": "99999", "SFLOW_WORKERS": "x", "EXPORTER_INACTIVE_TIMEOUT": "5", "RETENTION_DAYS": "0"}))
	if err == nil {
		t.Fatal("expected error")
	}
	for _, want := range []string{"SFLOW_PORT", "SFLOW_WORKERS", "EXPORTER_INACTIVE_TIMEOUT", "RETENTION_DAYS"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("error %q does not mention %s", err, want)
		}
	}
}
