package sflowgen

import (
	"encoding/binary"
	"net"
	"net/netip"
)

// Frame describes a synthetic Ethernet frame to be used as a sampled header.
type Frame struct {
	SrcMAC, DstMAC   net.HardwareAddr
	VLAN             uint16 // 0 = untagged
	Src, Dst         netip.Addr
	Protocol         uint8 // 6 TCP, 17 UDP, other = no L4 header
	SrcPort, DstPort uint16
	TCPFlags         uint8
	FrameLength      uint32 // original on-wire length (sets IP total length)
}

// Build returns the frame header bytes truncated to maxHeader bytes, as an
// sFlow agent would export them.
func (f Frame) Build(maxHeader int) []byte {
	b := make([]byte, 0, 128)
	b = append(b, f.DstMAC...)
	b = append(b, f.SrcMAC...)
	if f.VLAN != 0 {
		b = binary.BigEndian.AppendUint16(b, 0x8100)
		b = binary.BigEndian.AppendUint16(b, f.VLAN&0x0FFF)
	}
	l2 := len(b) + 2
	l4 := f.l4()

	if f.Src.Is4() {
		b = binary.BigEndian.AppendUint16(b, 0x0800)
		ipLen := int(f.FrameLength) - l2
		if ipLen < 20+len(l4) {
			ipLen = 20 + len(l4)
		}
		ip := make([]byte, 20)
		ip[0] = 0x45
		binary.BigEndian.PutUint16(ip[2:4], uint16(ipLen))
		ip[8] = 64
		ip[9] = f.Protocol
		s, d := f.Src.As4(), f.Dst.As4()
		copy(ip[12:16], s[:])
		copy(ip[16:20], d[:])
		b = append(b, ip...)
	} else {
		b = binary.BigEndian.AppendUint16(b, 0x86DD)
		payload := int(f.FrameLength) - l2 - 40
		if payload < len(l4) {
			payload = len(l4)
		}
		ip := make([]byte, 40)
		ip[0] = 0x60
		binary.BigEndian.PutUint16(ip[4:6], uint16(payload))
		ip[6] = f.Protocol
		ip[7] = 64
		s, d := f.Src.As16(), f.Dst.As16()
		copy(ip[8:24], s[:])
		copy(ip[24:40], d[:])
		b = append(b, ip...)
	}
	b = append(b, l4...)
	// Pad with zero payload up to the frame length, then truncate.
	for len(b) < int(f.FrameLength) && len(b) < maxHeader {
		b = append(b, 0)
	}
	if len(b) > maxHeader {
		b = b[:maxHeader]
	}
	return b
}

func (f Frame) l4() []byte {
	switch f.Protocol {
	case 6:
		t := make([]byte, 20)
		binary.BigEndian.PutUint16(t[0:2], f.SrcPort)
		binary.BigEndian.PutUint16(t[2:4], f.DstPort)
		t[12] = 5 << 4
		t[13] = f.TCPFlags
		return t
	case 17:
		u := make([]byte, 8)
		binary.BigEndian.PutUint16(u[0:2], f.SrcPort)
		binary.BigEndian.PutUint16(u[2:4], f.DstPort)
		return u
	}
	return nil
}
