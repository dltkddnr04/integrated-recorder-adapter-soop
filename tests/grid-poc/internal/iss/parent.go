package iss

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"github.com/integrated-recorder/source.soop/tests/grid-poc/internal/media"
	"github.com/integrated-recorder/source.soop/tests/grid-poc/internal/protocol"
	"net"
	"path/filepath"
	"strconv"
	"time"
)

type parentEntry struct {
	IP      uint32 `json:"parent_ip"`
	Port    uint32 `json:"parent_port"`
	Session uint32 `json:"parent_sess"`
	Quality uint32 `json:"quality"`
	Frame   uint64 `json:"cur_frame_no"`
	Wait    uint32 `json:"child_wait"`
}
type ParentResult struct {
	Entries        int                   `json:"entries"`
	Keys           []string              `json:"entry_keys,omitempty"`
	Quality        uint32                `json:"quality,omitempty"`
	StreamQuality  uint32                `json:"stream_quality_selected,omitempty"`
	FrameAvailable bool                  `json:"frame_available"`
	EndpointValid  bool                  `json:"endpoint_valid"`
	Connected      bool                  `json:"connected"`
	Outcome        string                `json:"outcome"`
	Capture        *media.CaptureSummary `json:"capture,omitempty"`
	Events         []Event               `json:"events,omitempty"`
}

func captureWireLimit(configured int64) int64 {
	if configured <= 0 || configured > 512<<20 {
		return 64 << 20
	}
	return configured
}

func parseParents(body []byte) ([]parentEntry, []string, error) {
	if len(body) < 4 {
		return nil, nil, fmt.Errorf("node_short")
	}
	n := int(binary.LittleEndian.Uint32(body))
	if n > len(body)-4 {
		return nil, nil, fmt.Errorf("node_length")
	}
	var v struct {
		List []json.RawMessage `json:"list"`
	}
	if json.Unmarshal(bytes.TrimRight(body[4:4+n], "\x00"), &v) != nil {
		return nil, nil, fmt.Errorf("node_json")
	}
	if len(v.List) > 8 {
		return nil, nil, fmt.Errorf("node_limit")
	}
	var entries []parentEntry
	var keys []string
	for _, r := range v.List {
		var p parentEntry
		if json.Unmarshal(r, &p) != nil {
			return nil, nil, fmt.Errorf("node_fields")
		}
		entries = append(entries, p)
		var fields map[string]json.RawMessage
		json.Unmarshal(r, &fields)
		for _, k := range []string{"parent_ip", "parent_port", "parent_sess", "quality", "cur_frame_no", "cur_pts", "child_wait", "skip_frame", "cur_seq_audio", "cur_seq_video"} {
			if _, ok := fields[k]; ok {
				keys = append(keys, k)
			}
		}
	}
	return entries, keys, nil
}
func receiveParent(ctx context.Context, body []byte, bno, key uint32, options ReceiveOptions) (out ParentResult) {
	out.Outcome = "no_usable_parent"
	entries, keys, err := parseParents(body)
	out.Keys = keys
	out.Entries = len(entries)
	if err != nil {
		out.Outcome = err.Error()
		return
	}
	for _, p := range entries {
		var b [4]byte
		binary.LittleEndian.PutUint32(b[:], p.IP)
		ip := net.IP(b[:])
		port := int((p.Port&255)<<8 | (p.Port>>8)&255)
		out.Quality = p.Quality
		out.StreamQuality = p.Quality
		out.FrameAvailable = p.Frame > 0
		if p.Port > 65535 || port == 0 || !ip.IsGlobalUnicast() || ip.IsPrivate() || ip.IsLoopback() {
			continue
		}
		out.EndpointValid = true
		c, e := (&net.Dialer{Timeout: 3 * time.Second}).DialContext(ctx, "tcp", net.JoinHostPort(ip.String(), strconv.Itoa(port)))
		if e != nil {
			out.Outcome = "parent_connect_failed"
			return
		}
		defer c.Close()
		stop := context.AfterFunc(ctx, func() { c.Close() })
		defer stop()
		out.Connected = true
		sendPeer := func(command uint16, data []byte) error {
			packet, e := protocol.NewPacket(command, 0, data)
			if e != nil {
				return e
			}
			wire, e := packet.MarshalBinary()
			if e != nil {
				return e
			}
			defer clear(wire)
			c.SetWriteDeadline(time.Now().Add(2 * time.Second))
			n, e := c.Write(wire)
			if e == nil && n != len(wire) {
				e = fmt.Errorf("short_write")
			}
			if e == nil {
				if len(out.Events) < 64 {
					out.Events = append(out.Events, Event{Direction: "send", Command: fmt.Sprintf("0x%04x", command), BodyBytes: len(data), WireBytes: n})
				}
			}
			return e
		}
		request := make([]byte, 12)
		binary.LittleEndian.PutUint32(request, bno)
		binary.LittleEndian.PutUint32(request[4:], key)
		binary.LittleEndian.PutUint32(request[8:], p.Quality)
		if sendPeer(0xcb2e, request) != nil {
			out.Outcome = "stream_write_failed"
			return
		}
		clear(request)
		var capture *media.Capture
		defer func() {
			if capture != nil {
				if capture.Close() != nil {
					out.Outcome = "capture_close_failed"
				}
				v := capture.Summary
				out.Capture = &v
			}
		}()
		duration := options.Duration
		if duration <= 0 || duration > 60*time.Second {
			duration = 35 * time.Second
		}
		maxWireBytes := captureWireLimit(options.MaxWireBytes)
		deadline := time.Now().Add(duration)
		lastFrame := p.Frame
		requests := 0
		liveStarted := false
		requestCache := func() error {
			if requests >= 45 {
				return fmt.Errorf("cache_request_limit")
			}
			requests++
			data := make([]byte, 16)
			binary.LittleEndian.PutUint32(data, p.Quality)
			binary.LittleEndian.PutUint64(data[4:], lastFrame+1)
			status := uint32(6)
			if requests == 1 {
				status = 0xffffffff
			}
			binary.LittleEndian.PutUint32(data[12:], status)
			return sendPeer(0xcb30, data)
		}
		for i := 0; i < 8192 && time.Now().Before(deadline) && ctx.Err() == nil; i++ {
			c.SetReadDeadline(time.Now().Add(3 * time.Second))
			f, e := protocol.ReadPacket(c, protocol.MaxBodyBytes)
			if e != nil {
				out.Outcome = "parent_read_failed"
				return
			}
			if len(out.Events) < 32 {
				out.Events = append(out.Events, Event{Direction: "receive", Command: fmt.Sprintf("0x%04x", f.Header.Command), BodyBytes: len(f.Body), WireBytes: 16 + len(f.Body)})
			}
			switch f.Header.Command {
			case 0xc746, 0x1bc7:
				data := make([]byte, 4)
				binary.LittleEndian.PutUint32(data, p.Quality)
				if sendPeer(0xcb2f, data) != nil {
					clear(f.Body)
					out.Outcome = "cache_header_write_failed"
					return
				}
			case 0xc747:
				if requestCache() != nil {
					clear(f.Body)
					out.Outcome = "cache_write_failed"
					return
				}

			case 0xc742, 0xc748:
				if f.Header.Command == 0xc742 {
					// Native handlePushFrame -> 0x1402f5c70: one zero-body
					// 0xCB2B switches cached frames to the live push flow.
					if !liveStarted {
						liveStarted = true
						if sendPeer(0xcb2b, nil) != nil {
							clear(f.Body)
							out.Outcome = "live_push_start_failed"
							return
						}
					}
					clear(f.Body)
					continue
				}
				if len(f.Body) < 69 {
					clear(f.Body)
					out.Outcome = "unexpected_short_cache_media"
					return
				}

				if capture == nil {
					dir := filepath.Join("..", "captures", "grid-"+time.Now().UTC().Format("20060102T150405.000000000Z"))
					if options.OutputDir != "" {
						dir = options.OutputDir
					}
					capture, e = media.NewCapture(dir)
					if e != nil {
						clear(f.Body)
						out.Outcome = "capture_create_failed"
						return
					}
				}
				if e = capture.Add(f); e != nil {
					clear(f.Body)
					out.Outcome = e.Error()
					return
				}
				out.Outcome = "grid_media_saved"
				lastFrame = binary.LittleEndian.Uint64(f.Body[12:20])
				if capture.Summary.WireBytes >= maxWireBytes {
					clear(f.Body)
					out.Outcome = "grid_media_saved_size_limit"
					return
				}

			default:
				out.Outcome = "unexpected_parent_command"
			}
			clear(f.Body)
		}
		return
	}
	return
}
