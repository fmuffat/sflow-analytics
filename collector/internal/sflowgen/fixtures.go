package sflowgen

import (
	"encoding/binary"
	"net"
	"net/netip"
)

// Fixture is a named, deterministic test datagram.
type Fixture struct {
	Name        string
	Description string
	Data        []byte
}

var (
	agentA = netip.MustParseAddr("10.0.0.10")
	macA   = net.HardwareAddr{0x00, 0x11, 0x22, 0x33, 0x44, 0x55}
	macB   = net.HardwareAddr{0x00, 0xaa, 0xbb, 0xcc, 0xdd, 0xee}
)

func httpsFlow(seq uint32) FlowSample {
	f := Frame{
		SrcMAC: macA, DstMAC: macB, VLAN: 120,
		Src: netip.MustParseAddr("10.20.30.42"), Dst: netip.MustParseAddr("10.40.50.20"),
		Protocol: 6, SrcPort: 53214, DstPort: 443, TCPFlags: 0x18, FrameLength: 1514,
	}
	return FlowSample{
		Sequence: seq, SourceIDIndex: 24, SamplingRate: 1024, SamplePool: 1024 * seq,
		Input: CompactIf(24), Output: CompactIf(48),
		Records: []Record{SampledHeader{Protocol: 1, FrameLength: 1514, Stripped: 4, Header: f.Build(128)}},
	}
}

func dnsFlowExtSwitch(seq uint32) FlowSample {
	f := Frame{
		SrcMAC: macB, DstMAC: macA,
		Src: netip.MustParseAddr("10.20.30.43"), Dst: netip.MustParseAddr("10.40.50.53"),
		Protocol: 17, SrcPort: 40000, DstPort: 53, FrameLength: 86,
	}
	return FlowSample{
		Sequence: seq, SourceIDIndex: 12, SamplingRate: 512, SamplePool: 512 * seq,
		Input: CompactIf(12), Output: CompactIf(48),
		Records: []Record{
			SampledHeader{Protocol: 1, FrameLength: 86, Stripped: 4, Header: f.Build(128)},
			ExtendedSwitch{SrcVLAN: 20, DstVLAN: 20},
		},
	}
}

func ipv6Flow(seq uint32) FlowSample {
	f := Frame{
		SrcMAC: macA, DstMAC: macB, VLAN: 30,
		Src: netip.MustParseAddr("2001:db8:10::42"), Dst: netip.MustParseAddr("2001:db8:40::20"),
		Protocol: 17, SrcPort: 51000, DstPort: 443, FrameLength: 1294,
	}
	return FlowSample{
		Sequence: seq, SourceIDIndex: 5, SamplingRate: 2048, SamplePool: 2048 * seq,
		Input: CompactIf(5), Output: CompactIf(48),
		Records: []Record{SampledHeader{Protocol: 1, FrameLength: 1294, Stripped: 4, Header: f.Build(128)}},
	}
}

func counters(seq, ifIndex uint32) CounterSample {
	return CounterSample{
		Sequence: seq, SourceIDIndex: ifIndex,
		Records: []Record{IfCounters{
			IfIndex: ifIndex, IfType: 6, IfSpeed: 10_000_000_000, IfDirection: 1, IfStatus: 3,
			InOctets: 123456789, OutOctets: 987654321, InUcast: 1000, OutUcast: 2000, InErrors: 1,
		}},
	}
}

// Fixtures returns the canonical test datagrams written to collector/tests/fixtures.
func Fixtures() []Fixture {
	base := func(seq uint32, samples ...Sample) []byte {
		return Datagram{AgentIP: agentA, Sequence: seq, UptimeMs: 3600000, Samples: samples}.Marshal()
	}
	multi := base(4, httpsFlow(10), dnsFlowExtSwitch(11), ipv6Flow(12), counters(1, 48))
	unsupportedVersion := make([]byte, 28)
	binary.BigEndian.PutUint32(unsupportedVersion[0:4], 4)

	return []Fixture{
		{"ipv4_tcp_https_vlan", "IPv4 TCP/443, 802.1Q VLAN 120 in header, ifIndex 24 -> 48", base(1, httpsFlow(1))},
		{"ipv4_udp_dns_extswitch", "IPv4 UDP/53, untagged header, VLAN 20 from extended switch record", base(2, dnsFlowExtSwitch(2))},
		{"ipv6_udp", "IPv6 UDP/443 (QUIC-like), VLAN 30", base(3, ipv6Flow(3))},
		{"multi_sample", "3 flow samples + 1 counter sample in one datagram", multi},
		{"expanded_flow", "expanded flow sample (format 3), ifIndex 1001 -> 2002", base(5, ExpandedFlowSample{
			Sequence: 20, SourceIDIndex: 1001, SamplingRate: 4096, SamplePool: 4096,
			InputValue: 1001, OutputValue: 2002, Records: httpsFlow(20).Records,
		})},
		{"counters_only", "counter sample with generic interface counters", base(6, counters(2, 24))},
		{"unknown_sample_and_record", "unknown sample format 99 and unknown flow record 4242 next to a valid flow", base(7,
			RawSample{Format: 99, Data: []byte{1, 2, 3, 4}},
			FlowSample{
				Sequence: 30, SourceIDIndex: 24, SamplingRate: 1024, SamplePool: 1024,
				Input: CompactIf(24), Output: CompactIf(48),
				Records: append([]Record{RawRecord{Format: 4242, Data: []byte{0, 0, 0, 1}}}, httpsFlow(30).Records...),
			})},
		{"malformed_truncated", "multi_sample cut in the middle of the second sample", multi[:len(multi)/2]},
		{"unsupported_version", "sFlow v4 header", unsupportedVersion},
	}
}
