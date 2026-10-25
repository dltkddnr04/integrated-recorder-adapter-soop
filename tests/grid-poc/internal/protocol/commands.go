package protocol

import (
	"encoding/binary"
	"errors"
	"fmt"
)

const (
	CmdBroadcastStreamRequest uint16 = 0xCB2E
	// The current SOOPStreamer candidate emits this 4-byte follow-up after
	// stream setup. The body word's meaning is not recovered yet.
	CmdBroadcastStreamFollowup uint16 = 0xCB2F
	CmdBroadcastStreamAck      uint16 = 0x1BC7
	CmdCacheDataRequest        uint16 = 0xCB30
	CmdCacheDataReply          uint16 = 0xC748
	// Current response-name strings in the candidate. These have not been
	// observed on a live Parent connection.
	CmdCurrentBroadcastReply uint16 = 0xC746
	CmdCurrentCacheHeader    uint16 = 0xC747
	CmdCurrentPushFrame      uint16 = 0xC742
)

type BroadcastStreamRequest struct {
	BroadcastID uint32
	NodeKey     uint32
	Quality     uint32
	Unknown     uint32 // retained from observed headers; no default is inferred
}

func (r BroadcastStreamRequest) Packet() (Packet, error) {
	body := make([]byte, 12)
	binary.LittleEndian.PutUint32(body[0:4], r.BroadcastID)
	binary.LittleEndian.PutUint32(body[4:8], r.NodeKey)
	binary.LittleEndian.PutUint32(body[8:12], r.Quality)
	return NewPacket(CmdBroadcastStreamRequest, r.Unknown, body)
}

// BroadcastStreamFollowup captures only the current binary's observed packet
// layout. Value is intentionally opaque: its protocol meaning has not been
// recovered, and callers must obtain it from a normal session response.
type BroadcastStreamFollowup struct {
	Value   uint32
	Unknown uint32
}

func (r BroadcastStreamFollowup) Packet() (Packet, error) {
	body := make([]byte, 4)
	binary.LittleEndian.PutUint32(body, r.Value)
	return NewPacket(CmdBroadcastStreamFollowup, r.Unknown, body)
}

type BroadcastStreamAck struct {
	Quality         uint32
	LastFrameNew    uint32
	LastFrameBefore uint32
	LastPTSNew      uint32
	LastPTSBefore   uint32
	Unknown         uint32
}

func ParseBroadcastStreamAck(p Packet) (BroadcastStreamAck, error) {
	if p.Header.Version != Version2023 || p.Header.Command != CmdBroadcastStreamAck || len(p.Body) != 24 || p.Header.BodySize != uint32(len(p.Body)) || p.Header.Checksum != Checksum(p.Header.Version, p.Header.Command, p.Header.BodySize) {
		return BroadcastStreamAck{}, errors.New("unexpected stream-ack command or body length")
	}
	b := p.Body
	return BroadcastStreamAck{
		Quality:         binary.LittleEndian.Uint32(b[0:4]),
		LastFrameNew:    binary.LittleEndian.Uint32(b[4:8]),
		LastFrameBefore: binary.LittleEndian.Uint32(b[8:12]),
		LastPTSNew:      binary.LittleEndian.Uint32(b[12:16]),
		LastPTSBefore:   binary.LittleEndian.Uint32(b[16:20]),
		Unknown:         binary.LittleEndian.Uint32(b[20:24]),
	}, nil
}

type CacheDataRequest struct {
	StatusQuality  uint32
	LastFrameNew   uint32
	LastFramePrior uint32
	ConnectStatus  uint32
	Unknown        uint32
}

func (r CacheDataRequest) Packet() (Packet, error) {
	body := make([]byte, 16)
	binary.LittleEndian.PutUint32(body[0:4], r.StatusQuality)
	binary.LittleEndian.PutUint32(body[4:8], r.LastFrameNew)
	binary.LittleEndian.PutUint32(body[8:12], r.LastFramePrior)
	binary.LittleEndian.PutUint32(body[12:16], r.ConnectStatus)
	return NewPacket(CmdCacheDataRequest, r.Unknown, body)
}

type MediaFrame struct {
	HeaderValue uint16
	HeaderSize  uint16
	Extension   uint32
	RawSize     uint32
	FrameNumber uint32
	FrameType   byte
	Payload     []byte
}

const mediaFrameTypeOffset = 0x28

func ParseMediaFrame(p Packet) (MediaFrame, error) {
	if p.Header.Version != Version2023 || p.Header.Command != CmdCacheDataReply || p.Header.BodySize != uint32(len(p.Body)) || p.Header.Checksum != Checksum(p.Header.Version, p.Header.Command, p.Header.BodySize) {
		return MediaFrame{}, errors.New("unexpected command for media frame")
	}
	if len(p.Body) < 16 || len(p.Body) <= mediaFrameTypeOffset {
		return MediaFrame{}, errors.New("media body is shorter than observed fixed fields")
	}
	f := MediaFrame{
		HeaderValue: binary.LittleEndian.Uint16(p.Body[0:2]),
		HeaderSize:  binary.LittleEndian.Uint16(p.Body[2:4]),
		Extension:   binary.LittleEndian.Uint32(p.Body[4:8]),
		RawSize:     binary.LittleEndian.Uint32(p.Body[8:12]),
		FrameNumber: binary.LittleEndian.Uint32(p.Body[12:16]),
		FrameType:   p.Body[mediaFrameTypeOffset],
	}
	if f.HeaderSize <= mediaFrameTypeOffset || uint64(f.HeaderSize)+uint64(f.RawSize) > uint64(len(p.Body)) {
		return MediaFrame{}, fmt.Errorf("invalid media offsets: header=%d raw=%d body=%d", f.HeaderSize, f.RawSize, len(p.Body))
	}
	end := uint64(f.HeaderSize) + uint64(f.RawSize)
	f.Payload = append([]byte(nil), p.Body[int(f.HeaderSize):int(end)]...)
	return f, nil
}
