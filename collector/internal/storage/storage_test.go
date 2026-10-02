package storage

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"net/netip"
	"strings"
	"testing"
	"time"

	"sflow-analytics/collector/internal/normalize"
)

func TestRateLimiter(t *testing.T) {
	t0 := time.Now()
	r := NewRateLimiter(2, time.Second)
	got := []bool{r.Allow(t0), r.Allow(t0), r.Allow(t0), r.Allow(t0.Add(time.Second))}
	want := []bool{true, true, false, true}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("Allow #%d = %v", i, got[i])
		}
	}
	if NewRateLimiter(0, time.Second).Allow(t0) {
		t.Error("n=0 must never allow")
	}
}

func TestLogSinkJSONAndSuppression(t *testing.T) {
	var buf bytes.Buffer
	s := NewLogSink(slog.New(slog.NewJSONHandler(&buf, nil)), 2)
	src := netip.MustParseAddr("10.1.1.1")
	flows := make([]normalize.FlowRecord, 5)
	for i := range flows {
		flows[i] = normalize.FlowRecord{ExporterIP: src, AgentIP: src, SrcIP: &src, SamplingRate: 1024}
	}
	if err := s.WriteFlows(context.Background(), flows); err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSpace(buf.String()), "\n")
	if len(lines) != 2 || s.Suppressed() != 3 {
		t.Fatalf("lines=%d suppressed=%d", len(lines), s.Suppressed())
	}
	var entry struct {
		Msg    string         `json:"msg"`
		Record map[string]any `json:"record"`
	}
	if err := json.Unmarshal([]byte(lines[0]), &entry); err != nil {
		t.Fatal(err)
	}
	if entry.Msg != "flow" || entry.Record["src_ip"] != "10.1.1.1" || entry.Record["dst_ip"] != nil {
		t.Errorf("entry = %+v", entry)
	}
	if _, ok := entry.Record["dst_ip"]; !ok {
		t.Error("nullable fields must be present as null")
	}
}
