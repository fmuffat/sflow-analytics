// Command sflow-gen sends synthetic sFlow v5 traffic from one or more fake
// exporters, replays captured datagrams, or writes test fixtures.
//
//	sflow-gen -target 127.0.0.1:6343 -exporters 5 -rate 20
//	sflow-gen -target 127.0.0.1:6343 -replay capture.pcap -speed 1
//	sflow-gen -write-fixtures tests/fixtures
package main

import (
	"context"
	"encoding/binary"
	"flag"
	"fmt"
	"log/slog"
	"math/rand/v2"
	"net"
	"net/netip"
	"os"
	"os/signal"
	"path/filepath"
	"sort"
	"strings"
	"syscall"
	"time"

	"sflow-analytics/collector/internal/pcap"
	"sflow-analytics/collector/internal/sflowgen"
)

func main() {
	var (
		target        = flag.String("target", "127.0.0.1:6343", "collector UDP address")
		exporterCount = flag.Int("exporters", 3, "number of fake exporters (distinct agent IPs)")
		agentBase     = flag.String("agent-base", "10.255.0.1", "agent IP of the first fake exporter; others increment")
		rate          = flag.Float64("rate", 10, "datagrams per second per exporter")
		samples       = flag.Int("samples", 5, "flow samples per datagram")
		samplingRate  = flag.Uint("sampling-rate", 1024, "sampling rate advertised by fake exporters")
		ports         = flag.Int("ports", 48, "access ports per fake switch")
		counterEvery  = flag.Duration("counter-interval", 20*time.Second, "interval between counter samples per exporter")
		malformed     = flag.Int("malformed-every", 0, "send a garbage datagram every N datagrams (0 = never)")
		duration      = flag.Duration("duration", 0, "stop after this duration (0 = run until interrupted)")
		seed          = flag.Uint64("seed", 1, "random seed")

		replay     = flag.String("replay", "", "replay a .pcap file, a raw .bin datagram, or a directory of .bin files")
		replayPort = flag.Uint("replay-port", 6343, "UDP destination port to extract from pcap (0 = any)")
		speed      = flag.Float64("speed", 0, "pcap replay speed: 1 = real time, 0 = as fast as possible")
		loop       = flag.Bool("loop", false, "replay in a loop")

		writeFixtures = flag.String("write-fixtures", "", "write the canonical test fixtures to this directory and exit")
	)
	flag.Parse()
	log := slog.New(slog.NewJSONHandler(os.Stdout, nil)).With("service", "sflow-gen")

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if *duration > 0 {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, *duration)
		defer cancel()
	}

	var err error
	switch {
	case *writeFixtures != "":
		err = doWriteFixtures(*writeFixtures)
	case *replay != "":
		err = doReplay(ctx, log, *target, *replay, uint16(*replayPort), *speed, *loop)
	default:
		err = doSynthetic(ctx, log, synthOpts{
			target: *target, exporters: *exporterCount, agentBase: *agentBase, rate: *rate,
			samples: *samples, samplingRate: uint32(*samplingRate), ports: *ports,
			counterEvery: *counterEvery, malformedEvery: *malformed, seed: *seed,
		})
	}
	if err != nil {
		log.Error("sflow-gen failed", "error", err)
		os.Exit(1)
	}
}

type synthOpts struct {
	target         string
	exporters      int
	agentBase      string
	rate           float64
	samples        int
	samplingRate   uint32
	ports          int
	counterEvery   time.Duration
	malformedEvery int
	seed           uint64
}

func doSynthetic(ctx context.Context, log *slog.Logger, o synthOpts) error {
	if o.exporters < 1 || o.rate <= 0 || o.samples < 1 {
		return fmt.Errorf("exporters, rate and samples must be positive")
	}
	base, err := netip.ParseAddr(o.agentBase)
	if err != nil || !base.Is4() {
		return fmt.Errorf("agent-base must be an IPv4 address: %q", o.agentBase)
	}
	conn, err := net.Dial("udp", o.target)
	if err != nil {
		return err
	}
	defer conn.Close()

	scenarios := make([]*sflowgen.Scenario, o.exporters)
	agent := base
	for i := range scenarios {
		scenarios[i] = sflowgen.NewScenario(agent, 0, o.samplingRate, o.ports, o.seed+uint64(i))
		agent = agent.Next()
	}
	log.Info("sending synthetic sflow", "target", o.target, "exporters", o.exporters,
		"agent_base", o.agentBase, "datagrams_per_sec_per_exporter", o.rate, "samples_per_datagram", o.samples)

	// Pace on elapsed time rather than one datagram per tick, so that high
	// aggregate rates are reached despite timer granularity.
	totalRate := o.rate * float64(o.exporters)
	tick := time.NewTicker(max(time.Duration(float64(time.Second)/totalRate), time.Millisecond))
	defer tick.Stop()
	stats := time.NewTicker(10 * time.Second)
	defer stats.Stop()

	start := time.Now()
	lastCounters := make([]time.Time, o.exporters)
	junk := rand.New(rand.NewPCG(o.seed, 42))
	var i, sent, errs uint64
	for {
		select {
		case <-ctx.Done():
			log.Info("stopped", "datagrams_sent", sent, "send_errors", errs)
			return nil
		case <-stats.C:
			log.Info("progress", "datagrams_sent", sent, "send_errors", errs)
		case now := <-tick.C:
			due := uint64(now.Sub(start).Seconds() * totalRate)
			for ; i < due; i++ {
				var payload []byte
				if o.malformedEvery > 0 && i%uint64(o.malformedEvery) == uint64(o.malformedEvery-1) {
					payload = garbage(junk)
				} else {
					idx := int(i % uint64(o.exporters))
					withCounters := now.Sub(lastCounters[idx]) >= o.counterEvery
					if withCounters {
						lastCounters[idx] = now
					}
					payload = scenarios[idx].NextDatagram(o.samples, withCounters).Marshal()
				}
				if _, err := conn.Write(payload); err != nil {
					errs++
				} else {
					sent++
				}
			}
		}
	}
}

// garbage returns a datagram that looks like sFlow v5 but is corrupted.
func garbage(r *rand.Rand) []byte {
	b := make([]byte, 20+r.IntN(200))
	for i := range b {
		b[i] = byte(r.IntN(256))
	}
	if r.IntN(2) == 0 {
		binary.BigEndian.PutUint32(b[0:4], 5)
		binary.BigEndian.PutUint32(b[4:8], 1)
	}
	return b
}

func doReplay(ctx context.Context, log *slog.Logger, target, path string, port uint16, speed float64, loop bool) error {
	dgs, err := loadReplay(path, port)
	if err != nil {
		return err
	}
	if len(dgs) == 0 {
		return fmt.Errorf("no datagrams found in %s", path)
	}
	conn, err := net.Dial("udp", target)
	if err != nil {
		return err
	}
	defer conn.Close()
	log.Info("replaying", "source", path, "datagrams", len(dgs), "target", target, "speed", speed, "loop", loop)
	log.Info("note: replayed datagrams arrive from this host's IP; the exporter is still identified by the sFlow agent IP")

	var sent uint64
	for {
		for i, d := range dgs {
			if speed > 0 && i > 0 && !dgs[i-1].Time.IsZero() {
				wait := time.Duration(float64(d.Time.Sub(dgs[i-1].Time)) / speed)
				if wait > 0 {
					select {
					case <-ctx.Done():
						return nil
					case <-time.After(wait):
					}
				}
			}
			if ctx.Err() != nil {
				return nil
			}
			if _, err := conn.Write(d.Payload); err == nil {
				sent++
			}
		}
		if !loop || ctx.Err() != nil {
			break
		}
	}
	log.Info("replay finished", "datagrams_sent", sent)
	return nil
}

func loadReplay(path string, port uint16) ([]pcap.Datagram, error) {
	st, err := os.Stat(path)
	if err != nil {
		return nil, err
	}
	if st.IsDir() {
		files, err := filepath.Glob(filepath.Join(path, "*.bin"))
		if err != nil {
			return nil, err
		}
		sort.Strings(files)
		var out []pcap.Datagram
		for _, f := range files {
			b, err := os.ReadFile(f)
			if err != nil {
				return nil, err
			}
			out = append(out, pcap.Datagram{Payload: b})
		}
		return out, nil
	}
	if strings.HasSuffix(strings.ToLower(path), ".bin") {
		b, err := os.ReadFile(path)
		return []pcap.Datagram{{Payload: b}}, err
	}
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	return pcap.Read(f, port)
}

func doWriteFixtures(dir string) error {
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	var index strings.Builder
	index.WriteString("# Collector test fixtures\n\nGenerated by `go run ./cmd/sflow-gen -write-fixtures tests/fixtures`. Synthetic data only.\n\n")
	for _, fx := range sflowgen.Fixtures() {
		if err := os.WriteFile(filepath.Join(dir, fx.Name+".bin"), fx.Data, 0o644); err != nil {
			return err
		}
		fmt.Fprintf(&index, "- `%s.bin` (%d bytes): %s\n", fx.Name, len(fx.Data), fx.Description)
	}

	// A small multi-exporter capture for pcap replay tests.
	var dgs []pcap.Datagram
	t0 := time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC)
	sc := []*sflowgen.Scenario{
		sflowgen.NewScenario(netip.MustParseAddr("10.255.0.1"), 0, 1024, 48, 7),
		sflowgen.NewScenario(netip.MustParseAddr("10.255.0.2"), 0, 2048, 24, 8),
	}
	for i := 0; i < 20; i++ {
		d := sc[i%2].NextDatagram(4, i < 2)
		src := netip.MustParseAddr("10.255.0.1")
		if i%2 == 1 {
			src = netip.MustParseAddr("10.255.0.2")
		}
		dgs = append(dgs, pcap.Datagram{Time: t0.Add(time.Duration(i) * 100 * time.Millisecond), Src: src, DstPort: 6343, Payload: d.Marshal()})
	}
	f, err := os.Create(filepath.Join(dir, "two_exporters.pcap"))
	if err != nil {
		return err
	}
	if err := pcap.Write(f, dgs); err != nil {
		f.Close()
		return err
	}
	if err := f.Close(); err != nil {
		return err
	}
	fmt.Fprintf(&index, "- `two_exporters.pcap`: 20 datagrams from agents 10.255.0.1 and 10.255.0.2 (classic pcap)\n")
	return os.WriteFile(filepath.Join(dir, "README.md"), []byte(index.String()), 0o644)
}
