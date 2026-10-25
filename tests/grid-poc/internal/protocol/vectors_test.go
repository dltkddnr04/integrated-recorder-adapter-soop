package protocol

import (
	"bytes"
	"encoding/hex"
	"testing"
)

func mustHex(t *testing.T, s string) []byte {
	t.Helper()
	b, err := hex.DecodeString(s)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func TestPublishedBroadcastRequestVector(t *testing.T) {
	want := mustHex(t, "02022ecb0c00000020c9000028f91900a1cbe70eb814000001000000")
	p, err := (BroadcastStreamRequest{BroadcastID: 0x0EE7CBA1, NodeKey: 0x14B8, Quality: 1, Unknown: 0x0019F928}).Packet()
	if err != nil {
		t.Fatal(err)
	}
	got, err := p.MarshalBinary()
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, want) {
		t.Fatalf("wire vector mismatch\n got %x\nwant %x", got, want)
	}
}

func TestCurrentBroadcastFollowupHeaderVector(t *testing.T) {
	// The binary statically constructs command 0xCB2F with a 4-byte body and
	// checksum 0xC929. The test word is synthetic and is not a SOOP response.
	want := mustHex(t, "02022fcb0400000029c9000000000000ddccbbaa")
	p, err := (BroadcastStreamFollowup{Value: 0xAABBCCDD}).Packet()
	if err != nil {
		t.Fatal(err)
	}
	got, err := p.MarshalBinary()
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, want) {
		t.Fatalf("wire vector mismatch\n got %x\nwant %x", got, want)
	}
}

func TestPublishedStreamAckVector(t *testing.T) {
	wire := mustHex(t, "0202c71b18000000dd19000053554c540100000054df3000000000003b0dc8c11006000001000000")
	p, err := ReadPacket(bytes.NewReader(wire), MaxBodyBytes)
	if err != nil {
		t.Fatal(err)
	}
	ack, err := ParseBroadcastStreamAck(p)
	if err != nil {
		t.Fatal(err)
	}
	if ack.Quality != 1 || ack.LastFrameNew != 0x0030DF54 || ack.LastFrameBefore != 0 || ack.LastPTSNew != 0xC1C80D3B || ack.LastPTSBefore != 0x610 || ack.Unknown != 1 {
		t.Fatalf("unexpected ack fields: %+v", ack)
	}
}

func TestPublishedCacheRequestVector(t *testing.T) {
	want := mustHex(t, "020230cb1000000022c9000010d2d800010000005cdf300000000000ffffffff")
	p, err := (CacheDataRequest{StatusQuality: 1, LastFrameNew: 0x0030DF5C, LastFramePrior: 0, ConnectStatus: 0xFFFFFFFF, Unknown: 0x00D8D210}).Packet()
	if err != nil {
		t.Fatal(err)
	}
	got, err := p.MarshalBinary()
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, want) {
		t.Fatalf("wire vector mismatch\n got %x\nwant %x", got, want)
	}
}

func TestPublishedMediaReplyHeaderAndOffsets(t *testing.T) {
	// Header/body fields are from the published screenshot. The payload bytes
	// are not captured here; this checks only the documented offsets and bounds.
	head := mustHex(t, "020248c77b04000031c1000000000000")
	h, err := ParseHeader(head, MaxBodyBytes)
	if err != nil {
		t.Fatal(err)
	}
	if h.BodySize != 0x47B || h.Checksum != 0xC131 || h.Unknown != 0 {
		t.Fatalf("unexpected media header: %+v", h)
	}
	body := make([]byte, 0x47B)
	copy(body, mustHex(t, "0100450000000000360400005cdf3000"))
	body[mediaFrameTypeOffset] = 'P'
	p := Packet{Header: h, Body: body}
	f, err := ParseMediaFrame(p)
	if err != nil {
		t.Fatal(err)
	}
	if f.HeaderSize != 0x45 || f.Extension != 0 || f.RawSize != 0x436 || f.FrameNumber != 0x0030DF5C || f.FrameType != 'P' || len(f.Payload) != 0x436 {
		t.Fatalf("unexpected media frame fields: header=%d raw=%d frame=%x type=%q payload=%d", f.HeaderSize, f.RawSize, f.FrameNumber, f.FrameType, len(f.Payload))
	}
}

func TestRejectsBadChecksumAndOversizedBody(t *testing.T) {
	bad := mustHex(t, "02022ecb0c00000021c9000028f91900")
	if _, err := ParseHeader(bad, MaxBodyBytes); err == nil {
		t.Fatal("accepted checksum mismatch")
	}
	large := mustHex(t, "02022ecb010000002ecb020200000000")
	if _, err := ParseHeader(large, 8); err == nil {
		t.Fatal("accepted oversized body")
	}
}
