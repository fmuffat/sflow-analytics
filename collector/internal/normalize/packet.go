package normalize

import (
	"encoding/binary"
	"fmt"
	"net/netip"
)

// sFlow sampled_header "header_protocol" values we understand.
const (
	HeaderProtoEthernet = 1
	HeaderProtoIPv4     = 11
	HeaderProtoIPv6     = 12
)

const (
	etherTypeIPv4   = 0x0800
	etherTypeIPv6   = 0x86DD
	etherTypeVLAN   = 0x8100
	etherTypeQinQ   = 0x88A8
	etherTypeQinQv1 = 0x9100

	protoICMP   = 1
	protoTCP    = 6
	protoUDP    = 17
	protoICMPv6 = 58
	protoSCTP   = 132
)

// PacketInfo holds what could be extracted from a sampled packet header.
// Headers are frequently truncated by the exporter (typically 128 bytes),
// so every field is optional.
type PacketInfo struct {
	SrcMAC, DstMAC *string
	EtherType      *uint16
	VLAN           *uint16 // outermost 802.1Q/802.1ad tag

	IPVersion  *uint8
	SrcIP      *netip.Addr
	DstIP      *netip.Addr
	IPProtocol *uint8
	SrcPort    *uint16
	DstPort    *uint16
	TCPFlags   *uint8
}

// ParseHeader decodes a sampled packet header according to the sFlow
// header_protocol value. It never panics on short or garbage input.
func ParseHeader(protocol uint32, data []byte) PacketInfo {
	var p PacketInfo
	switch protocol {
	case HeaderProtoEthernet:
		p.parseEthernet(data)
	case HeaderProtoIPv4:
		p.EtherType = ptr[uint16](etherTypeIPv4)
		p.parseIPv4(data)
	case HeaderProtoIPv6:
		p.EtherType = ptr[uint16](etherTypeIPv6)
		p.parseIPv6(data)
	}
	return p
}

func formatMAC(b []byte) string {
	return fmt.Sprintf("%02x:%02x:%02x:%02x:%02x:%02x", b[0], b[1], b[2], b[3], b[4], b[5])
}

func (p *PacketInfo) parseEthernet(b []byte) {
	if len(b) < 14 {
		return
	}
	p.DstMAC = ptr(formatMAC(b[0:6]))
	p.SrcMAC = ptr(formatMAC(b[6:12]))
	etype := binary.BigEndian.Uint16(b[12:14])
	off := 14
	// Up to two stacked tags (QinQ); keep the outermost VLAN ID.
	for i := 0; i < 2 && (etype == etherTypeVLAN || etype == etherTypeQinQ || etype == etherTypeQinQv1); i++ {
		if len(b) < off+4 {
			return
		}
		if p.VLAN == nil {
			p.VLAN = ptr(binary.BigEndian.Uint16(b[off:off+2]) & 0x0FFF)
		}
		etype = binary.BigEndian.Uint16(b[off+2 : off+4])
		off += 4
	}
	// Values below 0x0600 are an 802.3 length (LLC/SNAP frames such as STP
	// BPDUs), not an EtherType.
	if etype < 0x0600 {
		return
	}
	p.EtherType = ptr(etype)
	switch etype {
	case etherTypeIPv4:
		p.parseIPv4(b[off:])
	case etherTypeIPv6:
		p.parseIPv6(b[off:])
	}
}

func (p *PacketInfo) parseIPv4(b []byte) {
	if len(b) < 20 || b[0]>>4 != 4 {
		return
	}
	ihl := int(b[0]&0x0F) * 4
	if ihl < 20 {
		return
	}
	p.IPVersion = ptr[uint8](4)
	proto := b[9]
	p.IPProtocol = ptr(proto)
	src := netip.AddrFrom4([4]byte(b[12:16]))
	dst := netip.AddrFrom4([4]byte(b[16:20]))
	p.SrcIP, p.DstIP = &src, &dst

	// Only the first fragment carries the L4 header.
	if binary.BigEndian.Uint16(b[6:8])&0x1FFF != 0 || len(b) < ihl {
		return
	}
	p.parseL4(proto, b[ihl:])
}

func (p *PacketInfo) parseIPv6(b []byte) {
	if len(b) < 40 || b[0]>>4 != 6 {
		return
	}
	p.IPVersion = ptr[uint8](6)
	src := netip.AddrFrom16([16]byte(b[8:24]))
	dst := netip.AddrFrom16([16]byte(b[24:40]))
	p.SrcIP, p.DstIP = &src, &dst

	next := b[6]
	rest := b[40:]
	// Walk common extension headers to reach the transport header.
	for i := 0; i < 8; i++ {
		switch next {
		case 0, 43, 60: // hop-by-hop, routing, destination options
			if len(rest) < 8 {
				p.IPProtocol = ptr(next)
				return
			}
			l := (int(rest[1]) + 1) * 8
			if len(rest) < l {
				p.IPProtocol = ptr(next)
				return
			}
			next, rest = rest[0], rest[l:]
		case 44: // fragment
			if len(rest) < 8 {
				p.IPProtocol = ptr(next)
				return
			}
			fragOff := binary.BigEndian.Uint16(rest[2:4]) >> 3
			next, rest = rest[0], rest[8:]
			if fragOff != 0 {
				p.IPProtocol = ptr(next)
				return
			}
		default:
			p.IPProtocol = ptr(next)
			p.parseL4(next, rest)
			return
		}
	}
	p.IPProtocol = ptr(next)
}

func (p *PacketInfo) parseL4(proto uint8, b []byte) {
	switch proto {
	case protoTCP:
		if len(b) < 4 {
			return
		}
		p.SrcPort = ptr(binary.BigEndian.Uint16(b[0:2]))
		p.DstPort = ptr(binary.BigEndian.Uint16(b[2:4]))
		if len(b) >= 14 {
			p.TCPFlags = ptr(b[13])
		}
	case protoUDP, protoSCTP:
		if len(b) < 4 {
			return
		}
		p.SrcPort = ptr(binary.BigEndian.Uint16(b[0:2]))
		p.DstPort = ptr(binary.BigEndian.Uint16(b[2:4]))
	}
}
