package iss

import (
	"bytes"
	"crypto/aes"
	"crypto/cipher"
	"encoding/binary"
	"errors"
	"strings"
)

// Credentials are never JSON-serializable. They are copied only from the
// current Gateway reply, consumed on its authorized ISS session, and scrubbed.
type Credentials struct {
	ticket         []byte
	seed           []byte
	guid           string
	joinLog        string
	broadcast      uint32
	streamPassword []byte
}

func (c *Credentials) Close() {
	if c != nil {
		clear(c.streamPassword)
		c.streamPassword = nil
		clear(c.ticket)
		clear(c.seed)
		c.ticket = nil
		c.seed = nil
		c.guid = ""
		c.joinLog = ""
		c.broadcast = 0
	}
}

// Gateway handler 0x140268250 receives 16 fixed bytes followed by a declared
// region consisting of ticket text, a 20-byte server token and 8 trailing bytes.
// The server token boundary is independently checked by sendCcpSvcAuth's hash.
func ParseGatewayCredentials(body []byte, guid, joinLog string, broadcast uint32) (*Credentials, error) {
	if len(body) < 44 || len(body) > 8192 {
		return nil, errors.New("certificate_body_bounds")
	}
	n := int(binary.LittleEndian.Uint32(body[12:16]))
	if n < 28 || n != len(body)-16 {
		return nil, errors.New("certificate_declared_length")
	}
	textLen := n - 28
	end := bytes.IndexByte(body[16:16+textLen], 0)
	if end < 1 {
		return nil, errors.New("certificate_text_termination")
	}
	seed := body[16+textLen : 16+textLen+20]
	if !validSeed(seed) {
		return nil, errors.New("server_seed_validation_failed")
	}
	if len(guid) < 1 || len(guid) > 73 || strings.ContainsRune(guid, 0) || broadcast == 0 {
		return nil, errors.New("session_input_bounds")
	}
	return &Credentials{ticket: append([]byte(nil), body[16:16+end]...), seed: append([]byte(nil), seed...), guid: guid, joinLog: joinLog, broadcast: broadcast}, nil
}

var authKey = [16]byte{0xe2, 0x08, 0x11, 0x29, 0xc4, 0xd0, 0xaf, 0xbe, 0x55, 0x37, 0x9f, 0xcd, 0xe1, 0x75, 0x54, 0x13}
var authIV = [16]byte{0x22, 0x65, 0x5d, 0x87, 0x96, 0xee, 0xca, 0x33, 0xc7, 0xa8, 0x22, 0x1d, 0xff, 0xcb, 0x82, 0x71}

// authBody reproduces sendCcpSvcAuth (0x14027b460), using the issued token's
// timestamp and a local counter. No server credential is generated here.
func authBody(c *Credentials, counter uint32) ([]byte, error) {
	if c == nil || !validSeed(c.seed) {
		return nil, errors.New("normal_server_seed_required")
	}
	guid := strings.ReplaceAll(c.guid, "-", "")
	input := make([]byte, 13+len(guid))
	binary.LittleEndian.PutUint32(input, 2)
	binary.LittleEndian.PutUint32(input[4:], 8)
	copy(input[12:], guid)
	defer clear(input)
	adjust := [6]byte{1, 255, 253, 254, 1, 2}
	var ts [4]byte
	for i := range ts {
		ts[i] = c.seed[i] - adjust[i%6]
	}
	length := uint32(len(input))
	mix := length | 0x08000000
	for i, b := range input {
		v := uint32(b) << uint((i%5&1)*16)
		if i&1 == 0 {
			mix |= v
		} else {
			mix ^= v
		}
	}
	stage1 := make([]byte, 28+len(input))
	stage1[0], stage1[4], stage1[8], stage1[12] = ts[0], ts[2], ts[3], ts[1]
	stage1[1], stage1[5], stage1[9], stage1[13] = byte(mix>>24), byte(mix>>8), byte(mix>>16), byte(mix)
	stage1[2], stage1[6], stage1[10], stage1[14] = byte(counter>>16), byte(counter), byte(counter>>8), byte(counter>>24)
	stage1[3], stage1[7], stage1[11], stage1[15] = byte(length>>8), byte(length), byte(length>>24), byte(length>>16)
	copy(stage1[16:], []byte{0x75, 0x6a, 0x5e, 0x74, 0x76, 0x66, 0x66, 0x67, 0x61, 0x34, 0x33, 0x37})
	copy(stage1[28:], input)
	first := ctrPadded(authKey[:], stage1)
	clear(stage1)
	stage2 := make([]byte, 18+len(first))
	copy(stage2, []byte{0x75, 0x6f, 0x6e, 0x6b, 0x74, 0x73, 0x71, 0x72, 0x60, 0x68, 0x72, 0x71, 0x67, 0x65, 0x69, 0x2f, 0x2e, 0x34})
	copy(stage2[18:], first)
	clear(first)
	padded := make([]byte, (len(stage2)+15)&^15)
	copy(padded, stage2)
	digest := wireHash(padded)
	clear(padded)
	second := ctrPadded(digest[:], stage2)
	clear(stage2)
	body := make([]byte, 8+16+len(second))
	binary.LittleEndian.PutUint32(body, 8)
	binary.LittleEndian.PutUint32(body[4:], uint32(16+len(second)))
	copy(body[8:], digest[:])
	copy(body[24:], second)
	clear(second)
	return body, nil
}

func ctrPadded(key, input []byte) []byte {
	padded := make([]byte, (len(input)+15)&^15)
	copy(padded, input)
	block, _ := aes.NewCipher(key) // all call sites supply fixed 16-byte keys
	cipher.NewCTR(block, authIV[:]).XORKeyStream(padded, padded)
	return padded
}

// sendCcpJoinchCommon at 0x14027c2d0: 20-byte inner header and NUL-terminated
// DC1/DC2-delimited session fields and zero advertised listen port.
func joinBody(c *Credentials) ([]byte, error) {
	if c == nil || len(c.ticket) == 0 || c.broadcast == 0 {
		return nil, errors.New("issued_ticket_required")
	}
	log := c.joinLog
	// 0x140279150 removes the liveualog record from the copied JOINLOG.
	if i := strings.Index(log, "liveualog\x11"); i >= 0 {
		j := strings.IndexByte(log[i:], 0x12)
		if j >= 0 {
			log = log[:i] + log[i+j+1:]
		}
	}
	text := log + "ticket\x11" + string(c.ticket) + "\x12quality\x11ori\x12is_auto\x11false\x12passwd\x11" + string(c.streamPassword) + "\x12"
	if len(text) > 8192 || strings.ContainsRune(text, 0) {
		return nil, errors.New("join_field_bounds")
	}
	b := make([]byte, 21+len(text))
	binary.LittleEndian.PutUint32(b[8:], c.broadcast)
	binary.LittleEndian.PutUint32(b[12:], 20)
	binary.LittleEndian.PutUint32(b[16:], uint32(len(text)))
	copy(b[20:], text)
	return b, nil
}

// SetStreamPassword reproduces the ASCII subset of PASSWORD -> UTF8/CP949
// conversion in 0x140358b20, called by 0x14027c2d0. Never log this field.
func (c *Credentials) SetStreamPassword(secret string) error {
	if c == nil || len(c.ticket) == 0 || len(secret) > 12 {
		return errors.New("invalid_stream_password_input")
	}
	for _, b := range []byte(secret) {
		if b < 0x20 || b > 0x7e {
			return errors.New("unsupported_stream_password_encoding")
		}
	}
	clear(c.streamPassword)
	c.streamPassword = append([]byte(nil), secret...)
	return nil
}
