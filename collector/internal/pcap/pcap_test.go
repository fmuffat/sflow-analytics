package pcap

import (
	"bytes"
	"errors"
	"net/netip"
	"testing"
	"time"
)

func TestWriteReadRoundTrip(t *testing.T) {
	t0 := time.Date(2026, 1, 1, 0, 0, 0, 123000, time.UTC)
	in := []Datagram{
		{Time: t0, Src: netip.MustParseAddr("10.0.0.1"), DstPort: 6343, Payload: []byte{0, 0, 0, 5, 1}},
		{Time: t0.Add(time.Second), Src: netip.MustParseAddr("10.0.0.2"), DstPort: 9999, Payload: []byte{7}},
		{Time: t0.Add(2 * time.Second), Src: netip.MustParseAddr("10.0.0.3"), DstPort: 6343, Payload: []byte{1, 2}},
	}
	var buf bytes.Buffer
	if err := Write(&buf, in); err != nil {
		t.Fatal(err)
	}
	out, err := Read(bytes.NewReader(buf.Bytes()), 6343)
	if err != nil {
		t.Fatal(err)
	}
	if len(out) != 2 {
		t.Fatalf("got %d datagrams, want 2 (port filter)", len(out))
	}
	if out[0].Src.String() != "10.0.0.1" || !bytes.Equal(out[0].Payload, in[0].Payload) || !out[0].Time.Equal(t0) {
		t.Errorf("first = %+v", out[0])
	}
	all, _ := Read(bytes.NewReader(buf.Bytes()), 0)
	if len(all) != 3 {
		t.Errorf("port 0 must return all, got %d", len(all))
	}
}

func TestRejectsPcapng(t *testing.T) {
	_, err := Read(bytes.NewReader([]byte{0x0a, 0x0d, 0x0d, 0x0a, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0}), 0)
	if !errors.Is(err, ErrNotPcap) {
		t.Errorf("err = %v", err)
	}
}

func TestTruncatedFileKeepsPrefix(t *testing.T) {
	var buf bytes.Buffer
	Write(&buf, []Datagram{
		{Src: netip.MustParseAddr("10.0.0.1"), DstPort: 6343, Payload: []byte{1}},
		{Src: netip.MustParseAddr("10.0.0.1"), DstPort: 6343, Payload: []byte{2}},
	})
	b := buf.Bytes()
	out, err := Read(bytes.NewReader(b[:len(b)-3]), 6343)
	if err == nil || len(out) != 1 {
		t.Errorf("out=%d err=%v", len(out), err)
	}
}
