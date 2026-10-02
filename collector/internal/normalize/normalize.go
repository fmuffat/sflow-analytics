package normalize

import (
	"net/netip"

	"github.com/netsampler/goflow2/v2/decoders/sflow"
)

// ifIndexUnknown is the sFlow value for "interface internal to the agent / unknown".
const ifIndexUnknown = 0x3FFFFFFF

// FlowSampleInput is the subset of a (compact or expanded) sFlow flow sample
// needed to build a FlowRecord.
type FlowSampleInput struct {
	SampleSequence uint32
	SourceIDType   uint32
	SourceIDIndex  uint32
	SamplingRate   uint32
	InputFormat    uint32
	InputValue     uint32
	OutputFormat   uint32
	OutputValue    uint32
	Records        []sflow.FlowRecord
}

// FromCompact splits the compact interface encoding (2-bit format, 30-bit value).
func FromCompact(v uint32) (format, value uint32) {
	return v >> 30, v & 0x3FFFFFFF
}

// ifIndex returns the ifIndex only when the encoding designates a single,
// known interface (format 0). Discarded (1) and multiple (2) are left null.
func ifIndex(format, value uint32) *uint32 {
	if format != 0 || value == 0 || value == ifIndexUnknown {
		return nil
	}
	return ptr(value)
}

// BuildFlow converts one flow sample into a FlowRecord. The second return
// value is the number of flow records whose format is not supported.
func BuildFlow(meta DatagramMeta, in FlowSampleInput) (FlowRecord, int) {
	rec := FlowRecord{
		Timestamp:      meta.ReceivedAt,
		ExporterIP:     meta.ExporterIP,
		AgentIP:        meta.AgentIP,
		AgentSubID:     meta.SubAgentID,
		SampleSequence: in.SampleSequence,
		SourceIDType:   in.SourceIDType,
		SourceIDIndex:  in.SourceIDIndex,
		InputIfIndex:   ifIndex(in.InputFormat, in.InputValue),
		OutputIfIndex:  ifIndex(in.OutputFormat, in.OutputValue),
		SamplingRate:   in.SamplingRate,
	}

	var (
		pkt       PacketInfo
		frameLen  uint32
		ipLen     uint32
		ethLen    uint32
		extVLAN   *uint16
		ethRec    *sflow.SampledEthernet
		ipRec     *sflow.SampledIPBase
		unsupport int
	)

	for i := range in.Records {
		switch d := in.Records[i].Data.(type) {
		case sflow.SampledHeader:
			pkt = ParseHeader(d.Protocol, d.HeaderData)
			frameLen = d.FrameLength
		case sflow.SampledEthernet:
			ethRec = &d
			ethLen = d.Length
		case sflow.SampledIPv4:
			ipRec = &d.SampledIPBase
			ipLen = d.Length
		case sflow.SampledIPv6:
			ipRec = &d.SampledIPBase
			ipLen = d.Length
		case sflow.ExtendedSwitch:
			if d.SrcVlan != 0 {
				extVLAN = ptr(uint16(d.SrcVlan & 0x0FFF))
			} else if d.DstVlan != 0 {
				extVLAN = ptr(uint16(d.DstVlan & 0x0FFF))
			}
		case sflow.ExtendedRouter, sflow.ExtendedGateway:
			// Known but not used in the MVP.
		default:
			// RawRecord (unknown format) or nil (truncated record).
			unsupport++
		}
	}

	rec.SrcMAC, rec.DstMAC = pkt.SrcMAC, pkt.DstMAC
	rec.EtherType, rec.VLAN = pkt.EtherType, pkt.VLAN
	rec.IPVersion, rec.SrcIP, rec.DstIP = pkt.IPVersion, pkt.SrcIP, pkt.DstIP
	rec.IPProtocol, rec.SrcPort, rec.DstPort, rec.TCPFlags = pkt.IPProtocol, pkt.SrcPort, pkt.DstPort, pkt.TCPFlags

	// Fall back to the decoded (non-header) records when the raw header
	// was absent or too short.
	if rec.SrcMAC == nil && ethRec != nil && len(ethRec.SrcMac) == 6 && len(ethRec.DstMac) == 6 {
		rec.SrcMAC = ptr(formatMAC(ethRec.SrcMac))
		rec.DstMAC = ptr(formatMAC(ethRec.DstMac))
		if rec.EtherType == nil {
			rec.EtherType = ptr(uint16(ethRec.EthType))
		}
	}
	if rec.SrcIP == nil && ipRec != nil {
		if src, ok := netip.AddrFromSlice(ipRec.SrcIP); ok {
			rec.SrcIP = &src
		}
		if dst, ok := netip.AddrFromSlice(ipRec.DstIP); ok {
			rec.DstIP = &dst
		}
		if rec.SrcIP != nil {
			v := uint8(6)
			if rec.SrcIP.Is4() {
				v = 4
			}
			rec.IPVersion = &v
		}
		rec.IPProtocol = ptr(uint8(ipRec.Protocol))
		if ipRec.Protocol == protoTCP || ipRec.Protocol == protoUDP || ipRec.Protocol == protoSCTP {
			rec.SrcPort = ptr(uint16(ipRec.SrcPort))
			rec.DstPort = ptr(uint16(ipRec.DstPort))
		}
		if ipRec.Protocol == protoTCP {
			rec.TCPFlags = ptr(uint8(ipRec.TcpFlags))
		}
	}
	// The 802.1Q tag in the sampled header wins; the extended switch record
	// covers untagged access ports.
	if rec.VLAN == nil && extVLAN != nil {
		rec.VLAN = extVLAN
	}

	switch {
	case frameLen > 0:
		rec.SampledPacketSize = frameLen
	case ipLen > 0:
		rec.SampledPacketSize = ipLen
	default:
		rec.SampledPacketSize = ethLen
	}
	rec.EstimatedBytes, rec.EstimatedPackets = Estimate(rec.SampledPacketSize, in.SamplingRate)
	return rec, unsupport
}

// Estimate scales a single sampled packet by the sampling rate.
// A sampling rate of 0 (invalid, but seen in the wild) is treated as 1.
func Estimate(packetSize, samplingRate uint32) (bytes, packets uint64) {
	rate := uint64(samplingRate)
	if rate == 0 {
		rate = 1
	}
	return uint64(packetSize) * rate, rate
}

// BuildCounters extracts generic interface counters from a counter sample.
// It returns the interface records and the number of unsupported records.
func BuildCounters(meta DatagramMeta, records []sflow.CounterRecord) ([]InterfaceCounters, int) {
	var out []InterfaceCounters
	unsupported := 0
	for i := range records {
		switch d := records[i].Data.(type) {
		case sflow.IfCounters:
			out = append(out, InterfaceCounters{
				Timestamp:   meta.ReceivedAt,
				ExporterIP:  meta.ExporterIP,
				AgentIP:     meta.AgentIP,
				AgentSubID:  meta.SubAgentID,
				IfIndex:     d.IfIndex,
				IfType:      d.IfType,
				SpeedBps:    d.IfSpeed,
				Direction:   d.IfDirection,
				AdminUp:     d.IfStatus&0x1 != 0,
				OperUp:      d.IfStatus&0x2 != 0,
				InOctets:    d.IfInOctets,
				InUcast:     d.IfInUcastPkts,
				InMulticast: d.IfInMulticastPkts,
				InBroadcast: d.IfInBroadcastPkts,
				InDiscards:  d.IfInDiscards,
				InErrors:    d.IfInErrors,
				OutOctets:   d.IfOutOctets,
				OutUcast:    d.IfOutUcastPkts,
				OutMulti:    d.IfOutMulticastPkts,
				OutBcast:    d.IfOutBroadcastPkts,
				OutDiscards: d.IfOutDiscards,
				OutErrors:   d.IfOutErrors,
			})
		case sflow.EthernetCounters:
			// Decoded but not used in the MVP.
		default:
			unsupported++
		}
	}
	return out, unsupported
}
