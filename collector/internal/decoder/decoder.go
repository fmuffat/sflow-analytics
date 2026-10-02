// Package decoder turns raw sFlow v5 datagrams into normalized records.
//
// Per-sample decoding is delegated to goflow2 (github.com/netsampler/goflow2),
// but the datagram envelope is walked here so that one bad or unknown
// sample only drops that sample, not the whole datagram.
package decoder

import (
	"bytes"
	"encoding/binary"
	"errors"
	"fmt"
	"net/netip"
	"time"

	"github.com/netsampler/goflow2/v2/decoders/sflow"

	"sflow-analytics/collector/internal/normalize"
)

const (
	maxSamplesPerDatagram = 1000
	maxRecordsPerSample   = 1000
)

var (
	// ErrUnsupportedVersion is returned for datagrams that are not sFlow v5.
	ErrUnsupportedVersion = errors.New("unsupported sflow version")
	// ErrMalformed is returned when the datagram header cannot be decoded.
	ErrMalformed = errors.New("malformed sflow datagram")
)

// Result is everything extracted from one datagram, plus counters describing
// what was skipped. A Result may be partially filled when Truncated is true.
type Result struct {
	Meta     normalize.DatagramMeta        `json:"meta"`
	Version  uint32                        `json:"version"`
	Flows    []normalize.FlowRecord        `json:"flows"`
	Counters []normalize.InterfaceCounters `json:"counters"`

	Samples            int  `json:"samples"` // samples announced and walked
	FlowSamples        int  `json:"flow_samples"`
	CounterSamples     int  `json:"counter_samples"`
	DropSamples        int  `json:"drop_samples"`        // sFlow "discarded packet" samples (not processed in MVP)
	UnsupportedSamples int  `json:"unsupported_samples"` // unknown sample formats, skipped
	UnsupportedRecords int  `json:"unsupported_records"` // unknown flow/counter record formats, skipped
	SampleErrors       int  `json:"sample_errors"`       // samples that failed to decode, skipped
	Truncated          bool `json:"truncated"`
}

// Decode parses one UDP payload received from exporter src at time now.
// It never panics on arbitrary input. A non-nil error means nothing usable
// was decoded; per-sample problems are reported in Result counters instead.
func Decode(data []byte, src netip.Addr, now time.Time) (Result, error) {
	var res Result
	res.Meta.ReceivedAt = now
	res.Meta.ExporterIP = src

	r := reader{b: data}
	res.Version = r.u32()
	if r.err != nil {
		return res, fmt.Errorf("%w: short header", ErrMalformed)
	}
	if res.Version != 5 {
		return res, fmt.Errorf("%w: %d", ErrUnsupportedVersion, res.Version)
	}

	switch addrType := r.u32(); addrType {
	case 1:
		if b := r.bytes(4); b != nil {
			res.Meta.AgentIP = netip.AddrFrom4([4]byte(b))
		}
	case 2:
		if b := r.bytes(16); b != nil {
			res.Meta.AgentIP = netip.AddrFrom16([16]byte(b))
		}
	default:
		if r.err == nil {
			return res, fmt.Errorf("%w: unknown agent address type %d", ErrMalformed, addrType)
		}
	}
	res.Meta.SubAgentID = r.u32()
	res.Meta.SequenceNumber = r.u32()
	res.Meta.UptimeMs = r.u32()
	count := r.u32()
	if r.err != nil {
		return res, fmt.Errorf("%w: short header", ErrMalformed)
	}
	if count > maxSamplesPerDatagram {
		return res, fmt.Errorf("%w: %d samples announced", ErrMalformed, count)
	}

	for i := uint32(0); i < count; i++ {
		format := r.u32()
		length := r.u32()
		body := r.bytes(int(length))
		if r.err != nil || int(length) < 0 {
			res.Truncated = true
			break
		}
		res.Samples++
		res.decodeSample(format, length, body)
	}
	return res, nil
}

func (res *Result) decodeSample(format, length uint32, body []byte) {
	switch format {
	case sflow.SAMPLE_FORMAT_FLOW, sflow.SAMPLE_FORMAT_COUNTER,
		sflow.SAMPLE_FORMAT_EXPANDED_FLOW, sflow.SAMPLE_FORMAT_EXPANDED_COUNTER:
	case sflow.SAMPLE_FORMAT_DROP:
		res.DropSamples++
		return
	default:
		res.UnsupportedSamples++
		return
	}
	if !recordCountSane(format, body) {
		res.SampleErrors++
		return
	}

	hdr := sflow.SampleHeader{Format: format, Length: length}
	sample, err := safeDecodeSample(&hdr, body)
	if err != nil {
		res.SampleErrors++
		return
	}

	switch s := sample.(type) {
	case sflow.FlowSample:
		inF, inV := normalize.FromCompact(s.Input)
		outF, outV := normalize.FromCompact(s.Output)
		res.addFlow(normalize.FlowSampleInput{
			SampleSequence: s.Header.SampleSequenceNumber,
			SourceIDType:   s.Header.SourceIdType,
			SourceIDIndex:  s.Header.SourceIdValue,
			SamplingRate:   s.SamplingRate,
			InputFormat:    inF, InputValue: inV,
			OutputFormat: outF, OutputValue: outV,
			Records: s.Records,
		})
	case sflow.ExpandedFlowSample:
		res.addFlow(normalize.FlowSampleInput{
			SampleSequence: s.Header.SampleSequenceNumber,
			SourceIDType:   s.Header.SourceIdType,
			SourceIDIndex:  s.Header.SourceIdValue,
			SamplingRate:   s.SamplingRate,
			InputFormat:    s.InputIfFormat, InputValue: s.InputIfValue,
			OutputFormat: s.OutputIfFormat, OutputValue: s.OutputIfValue,
			Records: s.Records,
		})
	case sflow.CounterSample:
		res.CounterSamples++
		counters, unsupported := normalize.BuildCounters(res.Meta, s.Records)
		res.Counters = append(res.Counters, counters...)
		res.UnsupportedRecords += unsupported
	default:
		res.SampleErrors++
	}
}

func (res *Result) addFlow(in normalize.FlowSampleInput) {
	res.FlowSamples++
	rec, unsupported := normalize.BuildFlow(res.Meta, in)
	res.UnsupportedRecords += unsupported
	res.Flows = append(res.Flows, rec)
}

// recordCountSane rejects samples announcing more records than can fit in the
// sample body. goflow2 pre-allocates the announced count, and does not bound
// it for expanded flow samples, so a forged value could exhaust memory.
func recordCountSane(format uint32, body []byte) bool {
	var off int
	switch format {
	case sflow.SAMPLE_FORMAT_FLOW: // seq, source_id, rate, pool, drops, in, out
		off = 7 * 4
	case sflow.SAMPLE_FORMAT_COUNTER: // seq, source_id
		off = 2 * 4
	case sflow.SAMPLE_FORMAT_EXPANDED_FLOW: // seq, src type, src idx, rate, pool, drops, in fmt/val, out fmt/val
		off = 10 * 4
	case sflow.SAMPLE_FORMAT_EXPANDED_COUNTER: // seq, src type, src idx
		off = 3 * 4
	default:
		return false
	}
	if len(body) < off+4 {
		return false
	}
	n := binary.BigEndian.Uint32(body[off : off+4])
	return n <= maxRecordsPerSample && int(n) <= (len(body)-off-4)/8
}

// safeDecodeSample shields the collector from any panic inside the
// third-party decoder.
func safeDecodeSample(hdr *sflow.SampleHeader, body []byte) (sample interface{}, err error) {
	defer func() {
		if p := recover(); p != nil {
			err = fmt.Errorf("decoder panic: %v", p)
		}
	}()
	return sflow.DecodeSample(hdr, bytes.NewBuffer(body))
}

// reader is a bounds-checked big-endian (XDR) cursor.
type reader struct {
	b   []byte
	off int
	err error
}

var errShort = errors.New("short buffer")

func (r *reader) u32() uint32 {
	b := r.bytes(4)
	if b == nil {
		return 0
	}
	return binary.BigEndian.Uint32(b)
}

func (r *reader) bytes(n int) []byte {
	if r.err != nil {
		return nil
	}
	if n < 0 || len(r.b)-r.off < n {
		r.err = errShort
		return nil
	}
	b := r.b[r.off : r.off+n]
	r.off += n
	return b
}
