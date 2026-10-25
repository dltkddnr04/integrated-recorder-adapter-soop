// Package iss implements the current candidate's CenterSocket framing.
// Candidate SHA-256: 98a1741b2368be33e25958a46443760663ff8da225c742aabac16d625297709b.
// CenterSocket vtable +8 resolves to VA 0x140276b00. It writes a 20-byte
// header: command, zero, body length, zero, command XOR body length (LE32).
package iss

import (
	"encoding/binary"
	"errors"
	"io"
)

const HeaderSize = 20
const MaxBody = 1 << 20

type Frame struct {
	Command  uint32
	Result   int32
	Reserved uint32
	Check    uint32
	Body     []byte
}

func Encode(command uint32, body []byte) ([]byte, error) {
	if len(body) > MaxBody {
		return nil, errors.New("iss body limit")
	}
	wire := make([]byte, HeaderSize+len(body))
	binary.LittleEndian.PutUint32(wire[0:4], command)
	binary.LittleEndian.PutUint32(wire[8:12], uint32(len(body)))
	binary.LittleEndian.PutUint32(wire[16:20], command^uint32(len(body)))
	copy(wire[20:], body)
	return wire, nil
}

func Read(r io.Reader) (Frame, error) {
	var h [HeaderSize]byte
	if _, err := io.ReadFull(r, h[:]); err != nil {
		return Frame{}, err
	}
	f := Frame{Command: binary.LittleEndian.Uint32(h[:4]), Result: int32(binary.LittleEndian.Uint32(h[4:8])), Reserved: binary.LittleEndian.Uint32(h[12:16]), Check: binary.LittleEndian.Uint32(h[16:20])}
	n := binary.LittleEndian.Uint32(h[8:12])
	if n > MaxBody {
		return f, errors.New("iss incoming body limit")
	}
	if f.Check != f.Command^n {
		return f, errors.New("iss checksum mismatch")
	}
	f.Body = make([]byte, int(n))
	if _, err := io.ReadFull(r, f.Body); err != nil {
		clear(f.Body)
		return f, err
	}
	return f, nil
}
