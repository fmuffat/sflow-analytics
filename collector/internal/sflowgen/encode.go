// Package sflowgen encodes sFlow v5 datagrams. It is used by the synthetic
// traffic generator and by tests; it is intentionally independent from the
// decoder so that each validates the other.
package sflowgen

import (
	"bytes"
	"encoding/binary"
	"net/netip"
)

// Datagram is an sFlow v5 datagram.
type Datagram struct {
	AgentIP    netip.Addr
	SubAgentID uint32
	Sequence   uint32
	UptimeMs   uint32
	Samples    []Sample
}

// Sample is any sFlow sample (flow, counter, or raw/unknown).
type Sample interface {
	format() uint32
	body(*bytes.Buffer)
}

// Record is any flow or counter record.
type Record interface {
	format() uint32
	body(*bytes.Buffer)
}

// Marshal encodes the datagram in XDR (big-endian).
func (d Datagram) Marshal() []byte {
	var b bytes.Buffer
	u32(&b, 5)
	if d.AgentIP.Is4() {
		u32(&b, 1)
		a := d.AgentIP.As4()
		b.Write(a[:])
	} else {
		u32(&b, 2)
		a := d.AgentIP.As16()
		b.Write(a[:])
	}
	u32(&b, d.SubAgentID)
	u32(&b, d.Sequence)
	u32(&b, d.UptimeMs)
	u32(&b, uint32(len(d.Samples)))
	for _, s := range d.Samples {
		opaque(&b, s.format(), s.body)
	}
	return b.Bytes()
}

// FlowSample is a compact flow_sample (format 1).
type FlowSample struct {
	Sequence      uint32
	SourceIDType  uint32 // 0 = ifIndex
	SourceIDIndex uint32
	SamplingRate  uint32
	SamplePool    uint32
	Drops         uint32
	Input         uint32 // compact encoding: format<<30 | value
	Output        uint32
	Records       []Record
}

func (FlowSample) format() uint32 { return 1 }
func (s FlowSample) body(b *bytes.Buffer) {
	u32(b, s.Sequence)
	u32(b, s.SourceIDType<<24|s.SourceIDIndex&0x00FFFFFF)
	u32(b, s.SamplingRate)
	u32(b, s.SamplePool)
	u32(b, s.Drops)
	u32(b, s.Input)
	u32(b, s.Output)
	records(b, s.Records)
}

// ExpandedFlowSample is flow_sample_expanded (format 3).
type ExpandedFlowSample struct {
	Sequence                  uint32
	SourceIDType              uint32
	SourceIDIndex             uint32
	SamplingRate              uint32
	SamplePool                uint32
	Drops                     uint32
	InputFormat, InputValue   uint32
	OutputFormat, OutputValue uint32
	Records                   []Record
}

func (ExpandedFlowSample) format() uint32 { return 3 }
func (s ExpandedFlowSample) body(b *bytes.Buffer) {
	for _, v := range []uint32{s.Sequence, s.SourceIDType, s.SourceIDIndex, s.SamplingRate,
		s.SamplePool, s.Drops, s.InputFormat, s.InputValue, s.OutputFormat, s.OutputValue} {
		u32(b, v)
	}
	records(b, s.Records)
}

// CounterSample is a compact counters_sample (format 2).
type CounterSample struct {
	Sequence      uint32
	SourceIDType  uint32
	SourceIDIndex uint32
	Records       []Record
}

func (CounterSample) format() uint32 { return 2 }
func (s CounterSample) body(b *bytes.Buffer) {
	u32(b, s.Sequence)
	u32(b, s.SourceIDType<<24|s.SourceIDIndex&0x00FFFFFF)
	records(b, s.Records)
}

// RawSample is a sample with an arbitrary format and body (for testing
// unsupported sample types).
type RawSample struct {
	Format uint32
	Data   []byte
}

func (s RawSample) format() uint32       { return s.Format }
func (s RawSample) body(b *bytes.Buffer) { b.Write(s.Data) }

// SampledHeader is the raw packet header record (format 1).
type SampledHeader struct {
	Protocol    uint32 // 1 = Ethernet
	FrameLength uint32 // original frame length
	Stripped    uint32
	Header      []byte
}

func (SampledHeader) format() uint32 { return 1 }
func (r SampledHeader) body(b *bytes.Buffer) {
	u32(b, r.Protocol)
	u32(b, r.FrameLength)
	u32(b, r.Stripped)
	u32(b, uint32(len(r.Header)))
	b.Write(r.Header)
	pad(b, len(r.Header))
}

// SampledIPv4 is the decoded IPv4 record (format 3).
type SampledIPv4 struct {
	Length           uint32
	Protocol         uint32
	Src, Dst         netip.Addr
	SrcPort, DstPort uint32
	TCPFlags         uint32
	TOS              uint32
}

func (SampledIPv4) format() uint32 { return 3 }
func (r SampledIPv4) body(b *bytes.Buffer) {
	u32(b, r.Length)
	u32(b, r.Protocol)
	s, d := r.Src.As4(), r.Dst.As4()
	b.Write(s[:])
	b.Write(d[:])
	u32(b, r.SrcPort)
	u32(b, r.DstPort)
	u32(b, r.TCPFlags)
	u32(b, r.TOS)
}

// ExtendedSwitch is the extended switch record (format 1001).
type ExtendedSwitch struct {
	SrcVLAN, SrcPriority, DstVLAN, DstPriority uint32
}

func (ExtendedSwitch) format() uint32 { return 1001 }
func (r ExtendedSwitch) body(b *bytes.Buffer) {
	u32(b, r.SrcVLAN)
	u32(b, r.SrcPriority)
	u32(b, r.DstVLAN)
	u32(b, r.DstPriority)
}

// IfCounters is the generic interface counters record (format 1).
type IfCounters struct {
	IfIndex, IfType              uint32
	IfSpeed                      uint64
	IfDirection, IfStatus        uint32
	InOctets                     uint64
	InUcast, InMulti, InBcast    uint32
	InDiscards, InErrors         uint32
	InUnknownProtos              uint32
	OutOctets                    uint64
	OutUcast, OutMulti, OutBcast uint32
	OutDiscards, OutErrors       uint32
	Promiscuous                  uint32
}

func (IfCounters) format() uint32 { return 1 }
func (r IfCounters) body(b *bytes.Buffer) {
	u32(b, r.IfIndex)
	u32(b, r.IfType)
	u64(b, r.IfSpeed)
	u32(b, r.IfDirection)
	u32(b, r.IfStatus)
	u64(b, r.InOctets)
	for _, v := range []uint32{r.InUcast, r.InMulti, r.InBcast, r.InDiscards, r.InErrors, r.InUnknownProtos} {
		u32(b, v)
	}
	u64(b, r.OutOctets)
	for _, v := range []uint32{r.OutUcast, r.OutMulti, r.OutBcast, r.OutDiscards, r.OutErrors, r.Promiscuous} {
		u32(b, v)
	}
}

// RawRecord is a record with an arbitrary format (for testing unknown records).
type RawRecord struct {
	Format uint32
	Data   []byte
}

func (r RawRecord) format() uint32       { return r.Format }
func (r RawRecord) body(b *bytes.Buffer) { b.Write(r.Data) }

func records(b *bytes.Buffer, rs []Record) {
	u32(b, uint32(len(rs)))
	for _, r := range rs {
		opaque(b, r.format(), r.body)
	}
}

// opaque writes format, length and the body produced by fn.
func opaque(b *bytes.Buffer, format uint32, fn func(*bytes.Buffer)) {
	var inner bytes.Buffer
	fn(&inner)
	u32(b, format)
	u32(b, uint32(inner.Len()))
	b.Write(inner.Bytes())
}

func u32(b *bytes.Buffer, v uint32) { _ = binary.Write(b, binary.BigEndian, v) }
func u64(b *bytes.Buffer, v uint64) { _ = binary.Write(b, binary.BigEndian, v) }

func pad(b *bytes.Buffer, n int) {
	if r := n % 4; r != 0 {
		b.Write(make([]byte, 4-r))
	}
}

// CompactIf encodes a single ifIndex in compact interface format.
func CompactIf(ifIndex uint32) uint32 { return ifIndex & 0x3FFFFFFF }
