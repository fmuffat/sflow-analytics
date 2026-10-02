// Package pcap reads sFlow datagrams out of classic libpcap capture files
// (as written by `tcpdump -w`), so that real exporter traffic can be replayed.
// pcapng is not supported; convert with `editcap -F pcap in.pcapng out.pcap`.
package pcap

import (
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"net/netip"
	"time"
)

// Datagram is one UDP payload extracted from a capture.
type Datagram struct {
	Time    time.Time
	Src     netip.Addr
	DstPort uint16
	Payload []byte
}

const (
	linkEthernet = 1
	linkRaw      = 101
	linkSLL      = 113
	linkSLL2     = 276
)

// ErrNotPcap is returned for files that are not classic pcap.
var ErrNotPcap = errors.New("not a classic pcap file (pcapng is not supported)")

// Read returns every UDP datagram sent to dstPort (0 = any port).
func Read(r io.Reader, dstPort uint16) ([]Datagram, error) {
	hdr := make([]byte, 24)
	if _, err := io.ReadFull(r, hdr); err != nil {
		return nil, fmt.Errorf("pcap header: %w", err)
	}
	var bo binary.ByteOrder
	nano := false
	switch binary.LittleEndian.Uint32(hdr[0:4]) {
	case 0xa1b2c3d4:
		bo = binary.LittleEndian
	case 0xa1b23c4d:
		bo, nano = binary.LittleEndian, true
	case 0xd4c3b2a1:
		bo = binary.BigEndian
	case 0x4d3cb2a1:
		bo, nano = binary.BigEndian, true
	default:
		return nil, ErrNotPcap
	}
	link := bo.Uint32(hdr[20:24]) & 0x0FFFFFFF

	var out []Datagram
	rec := make([]byte, 16)
	for {
		if _, err := io.ReadFull(r, rec); err != nil {
			if errors.Is(err, io.EOF) {
				return out, nil
			}
			return out, fmt.Errorf("pcap record header: %w", err)
		}
		sec, frac := bo.Uint32(rec[0:4]), bo.Uint32(rec[4:8])
		capLen := bo.Uint32(rec[8:12])
		if capLen > 262144 {
			return out, fmt.Errorf("pcap record too large: %d", capLen)
		}
		pkt := make([]byte, capLen)
		if _, err := io.ReadFull(r, pkt); err != nil {
			return out, fmt.Errorf("pcap record: %w", err)
		}
		ns := int64(frac) * 1000
		if nano {
			ns = int64(frac)
		}
		ts := time.Unix(int64(sec), ns).UTC()
		if d, ok := extractUDP(link, pkt); ok && (dstPort == 0 || d.DstPort == dstPort) {
			d.Time = ts
			out = append(out, d)
		}
	}
}

func extractUDP(link uint32, b []byte) (Datagram, bool) {
	var etype uint16
	switch link {
	case linkEthernet:
		if len(b) < 14 {
			return Datagram{}, false
		}
		etype, b = binary.BigEndian.Uint16(b[12:14]), b[14:]
		for etype == 0x8100 || etype == 0x88A8 {
			if len(b) < 4 {
				return Datagram{}, false
			}
			etype, b = binary.BigEndian.Uint16(b[2:4]), b[4:]
		}
	case linkSLL:
		if len(b) < 16 {
			return Datagram{}, false
		}
		etype, b = binary.BigEndian.Uint16(b[14:16]), b[16:]
	case linkSLL2:
		if len(b) < 20 {
			return Datagram{}, false
		}
		etype, b = binary.BigEndian.Uint16(b[0:2]), b[20:]
	case linkRaw:
		if len(b) == 0 {
			return Datagram{}, false
		}
		etype = 0x0800
		if b[0]>>4 == 6 {
			etype = 0x86DD
		}
	default:
		return Datagram{}, false
	}

	var src netip.Addr
	var l4 []byte
	switch etype {
	case 0x0800:
		if len(b) < 20 || b[9] != 17 {
			return Datagram{}, false
		}
		ihl := int(b[0]&0x0F) * 4
		if ihl < 20 || len(b) < ihl || binary.BigEndian.Uint16(b[6:8])&0x3FFF != 0 {
			return Datagram{}, false // fragments are not reassembled
		}
		src = netip.AddrFrom4([4]byte(b[12:16]))
		l4 = b[ihl:]
	case 0x86DD:
		if len(b) < 40 || b[6] != 17 {
			return Datagram{}, false
		}
		src = netip.AddrFrom16([16]byte(b[8:24]))
		l4 = b[40:]
	default:
		return Datagram{}, false
	}
	if len(l4) < 8 {
		return Datagram{}, false
	}
	udpLen := int(binary.BigEndian.Uint16(l4[4:6]))
	if udpLen < 8 || udpLen > len(l4) {
		return Datagram{}, false // truncated capture (use tcpdump -s 0)
	}
	payload := make([]byte, udpLen-8)
	copy(payload, l4[8:udpLen])
	return Datagram{Src: src, DstPort: binary.BigEndian.Uint16(l4[2:4]), Payload: payload}, true
}

// Write writes datagrams as a classic pcap (Ethernet/IPv4/UDP) file.
// Used to produce test fixtures.
func Write(w io.Writer, dgs []Datagram) error {
	hdr := make([]byte, 24)
	binary.LittleEndian.PutUint32(hdr[0:4], 0xa1b2c3d4)
	binary.LittleEndian.PutUint16(hdr[4:6], 2)
	binary.LittleEndian.PutUint16(hdr[6:8], 4)
	binary.LittleEndian.PutUint32(hdr[16:20], 65535)
	binary.LittleEndian.PutUint32(hdr[20:24], linkEthernet)
	if _, err := w.Write(hdr); err != nil {
		return err
	}
	for _, d := range dgs {
		src := d.Src.As4()
		frame := make([]byte, 14+20+8+len(d.Payload))
		copy(frame[0:6], []byte{0x02, 0, 0, 0, 0, 2})
		copy(frame[6:12], []byte{0x02, 0, 0, 0, 0, 1})
		binary.BigEndian.PutUint16(frame[12:14], 0x0800)
		ip := frame[14:34]
		ip[0] = 0x45
		binary.BigEndian.PutUint16(ip[2:4], uint16(20+8+len(d.Payload)))
		ip[8], ip[9] = 64, 17
		copy(ip[12:16], src[:])
		copy(ip[16:20], []byte{192, 0, 2, 100})
		udp := frame[34:42]
		binary.BigEndian.PutUint16(udp[0:2], 50000)
		binary.BigEndian.PutUint16(udp[2:4], d.DstPort)
		binary.BigEndian.PutUint16(udp[4:6], uint16(8+len(d.Payload)))
		copy(frame[42:], d.Payload)

		rec := make([]byte, 16)
		binary.LittleEndian.PutUint32(rec[0:4], uint32(d.Time.Unix()))
		binary.LittleEndian.PutUint32(rec[4:8], uint32(d.Time.Nanosecond()/1000))
		binary.LittleEndian.PutUint32(rec[8:12], uint32(len(frame)))
		binary.LittleEndian.PutUint32(rec[12:16], uint32(len(frame)))
		if _, err := w.Write(rec); err != nil {
			return err
		}
		if _, err := w.Write(frame); err != nil {
			return err
		}
	}
	return nil
}
