// Package exporters keeps the in-memory inventory of sFlow exporters that
// have been seen by the collector (automatic exporter discovery).
package exporters

import (
	"fmt"
	"net/netip"
	"sort"
	"sync"
	"time"
)

// Key identifies an exporter: UDP source IP + sFlow agent address + sub-agent ID.
type Key struct {
	ExporterIP netip.Addr
	AgentIP    netip.Addr
	SubAgentID uint32
}

// ID is a stable, human-readable identifier derived from the key.
func (k Key) ID() string {
	return fmt.Sprintf("%s/%s/%d", k.ExporterIP, k.AgentIP, k.SubAgentID)
}

// Status of an exporter.
const (
	StatusActive   = "active"
	StatusInactive = "inactive"
)

// Observation is what one decoded datagram contributes to an exporter.
type Observation struct {
	Time           time.Time
	Sequence       uint32
	Samples        int
	FlowSamples    int
	CounterSamples int
	Errors         int
	SamplingRate   uint32            // last non-zero sampling rate seen, 0 if none
	IfIndexes      []uint32          // interfaces referenced by flows or counters
	IfStatus       []InterfaceStatus // from counter samples
}

// InterfaceStatus is interface state reported by a counter sample.
type InterfaceStatus struct {
	IfIndex  uint32
	SpeedBps uint64
	OperUp   bool
}

// Interface is an interface index seen for an exporter.
type Interface struct {
	IfIndex   uint32    `json:"ifindex"`
	FirstSeen time.Time `json:"first_seen"`
	LastSeen  time.Time `json:"last_seen"`
	SpeedBps  uint64    `json:"speed_bps"` // 0 until a counter sample is seen
	OperUp    *bool     `json:"oper_up"`
}

// Exporter is a point-in-time view of one exporter.
type Exporter struct {
	ID             string      `json:"id"`
	ExporterIP     netip.Addr  `json:"exporter_ip"`
	AgentIP        netip.Addr  `json:"agent_ip"`
	AgentSubID     uint32      `json:"agent_sub_id"`
	FirstSeen      time.Time   `json:"first_seen"`
	LastSeen       time.Time   `json:"last_seen"`
	Status         string      `json:"status"`
	SamplingRate   uint32      `json:"sample_rate"`
	SamplesPerSec  float64     `json:"samples_per_second"`
	Datagrams      uint64      `json:"datagrams"`
	Samples        uint64      `json:"samples"`
	FlowSamples    uint64      `json:"flow_samples"`
	CounterSamples uint64      `json:"counter_samples"`
	Errors         uint64      `json:"errors"`
	LostDatagrams  uint64      `json:"lost_datagrams"` // inferred from sequence gaps
	Interfaces     []Interface `json:"interfaces,omitempty"`
}

type state struct {
	Exporter
	lastSeq     uint32
	hasSeq      bool
	prevSamples uint64
	interfaces  map[uint32]*Interface
}

// Registry is safe for concurrent use.
type Registry struct {
	mu            sync.RWMutex
	byKey         map[Key]*state
	inactiveAfter time.Duration
	lastTick      time.Time
}

// NewRegistry creates a registry; exporters silent for longer than
// inactiveAfter are reported as inactive.
func NewRegistry(inactiveAfter time.Duration) *Registry {
	return &Registry{byKey: make(map[Key]*state), inactiveAfter: inactiveAfter}
}

// Observe records one datagram and reports whether the exporter is new.
func (r *Registry) Observe(k Key, o Observation) (isNew bool) {
	r.mu.Lock()
	defer r.mu.Unlock()

	s, ok := r.byKey[k]
	if !ok {
		isNew = true
		s = &state{
			Exporter: Exporter{
				ID: k.ID(), ExporterIP: k.ExporterIP, AgentIP: k.AgentIP, AgentSubID: k.SubAgentID,
				FirstSeen: o.Time, Status: StatusActive,
			},
			interfaces: make(map[uint32]*Interface),
		}
		r.byKey[k] = s
	}

	// Sequence numbers increase by one per datagram; a jump means datagrams
	// were lost in transit. A backwards jump (large wrapped delta) means the
	// agent restarted or datagrams were reordered; it is not counted as loss.
	if s.hasSeq {
		if delta := o.Sequence - s.lastSeq; delta > 1 && delta < 1<<31 {
			s.LostDatagrams += uint64(delta - 1)
		}
	}
	s.lastSeq, s.hasSeq = o.Sequence, true

	if o.Time.After(s.LastSeen) {
		s.LastSeen = o.Time
	}
	s.Status = StatusActive
	s.Datagrams++
	s.Samples += uint64(o.Samples)
	s.FlowSamples += uint64(o.FlowSamples)
	s.CounterSamples += uint64(o.CounterSamples)
	s.Errors += uint64(o.Errors)
	if o.SamplingRate != 0 {
		s.SamplingRate = o.SamplingRate
	}
	for _, idx := range o.IfIndexes {
		s.touchInterface(idx, o.Time)
	}
	for _, st := range o.IfStatus {
		it := s.touchInterface(st.IfIndex, o.Time)
		it.SpeedBps = st.SpeedBps
		up := st.OperUp
		it.OperUp = &up
	}
	return isNew
}

func (s *state) touchInterface(idx uint32, t time.Time) *Interface {
	it, ok := s.interfaces[idx]
	if !ok {
		it = &Interface{IfIndex: idx, FirstSeen: t}
		s.interfaces[idx] = it
	}
	if t.After(it.LastSeen) {
		it.LastSeen = t
	}
	return it
}

// Restore loads previously persisted exporters (e.g. from the database at
// startup). Exporters already known in memory are left untouched. Traffic
// counters restart from zero; identity, first/last seen, sampling rate and
// interfaces are kept.
func (r *Registry) Restore(list []Exporter, now time.Time) int {
	r.mu.Lock()
	defer r.mu.Unlock()
	n := 0
	for _, e := range list {
		k := Key{ExporterIP: e.ExporterIP, AgentIP: e.AgentIP, SubAgentID: e.AgentSubID}
		if _, ok := r.byKey[k]; ok {
			continue
		}
		st := &state{
			Exporter: Exporter{
				ID: k.ID(), ExporterIP: k.ExporterIP, AgentIP: k.AgentIP, AgentSubID: k.SubAgentID,
				FirstSeen: e.FirstSeen, LastSeen: e.LastSeen, SamplingRate: e.SamplingRate,
				Status: StatusActive,
			},
			interfaces: make(map[uint32]*Interface),
		}
		if now.Sub(e.LastSeen) > r.inactiveAfter {
			st.Status = StatusInactive
		}
		for _, it := range e.Interfaces {
			c := it
			st.interfaces[it.IfIndex] = &c
		}
		r.byKey[k] = st
		n++
	}
	return n
}

// Transition describes an exporter changing status during Tick.
type Transition struct {
	Exporter Exporter
	From, To string
}

// Tick refreshes per-exporter rates and statuses. It should be called
// periodically; it returns status transitions for logging.
func (r *Registry) Tick(now time.Time) []Transition {
	r.mu.Lock()
	defer r.mu.Unlock()

	elapsed := now.Sub(r.lastTick).Seconds()
	first := r.lastTick.IsZero()
	r.lastTick = now

	var out []Transition
	for _, s := range r.byKey {
		if !first && elapsed > 0 {
			s.SamplesPerSec = float64(s.Samples-s.prevSamples) / elapsed
		}
		s.prevSamples = s.Samples

		status := StatusActive
		if now.Sub(s.LastSeen) > r.inactiveAfter {
			status = StatusInactive
			s.SamplesPerSec = 0
		}
		if status != s.Status {
			out = append(out, Transition{Exporter: s.Exporter, From: s.Status, To: status})
			s.Status = status
		}
	}
	return out
}

// Snapshot returns all exporters sorted by ID. Interfaces are included when
// withInterfaces is true.
func (r *Registry) Snapshot(withInterfaces bool) []Exporter {
	r.mu.RLock()
	defer r.mu.RUnlock()

	out := make([]Exporter, 0, len(r.byKey))
	for _, s := range r.byKey {
		e := s.Exporter
		if withInterfaces {
			e.Interfaces = make([]Interface, 0, len(s.interfaces))
			for _, it := range s.interfaces {
				e.Interfaces = append(e.Interfaces, *it)
			}
			sort.Slice(e.Interfaces, func(i, j int) bool { return e.Interfaces[i].IfIndex < e.Interfaces[j].IfIndex })
		}
		out = append(out, e)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}

// Get returns one exporter by ID, including its interfaces.
func (r *Registry) Get(id string) (Exporter, bool) {
	for _, e := range r.Snapshot(true) {
		if e.ID == id {
			return e, true
		}
	}
	return Exporter{}, false
}

// Counts returns the number of known and active exporters.
func (r *Registry) Counts() (total, active int) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	for _, s := range r.byKey {
		total++
		if s.Status == StatusActive {
			active++
		}
	}
	return total, active
}
