# sFlow notes

## Ruckus ICX configuration

```
sflow destination 192.0.2.10 6343       ! collector IP / UDP port
sflow sample 512                         ! 1 packet in N; see below
sflow polling-interval 20                ! interface counters every 20 s
sflow enable                             ! global switch — required
interface ethernet 1/1/1 to 1/1/48
 sflow forwarding                        ! enable sampling on these ports
```

Verify with `show sflow`: services enabled, the collector listed, and the
"UDP packets exported" counter increasing.

Pitfalls seen so far:

- Without `sflow enable` nothing is sent, even with a destination and
  `sflow forwarding` configured (observed on an ICX).
- The ICX may source sFlow from the management port; the collector must be
  reachable from that interface.
- An ICX may report sub-agent ID 1, so its exporter ID looks like
  `192.0.2.2/192.0.2.2/1` (UDP source / agent address / sub-agent).

### Ingress sampling

ICX switches sample packets as they **enter** a port with `sflow forwarding`.
To see traffic per port in both directions, enable `sflow forwarding` on every
port of interest (access ports and uplinks); a packet is then sampled once, on
its ingress port, with its egress port as `output_ifindex`. If all samples
arrive on a single ifIndex, only that port has sampling enabled.

### Sampling rate

The estimate error shrinks with the number of samples. For a lab or small
site with little traffic, 1-in-4096 gives only a handful of samples per minute;
1-in-512 is a better default. For busy 10/40/100G uplinks keep the rate
higher (2048–8192) to limit collector load. The rate reported in each sample
is always used for estimation, so mixed rates are fine.

## What the collector extracts

| Source in sFlow                          | Field(s)                                   |
|------------------------------------------|--------------------------------------------|
| UDP source address                       | `exporter_ip`                              |
| Datagram header                          | `agent_ip`, `agent_sub_id`, sequence       |
| Flow sample                              | `sampling_rate`, `input_ifindex`, `output_ifindex`, source id |
| Sampled header (Ethernet/IPv4/IPv6)      | MACs, EtherType, VLAN, IPs, protocol, ports, TCP flags, frame length |
| Sampled IPv4/IPv6 / Ethernet records     | fallback when no raw header is present     |
| Extended switch record                   | VLAN when the sampled frame is untagged    |
| Counter sample, generic interface record | ifIndex, speed, status, octets, errors     |

Interface encoding: only "single interface" values are exported as ifIndex.
Discarded (format 1), multiple/flooded (format 2), 0 and 0x3FFFFFFF are
stored as null.

Frames whose type/length field is below 0x0600 are 802.3/LLC (e.g. STP
BPDUs): they keep their MACs and VLAN but have no EtherType.

## Estimation

```
estimated_bytes   = sampled_packet_size × sampling_rate
estimated_packets = sampling_rate
```

`sampled_packet_size` is the original frame length from the sampled header
(falls back to the IP record length). A sampling rate of 0 is treated as 1.

## Robustness

- The datagram envelope is walked by the collector; each sample is decoded
  by [goflow2](https://github.com/netsampler/goflow2). A bad or unknown sample
  is skipped and counted; the rest of the datagram is kept.
- Announced sample and record counts are bounded by the actual payload size
  before decoding (goflow2 does not bound expanded flow samples).
- Any panic in decoding is recovered and counted as a malformed datagram.
- Sequence gaps per exporter are counted as `lost_datagrams`.
- The decoder is fuzzed (`make fuzz`) and every truncation of every fixture is tested.

Not processed in the MVP (counted only): drop samples (format 5), extended
router/gateway records, Ethernet and CPU counter records.
