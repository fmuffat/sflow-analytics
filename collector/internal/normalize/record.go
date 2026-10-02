// Package normalize converts decoded sFlow structures into the collector's
// internal, storage-agnostic records.
package normalize

import (
	"net/netip"
	"time"
)

// DatagramMeta carries the per-datagram context shared by every sample.
type DatagramMeta struct {
	ReceivedAt     time.Time  `json:"received_at"`
	ExporterIP     netip.Addr `json:"exporter_ip"` // source IP of the UDP datagram
	AgentIP        netip.Addr `json:"agent_ip"`    // sFlow agent address from the datagram header
	SubAgentID     uint32     `json:"agent_sub_id"`
	SequenceNumber uint32     `json:"sequence_number"` // datagram sequence number
	UptimeMs       uint32     `json:"uptime_ms"`
}

// FlowRecord is one normalized flow sample. Pointer fields are nullable:
// not every sample carries every field.
//
// Traffic volumes are ESTIMATES (sampled size x sampling rate), never exact counts.
type FlowRecord struct {
	Timestamp  time.Time  `json:"timestamp"`
	ExporterIP netip.Addr `json:"exporter_ip"`
	AgentIP    netip.Addr `json:"agent_ip"`
	AgentSubID uint32     `json:"agent_sub_id"`

	SampleSequence uint32 `json:"sample_sequence"`
	SourceIDType   uint32 `json:"source_id_type"`
	SourceIDIndex  uint32 `json:"source_id_index"`

	InputIfIndex  *uint32 `json:"input_ifindex"`
	OutputIfIndex *uint32 `json:"output_ifindex"`

	SrcMAC    *string `json:"src_mac"`
	DstMAC    *string `json:"dst_mac"`
	EtherType *uint16 `json:"ether_type"`
	VLAN      *uint16 `json:"vlan"`

	IPVersion  *uint8      `json:"ip_version"`
	SrcIP      *netip.Addr `json:"src_ip"`
	DstIP      *netip.Addr `json:"dst_ip"`
	IPProtocol *uint8      `json:"ip_protocol"`
	SrcPort    *uint16     `json:"src_port"`
	DstPort    *uint16     `json:"dst_port"`
	TCPFlags   *uint8      `json:"tcp_flags"`

	SampledPacketSize uint32 `json:"sampled_packet_size"`
	SamplingRate      uint32 `json:"sampling_rate"`
	EstimatedBytes    uint64 `json:"estimated_bytes"`
	EstimatedPackets  uint64 `json:"estimated_packets"`
}

// InterfaceCounters is one normalized generic interface counter record
// (sFlow counters_sample, data format 0:1).
type InterfaceCounters struct {
	Timestamp  time.Time  `json:"timestamp"`
	ExporterIP netip.Addr `json:"exporter_ip"`
	AgentIP    netip.Addr `json:"agent_ip"`
	AgentSubID uint32     `json:"agent_sub_id"`

	IfIndex     uint32 `json:"ifindex"`
	IfType      uint32 `json:"if_type"`
	SpeedBps    uint64 `json:"speed_bps"`
	Direction   uint32 `json:"direction"` // 0 unknown, 1 full-duplex, 2 half-duplex, 3 in, 4 out
	AdminUp     bool   `json:"admin_up"`
	OperUp      bool   `json:"oper_up"`
	InOctets    uint64 `json:"in_octets"`
	InUcast     uint32 `json:"in_ucast_pkts"`
	InMulticast uint32 `json:"in_multicast_pkts"`
	InBroadcast uint32 `json:"in_broadcast_pkts"`
	InDiscards  uint32 `json:"in_discards"`
	InErrors    uint32 `json:"in_errors"`
	OutOctets   uint64 `json:"out_octets"`
	OutUcast    uint32 `json:"out_ucast_pkts"`
	OutMulti    uint32 `json:"out_multicast_pkts"`
	OutBcast    uint32 `json:"out_broadcast_pkts"`
	OutDiscards uint32 `json:"out_discards"`
	OutErrors   uint32 `json:"out_errors"`
}

func ptr[T any](v T) *T { return &v }
