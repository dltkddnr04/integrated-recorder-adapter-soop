package media

// Minimal MPEG-TS remuxer for the observed Annex-B H.264 + ADTS AAC path.
// PES timestamps come from the Grid media header's 100 ns clock; compressed
// payload bytes are copied unchanged. A playable export starts at SPS + IDR;
// all earlier received bytes remain in the separate raw/elementary files.
import (
	"bytes"
	"encoding/binary"
	"errors"
	"io"
)

type TSMux struct {
	w                                      io.Writer
	cc                                     map[uint16]byte
	started                                bool
	base                                   uint64
	videoCount                             int
	VideoFrames, AudioChunks, PrerollVideo int
	FirstPTS, LastPTS                      uint64
}

func NewTSMux(w io.Writer) *TSMux { return &TSMux{w: w, cc: make(map[uint16]byte)} }
func crcMPEG(b []byte) uint32 {
	crc := uint32(0xffffffff)
	for _, v := range b {
		crc ^= uint32(v) << 24
		for i := 0; i < 8; i++ {
			if crc&0x80000000 != 0 {
				crc = crc<<1 ^ 0x04c11db7
			} else {
				crc <<= 1
			}
		}
	}
	return crc
}
func (m *TSMux) section(pid uint16, data []byte) error {
	b := append([]byte(nil), data...)
	var sum [4]byte
	binary.BigEndian.PutUint32(sum[:], crcMPEG(b))
	b = append(b, sum[:]...)
	b = append([]byte{0}, b...)
	return m.packetize(pid, b, true, nil)
}
func (m *TSMux) tables() error {
	if e := m.section(0, []byte{0, 0xb0, 0x0d, 0, 1, 0xc1, 0, 0, 0, 1, 0xf0, 0}); e != nil {
		return e
	}
	return m.section(0x1000, []byte{2, 0xb0, 0x17, 0, 1, 0xc1, 0, 0, 0xe1, 0, 0xf0, 0, 0x1b, 0xe1, 0, 0xf0, 0, 0x0f, 0xe1, 1, 0xf0, 0})
}
func (m *TSMux) packetize(pid uint16, data []byte, start bool, pcr *uint64) error {
	for len(data) > 0 {
		var b [188]byte
		for i := range b {
			b[i] = 0xff
		}
		b[0] = 0x47
		b[1] = byte(pid>>8) & 0x1f
		if start {
			b[1] |= 0x40
		}
		b[2] = byte(pid)
		b[3] = 0x10 | m.cc[pid]
		m.cc[pid] = (m.cc[pid] + 1) & 15
		capacity := 184
		adapt := 0
		if pcr != nil {
			capacity = 176
			adapt = 8
		}
		n := len(data)
		if n > capacity {
			n = capacity
		}
		if n < capacity {
			adapt = 184 - n
		}
		pos := 4
		if adapt > 0 {
			b[3] |= 0x20
			b[pos] = byte(adapt - 1)
			if adapt >= 2 {
				b[pos+1] = 0
			}
			if pcr != nil {
				b[pos+1] = 0x10
				v := *pcr & ((1 << 33) - 1)
				b[pos+2] = byte(v >> 25)
				b[pos+3] = byte(v >> 17)
				b[pos+4] = byte(v >> 9)
				b[pos+5] = byte(v >> 1)
				b[pos+6] = byte(v<<7) | 0x7e
				b[pos+7] = 0
			}
			pos += adapt
		}
		copy(b[pos:], data[:n])
		if _, e := m.w.Write(b[:]); e != nil {
			return e
		}
		data = data[n:]
		start = false
		pcr = nil
	}
	return nil
}
func ptsBytes(v uint64) [5]byte {
	v &= (1 << 33) - 1
	return [5]byte{0x21 | byte((v>>29)&0x0e), byte(v >> 22), byte(v>>14)&0xfe | 1, byte(v >> 7), byte(v<<1) | 1}
}
func (m *TSMux) Add(kind byte, ts uint64, payload []byte) error {
	video := kind == 'I' || kind == 'P' || kind == 'B'
	if !video && kind != 'A' {
		return nil
	}
	if !m.started {
		if !video {
			return nil
		}
		if kind != 'I' || !hasNAL(payload, 7) || !hasNAL(payload, 5) {
			m.PrerollVideo++
			return nil
		}
		m.started = true
		m.base = ts
		if e := m.tables(); e != nil {
			return e
		}
	}
	// Preserve a positive initial PCR/PTS and avoid unsigned subtraction.
	if ts > (1<<63)-1-10000000 || m.base > (1<<63)-1 || ts+10000000 < m.base {
		return errors.New("timestamp exceeds negative lead bound")
	}
	pts := uint64(int64(ts)-int64(m.base)+10000000) * 9 / 1000
	if video {
		if m.videoCount > 0 && m.videoCount%60 == 0 {
			if e := m.tables(); e != nil {
				return e
			}
		}
		m.videoCount++
		m.VideoFrames++
		if m.VideoFrames == 1 {
			m.FirstPTS = pts
		}
		m.LastPTS = pts
	} else {
		m.AudioChunks++
	}
	header := []byte{0, 0, 1, 0xe0, 0, 0, 0x80, 0x80, 5}
	pid := uint16(0x100)
	if !video {
		header[3] = 0xc0
		pid = 0x101
		n := len(payload) + 8
		if n > 65535 {
			return errors.New("AAC PES limit")
		}
		binary.BigEndian.PutUint16(header[4:], uint16(n))
	}
	stamp := ptsBytes(pts)
	header = append(header, stamp[:]...)
	header = append(header, payload...)
	if video {
		return m.packetize(pid, header, true, &pts)
	}
	return m.packetize(pid, header, true, nil)
}
func hasNAL(b []byte, kind byte) bool {
	for i := 0; i+4 < len(b); i++ {
		n := 0
		if bytes.HasPrefix(b[i:], []byte{0, 0, 0, 1}) {
			n = 4
		} else if bytes.HasPrefix(b[i:], []byte{0, 0, 1}) {
			n = 3
		}
		if n > 0 && i+n < len(b) && b[i+n]&31 == kind {
			return true
		}
	}
	return false
}
