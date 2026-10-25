package media

import (
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"github.com/integrated-recorder/source.soop/tests/grid-poc/internal/protocol"
	"hash"
	"io"
	"os"
	"path/filepath"
)

// Capture preserves every received media wire packet and its unchanged payload.
// Only codec-recognized chunks are exported as elementary streams.
type Capture struct {
	Dir                                             string
	wire, payload, video, audio, sidecar, transport *os.File
	mux                                             *TSMux
	wireHash, payloadHash                           hash.Hash
	Summary                                         CaptureSummary
	lastFrame                                       uint64
	haveFrame                                       bool
}
type CaptureSummary struct {
	Packets             int        `json:"packets"`
	WireBytes           int64      `json:"wire_bytes"`
	PayloadBytes        int64      `json:"payload_bytes"`
	VideoFrames         int        `json:"video_frames"`
	AudioFrames         int        `json:"audio_frames"`
	VideoBytes          int64      `json:"video_bytes"`
	AudioBytes          int64      `json:"audio_bytes"`
	Discontinuities     int        `json:"sequence_discontinuities"`
	WireSHA256          string     `json:"wire_sha256"`
	PayloadSHA256       string     `json:"payload_sha256"`
	FirstVideoTS        uint64     `json:"first_video_timestamp_raw"`
	LastVideoTS         uint64     `json:"last_video_timestamp_raw"`
	PlayableVideoFrames int        `json:"playable_video_frames"`
	PrerollVideoFrames  int        `json:"preroll_video_frames"`
	VideoInfo           *VideoInfo `json:"video_info,omitempty"`
}

func NewCapture(dir string) (*Capture, error) {
	if err := os.Mkdir(dir, 0700); err != nil {
		return nil, err
	}
	c := &Capture{Dir: dir, wireHash: sha256.New(), payloadHash: sha256.New()}
	for _, v := range []struct {
		name string
		file **os.File
	}{{"grid-wire.bin", &c.wire}, {"grid-payload.bin", &c.payload}, {"video.h264", &c.video}, {"audio.aac", &c.audio}, {"frames.jsonl", &c.sidecar}, {"recording.ts", &c.transport}} {
		f, e := os.OpenFile(filepath.Join(dir, v.name), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
		if e != nil {
			c.Close()
			return nil, e
		}
		*v.file = f
	}
	c.mux = NewTSMux(c.transport)
	return c, nil
}
func (c *Capture) Add(p protocol.Packet) error {
	b := p.Body
	if len(b) < 69 {
		return errors.New("media_header_short")
	}
	hdr := int(binary.LittleEndian.Uint16(b[2:]))
	raw := int(binary.LittleEndian.Uint32(b[8:]))
	ext := int(binary.LittleEndian.Uint32(b[4:]))
	if hdr < 69 || hdr > 4096 || ext != 0 || raw < 1 || hdr+raw != len(b) {
		return errors.New("media_layout_unconfirmed")
	}
	frame := binary.LittleEndian.Uint64(b[12:20])
	ts := binary.LittleEndian.Uint64(b[41:49])
	payload := b[hdr:]
	kind := b[40]
	wire, err := p.MarshalBinary()
	if err != nil {
		return err
	}
	defer clear(wire)
	if _, err = io.MultiWriter(c.wire, c.wireHash).Write(wire); err != nil {
		return err
	}
	if _, err = io.MultiWriter(c.payload, c.payloadHash).Write(payload); err != nil {
		return err
	}
	c.Summary.Packets++
	c.Summary.WireBytes += int64(len(wire))
	c.Summary.PayloadBytes += int64(len(payload))
	if c.haveFrame && frame != c.lastFrame+1 {
		c.Summary.Discontinuities++
	}
	c.lastFrame = frame
	c.haveFrame = true
	switch kind {
	case 'I', 'P', 'B':
		if len(payload) < 4 || payload[0] != 0 || payload[1] != 0 {
			return errors.New("video_prefix_unconfirmed")
		}
		if _, err = c.video.Write(payload); err != nil {
			return err
		}
		if c.Summary.VideoFrames == 0 {
			c.Summary.FirstVideoTS = ts
		}
		c.Summary.LastVideoTS = ts
		c.Summary.VideoFrames++
		c.Summary.VideoBytes += int64(len(payload))
	case 'A':
		if len(payload) < 7 || payload[0] != 0xff || payload[1]&0xf0 != 0xf0 {
			return errors.New("audio_prefix_unconfirmed")
		}
		if _, err = c.audio.Write(payload); err != nil {
			return err
		}
		c.Summary.AudioFrames++
		c.Summary.AudioBytes += int64(len(payload))
	}
	if err = c.mux.Add(kind, ts, payload); err != nil {
		return err
	}
	return json.NewEncoder(c.sidecar).Encode(struct {
		Frame        uint64 `json:"frame"`
		Type         string `json:"type"`
		Timestamp    uint64 `json:"timestamp_raw"`
		HeaderBytes  int    `json:"header_bytes"`
		PayloadBytes int    `json:"payload_bytes"`
	}{frame, string([]byte{kind}), ts, hdr, raw})
}
func (c *Capture) Close() error {
	var first error
	for _, f := range []*os.File{c.wire, c.payload, c.video, c.audio, c.sidecar, c.transport} {
		if f != nil {
			if e := f.Sync(); e != nil && first == nil {
				first = e
			}
			if e := f.Close(); e != nil && first == nil {
				first = e
			}
		}
	}
	if c.mux != nil {
		c.Summary.PlayableVideoFrames = c.mux.VideoFrames
		c.Summary.PrerollVideoFrames = c.mux.PrerollVideo
	}
	if v, e := AnalyzeH264(filepath.Join(c.Dir, "video.h264")); e == nil {
		c.Summary.VideoInfo = &v
	}
	if c.wireHash != nil {
		c.Summary.WireSHA256 = hex.EncodeToString(c.wireHash.Sum(nil))
		c.Summary.PayloadSHA256 = hex.EncodeToString(c.payloadHash.Sum(nil))
		b, e := json.MarshalIndent(c.Summary, "", "  ")
		if e == nil {
			e = os.WriteFile(filepath.Join(c.Dir, "grid-analysis.json"), b, 0600)
		}
		if first == nil {
			first = e
		}
	}
	return first
}
