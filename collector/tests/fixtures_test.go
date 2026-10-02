// Package tests holds fixture-based regression tests: every datagram under
// fixtures/ is decoded and compared to its golden JSON output.
//
// Regenerate after an intentional change:
//
//	go run ./cmd/sflow-gen -write-fixtures tests/fixtures
//	go test ./tests -update
package tests

import (
	"bytes"
	"encoding/json"
	"errors"
	"flag"
	"net/netip"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"sflow-analytics/collector/internal/decoder"
	"sflow-analytics/collector/internal/exporters"
	"sflow-analytics/collector/internal/pcap"
	"sflow-analytics/collector/internal/sflowgen"
)

var update = flag.Bool("update", false, "rewrite golden files")

var (
	exporterIP = netip.MustParseAddr("192.0.2.10")
	fixedNow   = time.Date(2026, 9, 28, 14, 20, 31, 123000000, time.UTC)
)

type golden struct {
	Error  string          `json:"error,omitempty"`
	Result *decoder.Result `json:"result,omitempty"`
}

func TestFixturesAreUpToDate(t *testing.T) {
	for _, fx := range sflowgen.Fixtures() {
		b, err := os.ReadFile(filepath.Join("fixtures", fx.Name+".bin"))
		if err != nil {
			t.Fatalf("%v (run: go run ./cmd/sflow-gen -write-fixtures tests/fixtures)", err)
		}
		if !bytes.Equal(b, fx.Data) {
			t.Errorf("%s.bin is stale (run: go run ./cmd/sflow-gen -write-fixtures tests/fixtures)", fx.Name)
		}
	}
}

func TestFixturesGolden(t *testing.T) {
	files, _ := filepath.Glob(filepath.Join("fixtures", "*.bin"))
	if len(files) == 0 {
		t.Fatal("no fixtures found")
	}
	for _, f := range files {
		name := strings.TrimSuffix(filepath.Base(f), ".bin")
		t.Run(name, func(t *testing.T) {
			data, err := os.ReadFile(f)
			if err != nil {
				t.Fatal(err)
			}
			var g golden
			res, err := decoder.Decode(data, exporterIP, fixedNow)
			if err != nil {
				g.Error = err.Error()
			} else {
				g.Result = &res
			}
			got, _ := json.MarshalIndent(g, "", "  ")
			got = append(got, '\n')

			path := filepath.Join("fixtures", name+".golden.json")
			if *update {
				if err := os.WriteFile(path, got, 0o644); err != nil {
					t.Fatal(err)
				}
				return
			}
			want, err := os.ReadFile(path)
			if err != nil {
				t.Fatalf("%v (run: go test ./tests -update)", err)
			}
			if !bytes.Equal(bytes.ReplaceAll(want, []byte("\r\n"), []byte("\n")), got) {
				t.Errorf("decoded output differs from %s\n--- got ---\n%s", path, got)
			}
		})
	}
}

// TestPcapReplay decodes a capture with two exporters, as a replayed ICX
// capture would be processed.
func TestPcapReplay(t *testing.T) {
	f, err := os.Open(filepath.Join("fixtures", "two_exporters.pcap"))
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	dgs, err := pcap.Read(f, 6343)
	if err != nil {
		t.Fatal(err)
	}
	if len(dgs) != 20 {
		t.Fatalf("datagrams = %d, want 20", len(dgs))
	}

	reg := exporters.NewRegistry(time.Minute)
	flows := 0
	for _, d := range dgs {
		res, err := decoder.Decode(d.Payload, d.Src, d.Time)
		if err != nil {
			t.Fatal(err)
		}
		flows += len(res.Flows)
		reg.Observe(exporters.Key{ExporterIP: d.Src, AgentIP: res.Meta.AgentIP, SubAgentID: res.Meta.SubAgentID},
			exporters.Observation{Time: d.Time, Sequence: res.Meta.SequenceNumber, Samples: res.Samples})
	}
	if flows != 80 {
		t.Errorf("flows = %d, want 80", flows)
	}
	list := reg.Snapshot(false)
	if len(list) != 2 {
		t.Fatalf("exporters = %d, want 2", len(list))
	}
	for _, e := range list {
		if e.Datagrams != 10 || e.LostDatagrams != 0 {
			t.Errorf("%s: datagrams=%d lost=%d", e.ID, e.Datagrams, e.LostDatagrams)
		}
	}
}

func TestErrorsAreClassified(t *testing.T) {
	data, _ := os.ReadFile(filepath.Join("fixtures", "unsupported_version.bin"))
	if _, err := decoder.Decode(data, exporterIP, fixedNow); !errors.Is(err, decoder.ErrUnsupportedVersion) {
		t.Errorf("err = %v", err)
	}
}
