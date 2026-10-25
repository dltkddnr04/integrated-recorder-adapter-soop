package iss

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"sort"
	"strconv"
	"time"
)

type Event struct {
	Direction string       `json:"direction"`
	Command   string       `json:"command"`
	BodyBytes int          `json:"body_bytes"`
	WireBytes int          `json:"wire_bytes"`
	Result    int32        `json:"result,omitempty"`
	Detail    *ReplyDetail `json:"detail,omitempty"`
}

type Result struct {
	Connected             bool          `json:"connected"`
	Events                []Event       `json:"events"`
	Outcome               string        `json:"outcome"`
	Parent                *ParentResult `json:"parent,omitempty"`
	SensitiveValuesLogged bool          `json:"sensitive_values_logged"`
}

type ReceiveOptions struct {
	OutputDir    string
	Duration     time.Duration
	MaxWireBytes int64
}

func Receive(ctx context.Context, address string, c *Credentials, options ReceiveOptions) Result {
	return connectAndReceive(ctx, address, c, options)
}
func connectAndReceive(ctx context.Context, address string, credentials *Credentials, options ReceiveOptions) (out Result) {
	out.Outcome = "not_attempted"
	host, port, err := net.SplitHostPort(address)
	ip := net.ParseIP(host)
	p, pe := strconv.Atoi(port)
	if err != nil || pe != nil || p < 1 || p > 65535 || ip == nil || !ip.IsGlobalUnicast() || ip.IsPrivate() || ip.IsLoopback() {
		out.Outcome = "invalid_api_endpoint"
		return
	}
	c, err := (&net.Dialer{Timeout: 3 * time.Second}).DialContext(ctx, "tcp", address)
	if err != nil {
		out.Outcome = "connect_failed"
		return
	}
	defer c.Close()
	out.Connected = true
	_ = c.SetDeadline(time.Now().Add(4 * time.Second))
	wire, err := Encode(2, []byte{0})
	if err != nil {
		out.Outcome = "encode_failed"
		return
	}
	n, err := c.Write(wire)
	clear(wire)
	if err != nil || n != 21 {
		out.Outcome = "init_write_failed"
		return
	}
	out.Events = append(out.Events, Event{Direction: "send", Command: "0x0002", BodyBytes: 1, WireBytes: n})
	f, err := Read(c)
	defer clear(f.Body)
	if err != nil {
		var ne net.Error
		switch {
		case errors.As(err, &ne) && ne.Timeout():
			out.Outcome = "init_read_timeout"
		case errors.Is(err, io.EOF), errors.Is(err, io.ErrUnexpectedEOF):
			out.Outcome = "init_closed_or_truncated"
		default:
			out.Outcome = "init_framing_or_read_error"
		}
		return
	}
	out.Events = append(out.Events, Event{Direction: "receive", Command: fmt.Sprintf("0x%04x", f.Command), BodyBytes: len(f.Body), WireBytes: 20 + len(f.Body), Result: f.Result, Detail: inspectReply(f)})
	out.Outcome = "init_reply_received"
	if f.Command != 2 || f.Result < 0 || len(f.Body) < 8 {
		out.Outcome = "init_unexpected_or_rejected"
		return
	}
	clear(f.Body)
	// handleSvc2 at VA 0x14027aa01 calls the writer with op=3,
	// pointer=nil and length=0. Do not replay the historical opaque 0xd4.
	sentAt := time.Now()
	if err := send(c, 3, nil, &out); err != nil {
		out.Outcome = "op3_write_failed"
		return
	}
	for i := 0; i < 4; i++ {
		f, err = receive(c, &out)
		if err != nil {
			out.Outcome = "op3_read_failed"
			return
		}
		if f.Result < 0 {
			clear(f.Body)
			out.Outcome = "server_rejected"
			return
		}
		if f.Command == 3 {
			break
		}
		clear(f.Body)
	}
	defer clear(f.Body)
	if f.Command != 3 || len(f.Body) < 8 {
		out.Outcome = "op3_missing"
		return
	}
	nodeKey := binary.LittleEndian.Uint32(f.Body[4:8])
	// handleSvc3 at 0x14027d07a writes u16(4), u16(RTT/2), u32(0),
	// then sends service 0x3e through the CenterSocket writer.
	config := make([]byte, 8)
	binary.LittleEndian.PutUint16(config, 4)
	binary.LittleEndian.PutUint16(config[2:], uint16(time.Since(sentAt).Milliseconds()/2))
	if err := send(c, 0x3e, config, &out); err != nil {
		out.Outcome = "config_write_failed"
		return
	}
	auth, err := authBody(credentials, 1)
	if err != nil {
		out.Outcome = "auth_build_failed"
		return
	}
	defer clear(auth)
	// sendUpdateUploadSpeed: zero offered upload bandwidth, no peer listener.
	upload := make([]byte, 12)
	if err = send(c, 0x14, upload, &out); err != nil {
		out.Outcome = "capability_write_failed"
		return
	}
	if err = send(c, 0x2a, auth, &out); err != nil {
		out.Outcome = "auth_write_failed"
		return
	}
	join, err := joinBody(credentials)
	if err != nil {
		out.Outcome = "join_build_failed"
		return
	}
	defer clear(join)
	if err = send(c, 0x46, join, &out); err != nil {
		out.Outcome = "join_write_failed"
		return
	}
	var handle []byte
	requested := false
	for i := 0; i < 24; i++ {
		clear(f.Body)
		f, err = receive(c, &out)
		if err != nil {
			out.Outcome = "post_join_read_failed"
			return
		}
		if f.Result < 0 {
			out.Outcome = "server_rejected"
			return
		}
		if f.Command == 0x4a && len(f.Body) >= 52 {
			handle = append([]byte(nil), f.Body[:4]...)
			defer clear(handle)
		}
		if f.Command == 0x4a || f.Command == 0x46 || f.Command == 0x4d {
			out.Outcome = "join_related_reply_received"
		}
		if f.Command == 0x46 && len(handle) == 4 && !requested {
			requested = true
			ports := jsonBody([]byte(`{"broad_no":0,"relay_port":0,"reverse_port":0}`))
			if send(c, 0x71, ports, &out) != nil || send(c, 0x50, handle, &out) != nil {
				out.Outcome = "request_broadcast_write_failed"
				return
			}
		}
		if f.Command == 0x69 {
			parent := receiveParent(ctx, f.Body, credentials.broadcast, nodeKey, options)
			out.Parent = &parent
			out.Outcome = parent.Outcome
			return
		}
		if f.Command == 0x50 {
			nodes := jsonBody([]byte(`{"list":[{"alloc_type":1,"parent_ip":0,"parent_port":0,"quality":1}]}`))
			if send(c, 0x69, nodes, &out) != nil {
				out.Outcome = "discovery_write_failed"
				return
			}
		}
	}
	return
}

func send(c net.Conn, command uint32, body []byte, out *Result) error {
	wire, err := Encode(command, body)
	if err != nil {
		return err
	}
	defer clear(wire)
	left := wire
	for len(left) > 0 {
		n, e := c.Write(left)
		if e != nil {
			return e
		}
		if n == 0 {
			return io.ErrShortWrite
		}
		left = left[n:]
	}
	out.Events = append(out.Events, Event{Direction: "send", Command: fmt.Sprintf("0x%04x", command), BodyBytes: len(body), WireBytes: len(wire)})
	return nil
}

func receive(c net.Conn, out *Result) (Frame, error) {
	_ = c.SetReadDeadline(time.Now().Add(3 * time.Second))
	f, err := Read(c)
	if err == nil {
		out.Events = append(out.Events, Event{Direction: "receive", Command: fmt.Sprintf("0x%04x", f.Command), BodyBytes: len(f.Body), WireBytes: 20 + len(f.Body), Result: f.Result, Detail: inspectReply(f)})
	}
	return f, err
}

type ReplyDetail struct {
	JSONKeys          []string `json:"json_keys,omitempty"`
	DeclaredLengths   []int    `json:"bounded_json_offsets,omitempty"`
	ApplicationResult *int     `json:"application_result,omitempty"`
}

func jsonBody(text []byte) []byte {
	b := make([]byte, 4+len(text))
	binary.LittleEndian.PutUint32(b, uint32(len(text)))
	copy(b[4:], text)
	return b
}
func inspectReply(f Frame) *ReplyDetail {
	d := &ReplyDetail{}
	for off := 0; off <= 32 && off+4 <= len(f.Body); off += 4 {
		n := int(binary.LittleEndian.Uint32(f.Body[off:]))
		if n < 2 || n > len(f.Body)-off-4 {
			continue
		}
		var m map[string]json.RawMessage
		if json.Unmarshal(bytes.TrimRight(f.Body[off+4:off+4+n], "\x00"), &m) == nil && m != nil {
			d.DeclaredLengths = append(d.DeclaredLengths, off)
			for k := range m {
				if k == "RESULT" || k == "result" || k == "message" || k == "list" || k == "title" {
					d.JSONKeys = append(d.JSONKeys, k)
				}
			}
			if v, ok := m["RESULT"]; ok {
				var v2 int
				if json.Unmarshal(v, &v2) == nil {
					d.ApplicationResult = &v2
				}
			}
		}
	}
	sort.Strings(d.JSONKeys)
	if len(d.JSONKeys) == 0 {
		return nil
	}
	return d
}
