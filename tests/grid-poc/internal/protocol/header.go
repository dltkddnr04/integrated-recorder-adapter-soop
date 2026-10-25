// Package protocol implements the December 2023 Parent packet family and
// statically recovered current-candidate request shapes. It does not infer
// the Center protocol or current response payload offsets.
package protocol

import (
	"encoding/binary"
	"errors"
	"fmt"
	"io"
)

const (
	HeaderSize   = 16
	Version2023  = 0x0202
	MaxBodyBytes = 4 << 20
)

var ErrChecksum = errors.New("Grid header checksum mismatch")

type Header struct {
	Version  uint16
	Command  uint16
	BodySize uint32
	Checksum uint32
	Unknown  uint32
}

type Packet struct {
	Header Header
	Body   []byte
}

func Checksum(version, command uint16, bodySize uint32) uint32 {
	return uint32(version) ^ uint32(command) ^ bodySize
}

func (h Header) MarshalBinary() ([]byte, error) {
	if h.Version != Version2023 {
		return nil, fmt.Errorf("unsupported Grid version 0x%04x", h.Version)
	}
	if h.Checksum != Checksum(h.Version, h.Command, h.BodySize) {
		return nil, ErrChecksum
	}
	b := make([]byte, HeaderSize)
	binary.LittleEndian.PutUint16(b[0:2], h.Version)
	binary.LittleEndian.PutUint16(b[2:4], h.Command)
	binary.LittleEndian.PutUint32(b[4:8], h.BodySize)
	binary.LittleEndian.PutUint32(b[8:12], h.Checksum)
	binary.LittleEndian.PutUint32(b[12:16], h.Unknown)
	return b, nil
}

func ParseHeader(b []byte, maxBody uint32) (Header, error) {
	if len(b) != HeaderSize {
		return Header{}, fmt.Errorf("Grid header length %d; want %d", len(b), HeaderSize)
	}
	if maxBody == 0 || maxBody > MaxBodyBytes {
		maxBody = MaxBodyBytes
	}
	h := Header{
		Version:  binary.LittleEndian.Uint16(b[0:2]),
		Command:  binary.LittleEndian.Uint16(b[2:4]),
		BodySize: binary.LittleEndian.Uint32(b[4:8]),
		Checksum: binary.LittleEndian.Uint32(b[8:12]),
		Unknown:  binary.LittleEndian.Uint32(b[12:16]),
	}
	if h.Version != Version2023 {
		return Header{}, fmt.Errorf("unsupported Grid version 0x%04x", h.Version)
	}
	if h.BodySize > maxBody {
		return Header{}, fmt.Errorf("Grid body size %d exceeds limit %d", h.BodySize, maxBody)
	}
	if h.Checksum != Checksum(h.Version, h.Command, h.BodySize) {
		return Header{}, ErrChecksum
	}
	return h, nil
}

func NewPacket(command uint16, unknown uint32, body []byte) (Packet, error) {
	if len(body) > MaxBodyBytes {
		return Packet{}, fmt.Errorf("Grid body size %d exceeds limit %d", len(body), MaxBodyBytes)
	}
	h := Header{Version: Version2023, Command: command, BodySize: uint32(len(body)), Unknown: unknown}
	h.Checksum = Checksum(h.Version, h.Command, h.BodySize)
	return Packet{Header: h, Body: append([]byte(nil), body...)}, nil
}

func (p Packet) MarshalBinary() ([]byte, error) {
	if uint64(len(p.Body)) > uint64(MaxBodyBytes) || uint32(len(p.Body)) != p.Header.BodySize {
		return nil, errors.New("Grid packet body length does not match header or exceeds limit")
	}
	h, err := p.Header.MarshalBinary()
	if err != nil {
		return nil, err
	}
	out := make([]byte, 0, len(h)+len(p.Body))
	out = append(out, h...)
	out = append(out, p.Body...)
	return out, nil
}

func ReadPacket(r io.Reader, maxBody uint32) (Packet, error) {
	var raw [HeaderSize]byte
	if _, err := io.ReadFull(r, raw[:]); err != nil {
		return Packet{}, err
	}
	h, err := ParseHeader(raw[:], maxBody)
	if err != nil {
		return Packet{}, err
	}
	body := make([]byte, int(h.BodySize))
	if _, err := io.ReadFull(r, body); err != nil {
		return Packet{}, err
	}
	return Packet{Header: h, Body: body}, nil
}
