package normalize

import (
	"encoding/binary"
	"net"
	"net/netip"
	"testing"

	"sflow-analytics/collector/internal/sflowgen"
)

var (
	macSrc = net.HardwareAddr{0x00, 0x11, 0x22, 0x33, 0x44, 0x55}
	macDst = net.HardwareAddr{0x00, 0xaa, 0xbb, 0xcc, 0xdd, 0xee}
)

func frame(vlan uint16, src, dst string, proto uint8, sport, dport uint16) sflowgen.Frame {
	return sflowgen.Frame{
		SrcMAC: macSrc, DstMAC: macDst, VLAN: vlan,
		Src: netip.MustParseAddr(src), Dst: netip.MustParseAddr(dst),
		Protocol: proto, SrcPort: sport, DstPort: dport, TCPFlags: 0x12, FrameLength: 1000,
	}
}

func TestParseHeaderIPv4TCP(t *testing.T) {
	p := ParseHeader(HeaderProtoEthernet, frame(0, "10.1.1.1", "10.2.2.2", 6, 1234, 443).Build(128))
	if p.VLAN != nil {
		t.Errorf("VLAN = %d, want nil", *p.VLAN)
	}
	assertEq(t, "src_mac", *p.SrcMAC, "00:11:22:33:44:55")
	assertEq(t, "dst_mac", *p.DstMAC, "00:aa:bb:cc:dd:ee")
	assertEq(t, "ether_type", *p.EtherType, uint16(0x0800))
	assertEq(t, "ip_version", *p.IPVersion, uint8(4))
	assertEq(t, "src_ip", p.SrcIP.String(), "10.1.1.1")
	assertEq(t, "dst_ip", p.DstIP.String(), "10.2.2.2")
	assertEq(t, "proto", *p.IPProtocol, uint8(6))
	assertEq(t, "sport", *p.SrcPort, uint16(1234))
	assertEq(t, "dport", *p.DstPort, uint16(443))
	assertEq(t, "tcp_flags", *p.TCPFlags, uint8(0x12))
}

func TestParseHeaderIPv4UDPWithVLAN(t *testing.T) {
	p := ParseHeader(HeaderProtoEthernet, frame(120, "10.1.1.1", "10.2.2.2", 17, 5353, 53).Build(128))
	assertEq(t, "vlan", *p.VLAN, uint16(120))
	assertEq(t, "ether_type", *p.EtherType, uint16(0x0800))
	assertEq(t, "proto", *p.IPProtocol, uint8(17))
	assertEq(t, "dport", *p.DstPort, uint16(53))
	if p.TCPFlags != nil {
		t.Error("UDP must not have TCP flags")
	}
}

func TestParseHeaderQinQKeepsOuterVLAN(t *testing.T) {
	inner := frame(200, "10.1.1.1", "10.2.2.2", 17, 1, 2).Build(128)
	// Insert an outer 802.1ad tag (VLAN 3000) in front of the 802.1Q one.
	b := append([]byte{}, inner[:12]...)
	b = binary.BigEndian.AppendUint16(b, 0x88A8)
	b = binary.BigEndian.AppendUint16(b, 3000)
	b = append(b, inner[12:]...)
	p := ParseHeader(HeaderProtoEthernet, b)
	assertEq(t, "vlan", *p.VLAN, uint16(3000))
	assertEq(t, "dport", *p.DstPort, uint16(2))
}

func TestParseHeaderIPv6(t *testing.T) {
	p := ParseHeader(HeaderProtoEthernet, frame(0, "2001:db8::1", "2001:db8::2", 6, 40000, 22).Build(128))
	assertEq(t, "ether_type", *p.EtherType, uint16(0x86DD))
	assertEq(t, "ip_version", *p.IPVersion, uint8(6))
	assertEq(t, "src_ip", p.SrcIP.String(), "2001:db8::1")
	assertEq(t, "dport", *p.DstPort, uint16(22))
}

func TestParseHeaderIPv6ExtensionHeader(t *testing.T) {
	b := frame(0, "2001:db8::1", "2001:db8::2", 0, 0, 0).Build(128)
	ip := b[14:]
	ip[6] = 60                             // destination options header
	ext := []byte{17, 0, 0, 0, 0, 0, 0, 0} // next = UDP, length 8
	udp := []byte{0x1f, 0x90, 0x00, 0x35, 0, 8, 0, 0}
	b = append(append(b[:54], ext...), udp...)
	p := ParseHeader(HeaderProtoEthernet, b)
	assertEq(t, "proto", *p.IPProtocol, uint8(17))
	assertEq(t, "sport", *p.SrcPort, uint16(8080))
	assertEq(t, "dport", *p.DstPort, uint16(53))
}

func TestParseHeaderLLCFrameHasNoEtherType(t *testing.T) {
	// STP BPDU as sampled on an ICX: 802.3 length 0x0039, LLC 42/42/03.
	b := []byte{0x01, 0x80, 0xc2, 0, 0, 0, 0x50, 0xa7, 0x33, 0x54, 0xc6, 0x90, 0x00, 0x39, 0x42, 0x42, 0x03}
	p := ParseHeader(HeaderProtoEthernet, b)
	if p.EtherType != nil || p.IPVersion != nil {
		t.Errorf("ether_type=%v ip_version=%v, want nil", p.EtherType, p.IPVersion)
	}
	assertEq(t, "dst_mac", *p.DstMAC, "01:80:c2:00:00:00")
}

func TestParseHeaderIPv4NonFirstFragmentHasNoPorts(t *testing.T) {
	b := frame(0, "10.1.1.1", "10.2.2.2", 17, 1, 2).Build(128)
	binary.BigEndian.PutUint16(b[14+6:14+8], 185) // fragment offset != 0
	p := ParseHeader(HeaderProtoEthernet, b)
	if p.SrcPort != nil || p.DstPort != nil {
		t.Error("non-first fragment must not expose ports")
	}
	assertEq(t, "src_ip", p.SrcIP.String(), "10.1.1.1")
}

func TestParseHeaderRawIPv4Protocol(t *testing.T) {
	b := frame(0, "10.1.1.1", "10.2.2.2", 6, 1, 443).Build(128)[14:]
	p := ParseHeader(HeaderProtoIPv4, b)
	assertEq(t, "dport", *p.DstPort, uint16(443))
	if p.SrcMAC != nil {
		t.Error("raw IP header has no MAC")
	}
}

func TestParseHeaderTruncatedNeverPanics(t *testing.T) {
	full := frame(120, "10.1.1.1", "10.2.2.2", 6, 1, 443).Build(128)
	for n := 0; n <= len(full); n++ {
		p := ParseHeader(HeaderProtoEthernet, full[:n])
		if n < 14 && p.SrcMAC != nil {
			t.Fatalf("len %d: MAC decoded from short header", n)
		}
	}
	full6 := frame(0, "2001:db8::1", "2001:db8::2", 17, 1, 2).Build(128)
	for n := 0; n <= len(full6); n++ {
		ParseHeader(HeaderProtoEthernet, full6[:n])
	}
	ParseHeader(99, full) // unknown header protocol
}

func assertEq[T comparable](t *testing.T, name string, got, want T) {
	t.Helper()
	if got != want {
		t.Errorf("%s = %v, want %v", name, got, want)
	}
}
