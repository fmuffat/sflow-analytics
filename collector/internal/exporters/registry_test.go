package exporters

import (
	"net/netip"
	"testing"
	"time"
)

var t0 = time.Date(2026, 9, 28, 12, 0, 0, 0, time.UTC)

func key(exp, agent string, sub uint32) Key {
	return Key{ExporterIP: netip.MustParseAddr(exp), AgentIP: netip.MustParseAddr(agent), SubAgentID: sub}
}

func TestDiscovery(t *testing.T) {
	r := NewRegistry(5 * time.Minute)
	k := key("10.0.0.1", "10.0.0.1", 0)
	if !r.Observe(k, Observation{Time: t0, Sequence: 1, Samples: 3}) {
		t.Fatal("first observation must report a new exporter")
	}
	if r.Observe(k, Observation{Time: t0.Add(time.Second), Sequence: 2, Samples: 2}) {
		t.Fatal("second observation must not report a new exporter")
	}
	e, ok := r.Get(k.ID())
	if !ok {
		t.Fatal("exporter not found")
	}
	if e.Datagrams != 2 || e.Samples != 5 || !e.FirstSeen.Equal(t0) || !e.LastSeen.Equal(t0.Add(time.Second)) {
		t.Errorf("exporter = %+v", e)
	}
}

func TestExporterIdentity(t *testing.T) {
	r := NewRegistry(time.Minute)
	for _, k := range []Key{
		key("10.0.0.1", "10.0.0.1", 0),
		key("10.0.0.1", "10.0.0.1", 1),  // other sub-agent
		key("10.0.0.1", "10.99.0.1", 0), // other agent IP behind the same source
		key("192.0.2.1", "10.0.0.1", 0), // same agent IP from another source
	} {
		if !r.Observe(k, Observation{Time: t0}) {
			t.Errorf("%s not reported as new", k.ID())
		}
	}
	if total, _ := r.Counts(); total != 4 {
		t.Errorf("total = %d, want 4", total)
	}
}

func TestInactivityAndRates(t *testing.T) {
	r := NewRegistry(5 * time.Minute)
	k := key("10.0.0.1", "10.0.0.1", 0)
	r.Observe(k, Observation{Time: t0, Sequence: 1, Samples: 10})
	r.Tick(t0)
	r.Observe(k, Observation{Time: t0.Add(5 * time.Second), Sequence: 2, Samples: 50})
	if tr := r.Tick(t0.Add(10 * time.Second)); len(tr) != 0 {
		t.Errorf("unexpected transitions %+v", tr)
	}
	e, _ := r.Get(k.ID())
	if e.SamplesPerSec != 5 || e.Status != StatusActive {
		t.Errorf("rate=%v status=%s", e.SamplesPerSec, e.Status)
	}

	tr := r.Tick(t0.Add(6 * time.Minute))
	if len(tr) != 1 || tr[0].To != StatusInactive {
		t.Fatalf("transitions = %+v", tr)
	}
	if _, active := r.Counts(); active != 0 {
		t.Errorf("active = %d", active)
	}

	r.Observe(k, Observation{Time: t0.Add(7 * time.Minute), Sequence: 3})
	if e, _ := r.Get(k.ID()); e.Status != StatusActive {
		t.Error("exporter must become active again on new traffic")
	}
}

func TestSequenceGaps(t *testing.T) {
	r := NewRegistry(time.Minute)
	k := key("10.0.0.1", "10.0.0.1", 0)
	for _, seq := range []uint32{10, 11, 15, 16, 2 /* agent restart */, 3, 0xFFFFFFFF, 0 /* wrap */, 1} {
		r.Observe(k, Observation{Time: t0, Sequence: seq})
	}
	if e, _ := r.Get(k.ID()); e.LostDatagrams != 3 {
		t.Errorf("lost = %d, want 3 (12,13,14)", e.LostDatagrams)
	}
}

func TestInterfacesAndSamplingRate(t *testing.T) {
	r := NewRegistry(time.Minute)
	k := key("10.0.0.1", "10.0.0.1", 0)
	r.Observe(k, Observation{Time: t0, SamplingRate: 1024, IfIndexes: []uint32{48, 24}})
	r.Observe(k, Observation{Time: t0.Add(time.Second), IfIndexes: []uint32{24}})
	e, _ := r.Get(k.ID())
	if e.SamplingRate != 1024 {
		t.Errorf("sampling rate = %d", e.SamplingRate)
	}
	if len(e.Interfaces) != 2 || e.Interfaces[0].IfIndex != 24 || !e.Interfaces[0].LastSeen.Equal(t0.Add(time.Second)) {
		t.Errorf("interfaces = %+v", e.Interfaces)
	}
	if list := r.Snapshot(false); list[0].Interfaces != nil {
		t.Error("Snapshot(false) must omit interfaces")
	}
}

func TestInterfaceStatusFromCounters(t *testing.T) {
	r := NewRegistry(time.Minute)
	k := key("10.0.0.1", "10.0.0.1", 0)
	r.Observe(k, Observation{Time: t0, IfStatus: []InterfaceStatus{{IfIndex: 48, SpeedBps: 10_000_000_000, OperUp: true}}})
	e, _ := r.Get(k.ID())
	if len(e.Interfaces) != 1 || e.Interfaces[0].SpeedBps != 10_000_000_000 || e.Interfaces[0].OperUp == nil || !*e.Interfaces[0].OperUp {
		t.Errorf("interfaces = %+v", e.Interfaces)
	}
}

func TestRestore(t *testing.T) {
	src := NewRegistry(5 * time.Minute)
	kOld := key("10.0.0.1", "10.0.0.1", 0)
	kNew := key("10.0.0.2", "10.0.0.2", 1)
	src.Observe(kOld, Observation{Time: t0, SamplingRate: 512, IfIndexes: []uint32{3}})
	src.Observe(kNew, Observation{Time: t0.Add(time.Hour)})

	dst := NewRegistry(5 * time.Minute)
	dst.Observe(kNew, Observation{Time: t0.Add(2 * time.Hour)}) // already live: must be kept as is
	if n := dst.Restore(src.Snapshot(true), t0.Add(2*time.Hour)); n != 1 {
		t.Fatalf("restored = %d, want 1", n)
	}
	e, ok := dst.Get(kOld.ID())
	if !ok || !e.FirstSeen.Equal(t0) || e.SamplingRate != 512 || len(e.Interfaces) != 1 || e.Status != StatusInactive || e.Datagrams != 0 {
		t.Errorf("restored exporter = %+v", e)
	}
	if dst.Observe(kOld, Observation{Time: t0.Add(3 * time.Hour)}) {
		t.Error("restored exporter reported as new")
	}
	if live, _ := dst.Get(kNew.ID()); live.Datagrams != 1 {
		t.Errorf("live exporter overwritten: %+v", live)
	}
}
