package sflowgen

import (
	"math/rand/v2"
	"net"
	"net/netip"
)

// Scenario produces plausible, deterministic campus traffic for one fake
// exporter: a few heavy talkers, many light ones, common services, several
// VLANs and access/uplink ports. No real customer data is involved.
type Scenario struct {
	AgentIP      netip.Addr
	SubAgentID   uint32
	SamplingRate uint32
	Ports        int // access ports; the uplink is ifIndex Ports+1

	rng        *rand.Rand
	seq        uint32
	sampleSeq  uint32
	counterSeq uint32
	pools      map[uint32]uint32
	octets     map[uint32]uint64
	uptime     uint32
}

type service struct {
	proto uint8
	port  uint16
	size  uint32 // typical frame size
}

var services = []service{
	{6, 443, 1400}, {6, 443, 1400}, {6, 443, 900}, {17, 443, 1250}, // HTTPS + QUIC
	{17, 53, 90}, {6, 445, 1400}, {6, 22, 200}, {6, 3389, 700},
	{6, 80, 1000}, {17, 123, 90}, {6, 993, 400}, {6, 587, 600},
	{17, 5004, 220}, {6, 8443, 1100},
}

var vlans = []uint16{10, 20, 30, 120}

// NewScenario creates a scenario for one exporter.
func NewScenario(agent netip.Addr, subAgent uint32, samplingRate uint32, ports int, seed uint64) *Scenario {
	if ports < 2 {
		ports = 48
	}
	return &Scenario{
		AgentIP: agent, SubAgentID: subAgent, SamplingRate: samplingRate, Ports: ports,
		rng:    rand.New(rand.NewPCG(seed, uint64(agent.As16()[15]))),
		pools:  make(map[uint32]uint32),
		octets: make(map[uint32]uint64),
	}
}

// NextDatagram returns a datagram with n flow samples. When withCounters is
// true, interface counter samples for a few ports are appended.
func (s *Scenario) NextDatagram(n int, withCounters bool) Datagram {
	s.seq++
	s.uptime += 1000
	d := Datagram{AgentIP: s.AgentIP, SubAgentID: s.SubAgentID, Sequence: s.seq, UptimeMs: s.uptime}
	for i := 0; i < n; i++ {
		d.Samples = append(d.Samples, s.flowSample())
	}
	if withCounters {
		for i := 0; i < 3; i++ {
			d.Samples = append(d.Samples, s.counterSample(uint32(1+s.rng.IntN(s.Ports+1))))
		}
	}
	return d
}

func (s *Scenario) flowSample() FlowSample {
	// Zipf-like host popularity: a handful of clients generate most traffic.
	client := 10 + uint8(s.zipf(60))
	vlan := vlans[int(client)%len(vlans)]
	svc := services[s.rng.IntN(len(services))]
	server := netip.AddrFrom4([4]byte{10, 40, 50, 10 + uint8(s.zipf(20))})
	if svc.port == 443 && s.rng.IntN(3) == 0 {
		// Some internet-bound traffic (documentation range, RFC 5737).
		server = netip.AddrFrom4([4]byte{203, 0, 113, uint8(1 + s.rng.IntN(50))})
	}
	src := netip.AddrFrom4([4]byte{10, 20, uint8(vlan), client})
	accessPort := uint32(1 + int(client)%s.Ports)
	uplink := uint32(s.Ports + 1)

	size := svc.size/2 + uint32(s.rng.IntN(int(svc.size)))
	if size > 1514 {
		size = 1514
	}
	if size < 64 {
		size = 64
	}

	// Half of the samples are the response direction.
	in, out := accessPort, uplink
	sport, dport := uint16(49152+s.rng.IntN(16000)), svc.port
	srcMAC, dstMAC := mac(0x02, uint8(vlan), client), mac(0x02, 0xFF, 0x01)
	flags := uint8(0x18) // PSH+ACK
	if s.rng.IntN(2) == 0 {
		src, server = server, src
		in, out = out, in
		sport, dport = dport, sport
		srcMAC, dstMAC = dstMAC, srcMAC
		size = 1514 - uint32(s.rng.IntN(200))
		flags = 0x10
	}

	frame := Frame{
		SrcMAC: srcMAC, DstMAC: dstMAC, VLAN: vlan,
		Src: src, Dst: server, Protocol: svc.proto, SrcPort: sport, DstPort: dport,
		TCPFlags: flags, FrameLength: size,
	}
	s.sampleSeq++
	s.pools[in] += s.SamplingRate
	return FlowSample{
		Sequence: s.sampleSeq, SourceIDType: 0, SourceIDIndex: in,
		SamplingRate: s.SamplingRate, SamplePool: s.pools[in],
		Input: CompactIf(in), Output: CompactIf(out),
		Records: []Record{
			SampledHeader{Protocol: 1, FrameLength: size + 4, Stripped: 4, Header: frame.Build(128)},
			ExtendedSwitch{SrcVLAN: uint32(vlan), DstVLAN: uint32(vlan)},
		},
	}
}

func (s *Scenario) counterSample(ifIndex uint32) CounterSample {
	s.counterSeq++
	s.octets[ifIndex] += uint64(s.rng.IntN(50_000_000))
	speed := uint64(1_000_000_000)
	if ifIndex == uint32(s.Ports+1) {
		speed = 10_000_000_000
	}
	return CounterSample{
		Sequence: s.counterSeq, SourceIDType: 0, SourceIDIndex: ifIndex,
		Records: []Record{IfCounters{
			IfIndex: ifIndex, IfType: 6, IfSpeed: speed, IfDirection: 1, IfStatus: 3,
			InOctets: s.octets[ifIndex], OutOctets: s.octets[ifIndex] / 2,
			InUcast: uint32(s.octets[ifIndex] / 800), OutUcast: uint32(s.octets[ifIndex] / 1600),
		}},
	}
}

func (s *Scenario) zipf(n int) int {
	// Squared uniform variate: strongly skewed towards small values.
	u := s.rng.Float64()
	return int(u * u * float64(n))
}

func mac(a, b, c uint8) net.HardwareAddr {
	return net.HardwareAddr{a, 0x00, 0x5e, a ^ b, b, c}
}
