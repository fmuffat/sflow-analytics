// Package metrics holds the collector's global counters. High-frequency
// events are counted here rather than logged.
package metrics

import (
	"sync"
	"sync/atomic"
	"time"
)

// Collector counters. All fields are safe for concurrent use.
type Collector struct {
	DatagramsReceived  atomic.Uint64
	BytesReceived      atomic.Uint64
	DatagramsDropped   atomic.Uint64 // decode queue full
	MalformedDatagrams atomic.Uint64
	UnsupportedVersion atomic.Uint64
	TruncatedDatagrams atomic.Uint64
	SamplesReceived    atomic.Uint64
	FlowSamples        atomic.Uint64
	CounterSamples     atomic.Uint64
	DropSamples        atomic.Uint64
	UnsupportedSamples atomic.Uint64
	UnsupportedRecords atomic.Uint64
	SampleErrors       atomic.Uint64
	FlowRecords        atomic.Uint64 // normalized flow records emitted
	SinkErrors         atomic.Uint64 // storage write failures (database insert failures)
	RowsInserted       atomic.Uint64 // rows written to the database
	RowsDropped        atomic.Uint64 // rows lost: sink queue full or retry queue overflow
	PendingRows        atomic.Int64  // rows waiting in the sink (queue + retry)
	lastPacketUnixNano atomic.Int64

	startedAt time.Time

	mu            sync.Mutex
	lastTick      time.Time
	prevDatagrams uint64
	prevSamples   uint64
	datagramsRate float64
	samplesRate   float64
}

// New returns a Collector with its start time set.
func New() *Collector {
	return &Collector{startedAt: time.Now()}
}

// MarkPacket records the arrival time of the latest datagram.
func (c *Collector) MarkPacket(t time.Time) { c.lastPacketUnixNano.Store(t.UnixNano()) }

// Tick recomputes per-second rates since the previous tick.
func (c *Collector) Tick(now time.Time) {
	c.mu.Lock()
	defer c.mu.Unlock()
	d, s := c.DatagramsReceived.Load(), c.SamplesReceived.Load()
	if !c.lastTick.IsZero() {
		if el := now.Sub(c.lastTick).Seconds(); el > 0 {
			c.datagramsRate = float64(d-c.prevDatagrams) / el
			c.samplesRate = float64(s-c.prevSamples) / el
		}
	}
	c.lastTick, c.prevDatagrams, c.prevSamples = now, d, s
}

// Snapshot is a JSON-friendly copy of the counters.
type Snapshot struct {
	StartedAt          time.Time  `json:"started_at"`
	UptimeSeconds      float64    `json:"uptime_seconds"`
	LastPacketAt       *time.Time `json:"last_packet_at"`
	DatagramsReceived  uint64     `json:"datagrams_received"`
	DatagramsPerSec    float64    `json:"datagrams_per_second"`
	BytesReceived      uint64     `json:"bytes_received"`
	DatagramsDropped   uint64     `json:"datagrams_dropped"`
	MalformedDatagrams uint64     `json:"malformed_datagrams"`
	UnsupportedVersion uint64     `json:"unsupported_version_datagrams"`
	TruncatedDatagrams uint64     `json:"truncated_datagrams"`
	SamplesReceived    uint64     `json:"samples_received"`
	SamplesPerSec      float64    `json:"samples_per_second"`
	FlowSamples        uint64     `json:"flow_samples"`
	CounterSamples     uint64     `json:"counter_samples"`
	DropSamples        uint64     `json:"drop_samples"`
	UnsupportedSamples uint64     `json:"unsupported_samples"`
	UnsupportedRecords uint64     `json:"unsupported_records"`
	SampleErrors       uint64     `json:"sample_errors"`
	FlowRecords        uint64     `json:"flow_records"`
	SinkErrors         uint64     `json:"db_insert_failures"`
	RowsInserted       uint64     `json:"db_rows_inserted"`
	RowsDropped        uint64     `json:"db_rows_dropped"`
	PendingRows        int64      `json:"db_rows_pending"`
	ExportersTotal     int        `json:"exporters_total"`
	ExportersActive    int        `json:"exporters_active"`
}

// Snapshot captures the current values.
func (c *Collector) Snapshot(now time.Time) Snapshot {
	c.mu.Lock()
	dr, sr := c.datagramsRate, c.samplesRate
	c.mu.Unlock()

	s := Snapshot{
		StartedAt:          c.startedAt,
		UptimeSeconds:      now.Sub(c.startedAt).Seconds(),
		DatagramsReceived:  c.DatagramsReceived.Load(),
		DatagramsPerSec:    dr,
		BytesReceived:      c.BytesReceived.Load(),
		DatagramsDropped:   c.DatagramsDropped.Load(),
		MalformedDatagrams: c.MalformedDatagrams.Load(),
		UnsupportedVersion: c.UnsupportedVersion.Load(),
		TruncatedDatagrams: c.TruncatedDatagrams.Load(),
		SamplesReceived:    c.SamplesReceived.Load(),
		SamplesPerSec:      sr,
		FlowSamples:        c.FlowSamples.Load(),
		CounterSamples:     c.CounterSamples.Load(),
		DropSamples:        c.DropSamples.Load(),
		UnsupportedSamples: c.UnsupportedSamples.Load(),
		UnsupportedRecords: c.UnsupportedRecords.Load(),
		SampleErrors:       c.SampleErrors.Load(),
		FlowRecords:        c.FlowRecords.Load(),
		SinkErrors:         c.SinkErrors.Load(),
		RowsInserted:       c.RowsInserted.Load(),
		RowsDropped:        c.RowsDropped.Load(),
		PendingRows:        c.PendingRows.Load(),
	}
	if ns := c.lastPacketUnixNano.Load(); ns != 0 {
		t := time.Unix(0, ns).UTC()
		s.LastPacketAt = &t
	}
	return s
}
