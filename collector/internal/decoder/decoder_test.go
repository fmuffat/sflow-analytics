package decoder

import (
	"encoding/binary"
	"errors"
	"net/netip"
	"testing"
	"time"

	"sflow-analytics/collector/internal/normalize"
	"sflow-analytics/collector/internal/sflowgen"
)

var (
	exporter = netip.MustParseAddr("192.0.2.10")
	now      = time.Date(2026, 9, 28, 14, 20, 31, 0, time.UTC)
)

func fixture(t *testing.T, name string) []byte {
	t.Helper()
	for _, f := range sflowgen.Fixtures() {
		if f.Name == name {
			return f.Data
		}
	}
	t.Fatalf("no fixture %q", name)
	return nil
}

func decodeOK(t *testing.T, data []byte) Result {
	t.Helper()
	res, err := Decode(data, exporter, now)
	if err != nil {
		t.Fatalf("Decode: %v", err)
	}
	return res
}

func TestValidDatagramIPv4TCP(t *testing.T) {
	res := decodeOK(t, fixture(t, "ipv4_tcp_https_vlan"))
	if res.Version != 5 || res.Samples != 1 || res.FlowSamples != 1 || len(res.Flows) != 1 {
		t.Fatalf("unexpected result: %+v", res)
	}
	if res.Meta.AgentIP.String() != "10.0.0.10" || res.Meta.ExporterIP != exporter || res.Meta.SequenceNumber != 1 {
		t.Errorf("meta = %+v", res.Meta)
	}
	f := res.Flows[0]
	checks := []struct {
		name      string
		got, want any
	}{
		{"timestamp", f.Timestamp, now},
		{"src_ip", f.SrcIP.String(), "10.20.30.42"},
		{"dst_ip", f.DstIP.String(), "10.40.50.20"},
		{"src_mac", *f.SrcMAC, "00:11:22:33:44:55"},
		{"dst_mac", *f.DstMAC, "00:aa:bb:cc:dd:ee"},
		{"proto", *f.IPProtocol, uint8(6)},
		{"src_port", *f.SrcPort, uint16(53214)},
		{"dst_port", *f.DstPort, uint16(443)},
		{"vlan", *f.VLAN, uint16(120)},
		{"input_ifindex", *f.InputIfIndex, uint32(24)},
		{"output_ifindex", *f.OutputIfIndex, uint32(48)},
		{"sampled_packet_size", f.SampledPacketSize, uint32(1514)},
		{"sampling_rate", f.SamplingRate, uint32(1024)},
		{"estimated_bytes", f.EstimatedBytes, uint64(1514 * 1024)},
		{"estimated_packets", f.EstimatedPackets, uint64(1024)},
	}
	for _, c := range checks {
		if c.got != c.want {
			t.Errorf("%s = %v, want %v", c.name, c.got, c.want)
		}
	}
}

func TestUDPAndVLANFromExtendedSwitch(t *testing.T) {
	f := decodeOK(t, fixture(t, "ipv4_udp_dns_extswitch")).Flows[0]
	if *f.IPProtocol != 17 || *f.DstPort != 53 {
		t.Errorf("proto/port = %d/%d", *f.IPProtocol, *f.DstPort)
	}
	if f.VLAN == nil || *f.VLAN != 20 {
		t.Errorf("vlan = %v, want 20 from extended switch record", f.VLAN)
	}
	if f.TCPFlags != nil {
		t.Error("UDP flow has TCP flags")
	}
}

func TestIPv6Flow(t *testing.T) {
	f := decodeOK(t, fixture(t, "ipv6_udp")).Flows[0]
	if *f.IPVersion != 6 || f.SrcIP.String() != "2001:db8:10::42" || *f.VLAN != 30 {
		t.Errorf("ipv6 flow = %+v", f)
	}
	if f.EstimatedBytes != 1294*2048 {
		t.Errorf("estimated_bytes = %d", f.EstimatedBytes)
	}
}

func TestMultipleSamples(t *testing.T) {
	res := decodeOK(t, fixture(t, "multi_sample"))
	if res.Samples != 4 || res.FlowSamples != 3 || res.CounterSamples != 1 {
		t.Fatalf("samples=%d flows=%d counters=%d", res.Samples, res.FlowSamples, res.CounterSamples)
	}
	if len(res.Flows) != 3 || len(res.Counters) != 1 {
		t.Fatalf("records: %d flows, %d counters", len(res.Flows), len(res.Counters))
	}
	c := res.Counters[0]
	if c.IfIndex != 48 || c.SpeedBps != 10_000_000_000 || !c.AdminUp || !c.OperUp || c.InOctets != 123456789 || c.InErrors != 1 {
		t.Errorf("counters = %+v", c)
	}
}

func TestExpandedFlowSample(t *testing.T) {
	f := decodeOK(t, fixture(t, "expanded_flow")).Flows[0]
	if *f.InputIfIndex != 1001 || *f.OutputIfIndex != 2002 || f.SamplingRate != 4096 {
		t.Errorf("expanded flow = in %v out %v rate %d", f.InputIfIndex, f.OutputIfIndex, f.SamplingRate)
	}
}

func TestInterfaceIndexEncodings(t *testing.T) {
	cases := []struct {
		name   string
		output uint32
		want   *uint32
	}{
		{"single", sflowgen.CompactIf(48), u32(48)},
		{"discarded", 1<<30 | 5, nil},
		{"multiple", 2<<30 | 3, nil},
		{"internal", 0x3FFFFFFF, nil},
		{"unknown zero", 0, nil},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			fs := sflowgen.FlowSample{
				Sequence: 1, SourceIDIndex: 7, SamplingRate: 100,
				Input: sflowgen.CompactIf(7), Output: c.output,
				Records: []sflowgen.Record{sflowgen.SampledIPv4{Length: 100, Protocol: 6,
					Src: netip.MustParseAddr("10.0.0.1"), Dst: netip.MustParseAddr("10.0.0.2"), SrcPort: 1, DstPort: 2}},
			}
			f := decodeOK(t, sflowgen.Datagram{AgentIP: exporter, Samples: []sflowgen.Sample{fs}}.Marshal()).Flows[0]
			if *f.InputIfIndex != 7 {
				t.Errorf("input = %v", f.InputIfIndex)
			}
			if (f.OutputIfIndex == nil) != (c.want == nil) || (c.want != nil && *f.OutputIfIndex != *c.want) {
				t.Errorf("output = %v, want %v", f.OutputIfIndex, c.want)
			}
		})
	}
}

func TestSampledIPv4RecordWithoutHeader(t *testing.T) {
	fs := sflowgen.FlowSample{
		Sequence: 1, SamplingRate: 256, Input: sflowgen.CompactIf(1), Output: sflowgen.CompactIf(2),
		Records: []sflowgen.Record{sflowgen.SampledIPv4{Length: 600, Protocol: 17,
			Src: netip.MustParseAddr("10.9.9.9"), Dst: netip.MustParseAddr("10.8.8.8"), SrcPort: 123, DstPort: 123}},
	}
	f := decodeOK(t, sflowgen.Datagram{AgentIP: exporter, Samples: []sflowgen.Sample{fs}}.Marshal()).Flows[0]
	if f.SrcIP.String() != "10.9.9.9" || *f.DstPort != 123 || *f.IPVersion != 4 || f.EstimatedBytes != 600*256 {
		t.Errorf("flow = %+v", f)
	}
	if f.SrcMAC != nil {
		t.Error("no MAC expected")
	}
}

func TestSamplingCalculation(t *testing.T) {
	cases := []struct {
		size, rate     uint32
		bytes, packets uint64
	}{
		{1514, 1024, 1550336, 1024},
		{64, 1, 64, 1},
		{64, 0, 64, 1}, // invalid rate 0 is treated as 1
		{9000, 1 << 20, 9000 << 20, 1 << 20},
	}
	for _, c := range cases {
		b, p := normalize.Estimate(c.size, c.rate)
		if b != c.bytes || p != c.packets {
			t.Errorf("Estimate(%d,%d) = %d,%d want %d,%d", c.size, c.rate, b, p, c.bytes, c.packets)
		}
	}
}

func TestUnknownSampleAndRecordAreSkipped(t *testing.T) {
	res := decodeOK(t, fixture(t, "unknown_sample_and_record"))
	if res.UnsupportedSamples != 1 || res.UnsupportedRecords != 1 {
		t.Errorf("unsupported samples=%d records=%d", res.UnsupportedSamples, res.UnsupportedRecords)
	}
	if len(res.Flows) != 1 || *res.Flows[0].DstPort != 443 {
		t.Fatalf("the valid flow next to unknown data must still be decoded: %+v", res.Flows)
	}
}

func TestUnsupportedVersion(t *testing.T) {
	_, err := Decode(fixture(t, "unsupported_version"), exporter, now)
	if !errors.Is(err, ErrUnsupportedVersion) {
		t.Fatalf("err = %v, want ErrUnsupportedVersion", err)
	}
}

func TestMalformedTruncatedKeepsCompleteSamples(t *testing.T) {
	res, err := Decode(fixture(t, "malformed_truncated"), exporter, now)
	if err != nil {
		t.Fatalf("Decode: %v", err)
	}
	if !res.Truncated {
		t.Error("Truncated not set")
	}
	if len(res.Flows) != 1 {
		t.Errorf("flows = %d, want the 1 complete sample before the cut", len(res.Flows))
	}
}

func TestEveryTruncationIsSafe(t *testing.T) {
	for _, fx := range sflowgen.Fixtures() {
		for n := 0; n <= len(fx.Data); n++ {
			func() {
				defer func() {
					if p := recover(); p != nil {
						t.Fatalf("%s truncated to %d bytes: panic %v", fx.Name, n, p)
					}
				}()
				_, _ = Decode(fx.Data[:n], exporter, now)
			}()
		}
	}
}

func TestShortAndGarbageInput(t *testing.T) {
	for _, b := range [][]byte{nil, {0}, {0, 0, 0, 5}, {0, 0, 0, 5, 0, 0, 0, 9, 1, 2, 3, 4}} {
		if _, err := Decode(b, exporter, now); err == nil {
			t.Errorf("Decode(%v) succeeded", b)
		}
	}
}

func TestHostileCounts(t *testing.T) {
	// A datagram announcing 1e9 samples.
	d := sflowgen.Datagram{AgentIP: exporter}.Marshal()
	binary.BigEndian.PutUint32(d[len(d)-4:], 1_000_000_000)
	if _, err := Decode(d, exporter, now); !errors.Is(err, ErrMalformed) {
		t.Errorf("hostile sample count: err = %v", err)
	}

	// An expanded flow sample announcing 4 billion records in a tiny body.
	body := make([]byte, 44)
	binary.BigEndian.PutUint32(body[40:44], 0xFFFFFFFF)
	d = sflowgen.Datagram{AgentIP: exporter, Samples: []sflowgen.Sample{sflowgen.RawSample{Format: 3, Data: body}}}.Marshal()
	res := decodeOK(t, d)
	if res.SampleErrors != 1 || len(res.Flows) != 0 {
		t.Errorf("hostile record count: %+v", res)
	}
}

func TestIPv6AgentAddress(t *testing.T) {
	agent := netip.MustParseAddr("2001:db8::10")
	d := sflowgen.Datagram{AgentIP: agent, SubAgentID: 3, Sequence: 99}.Marshal()
	res := decodeOK(t, d)
	if res.Meta.AgentIP != agent || res.Meta.SubAgentID != 3 || res.Meta.SequenceNumber != 99 {
		t.Errorf("meta = %+v", res.Meta)
	}
}

func FuzzDecode(f *testing.F) {
	for _, fx := range sflowgen.Fixtures() {
		f.Add(fx.Data)
	}
	f.Fuzz(func(t *testing.T, data []byte) {
		res, err := Decode(data, exporter, now)
		if err == nil && len(res.Flows) != res.FlowSamples {
			t.Fatalf("flows %d != flow samples %d", len(res.Flows), res.FlowSamples)
		}
	})
}

func u32(v uint32) *uint32 { return &v }
