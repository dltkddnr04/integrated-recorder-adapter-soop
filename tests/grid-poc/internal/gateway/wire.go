package gateway

import (
	"crypto/rand"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"strconv"
	"strings"
)

const (
	WireHeaderSize = 16
	LoginCommand   = 0x3e2
	PreludeCommand = 0x3df
	CertCommand    = 0x415
	MaxFrameBody   = 1 << 20
	MaxFanTicket   = 4096
)

var (
	ErrGatewayChecksum = errors.New("Gateway frame checksum mismatch")
	ErrGatewayFrame    = errors.New("invalid Gateway frame")
)

// Frame is the 16-byte Gateway wire format described in the live ytv1 trace.
// This format is separate from the 16-bit 0x0202 Parent packet header.
type Frame struct {
	Command         uint32
	Reserved        uint32
	BodySize        uint32
	Check           uint32
	ChecksumVariant string
	NegativeReply   bool
	RawHeader       [WireHeaderSize]byte
	Body            []byte
}

func NewFrame(command uint32, body []byte) (Frame, error) {
	if len(body) > MaxFrameBody {
		return Frame{}, fmt.Errorf("Gateway body exceeds %d bytes", MaxFrameBody)
	}
	f := Frame{Command: command, BodySize: uint32(len(body)), Body: append([]byte(nil), body...)}
	f.Check = command ^ f.BodySize
	return f, nil
}

func (f Frame) MarshalBinary() ([]byte, error) {
	if len(f.Body) > MaxFrameBody || uint32(len(f.Body)) != f.BodySize {
		return nil, ErrGatewayFrame
	}
	if f.Check != f.Command^f.BodySize {
		return nil, ErrGatewayChecksum
	}
	b := make([]byte, WireHeaderSize+len(f.Body))
	binary.LittleEndian.PutUint32(b[0:4], f.Command)
	binary.LittleEndian.PutUint32(b[4:8], f.Reserved)
	binary.LittleEndian.PutUint32(b[8:12], f.BodySize)
	binary.LittleEndian.PutUint32(b[12:16], f.Check)
	copy(b[WireHeaderSize:], f.Body)
	return b, nil
}

// ReadFrame reads a single bounded frame and returns the exact bytes consumed.
func ReadFrame(r io.Reader, maxBody uint32) (Frame, int, error) {
	if maxBody == 0 || maxBody > MaxFrameBody {
		maxBody = MaxFrameBody
	}
	var raw [WireHeaderSize]byte
	n, err := io.ReadFull(r, raw[:])
	if err != nil {
		return Frame{}, n, err
	}
	f := Frame{
		Command:   binary.LittleEndian.Uint32(raw[0:4]),
		Reserved:  binary.LittleEndian.Uint32(raw[4:8]),
		BodySize:  binary.LittleEndian.Uint32(raw[8:12]),
		Check:     binary.LittleEndian.Uint32(raw[12:16]),
		RawHeader: raw,
	}
	if f.BodySize > maxBody {
		return f, n, fmt.Errorf("Gateway body size %d exceeds limit %d", f.BodySize, maxBody)
	}
	f.Body = make([]byte, int(f.BodySize))
	nBody, err := io.ReadFull(r, f.Body)
	n += nBody
	if err != nil {
		clear(f.Body)
		return f, n, err
	}
	expected := f.Command ^ f.BodySize
	switch {
	case f.Check == expected:
		f.ChecksumVariant = "xor"
	case f.Reserved == ^uint32(0) && f.Check == ^expected:
		f.ChecksumVariant = "ones_complement_xor"
		f.NegativeReply = true
	default:
		return f, n, ErrGatewayChecksum
	}
	return f, n, nil
}

// NewLoginFrame builds the 78-byte P1 login packet from the captured public
// template. The two fixed marker/length words remain opaque protocol values.
func NewLoginFrame(guid string) (Frame, error) {
	if len(guid) != 32 {
		return Frame{}, errors.New("Gateway GUID must be 32 hexadecimal characters")
	}
	if _, err := hex.DecodeString(guid); err != nil {
		return Frame{}, errors.New("Gateway GUID must be hexadecimal")
	}
	addInfo := []byte{0x00}
	addInfo = append(addInfo, "ad_lang"...)
	addInfo = append(addInfo, 0x11)
	addInfo = append(addInfo, "ko"...)
	addInfo = append(addInfo, 0x12)
	addInfo = append(addInfo, "is_auto"...)
	addInfo = append(addInfo, 0x11)
	addInfo = append(addInfo, '0', 0x12)

	body := make([]byte, 0, 62)
	body = append(body, 0x2a, 0x00, 0x02, 0x00)
	body = append(body, guid...)
	body = append(body, 0x15, 0x00, 0x00, 0x00)
	body = append(body, addInfo...)
	if len(body) != 62 {
		clear(body)
		return Frame{}, fmt.Errorf("captured Gateway login body is %d bytes, expected 62", len(body))
	}
	return NewFrame(LoginCommand, body)
}

// NewNativeLoginFrame reproduces the fixed body layout observed in candidate
// VA 0x140265420. addInfo must already contain the serialized dynamic fields;
// this function does not infer their values. Candidate VA 0x263c90 sends a
// 0x03df/service-6 frame first. One live 0x03df→0x03e2 sequence received a
// 0x03e2 reply with RESULT=0 and a structurally valid body. The native handler
// forwards RESULT without branching on it, so the success interpretation is
// qualified; see GATEWAY_LOGIN_DECODER.md.
func NewNativeLoginFrame(guid string, addInfo []byte) (Frame, error) {
	if len(guid) != 32 {
		return Frame{}, errors.New("native Gateway GUID must be 32 hexadecimal characters")
	}
	if _, err := hex.DecodeString(guid); err != nil {
		return Frame{}, errors.New("native Gateway GUID must be hexadecimal")
	}
	if len(addInfo) > MaxFrameBody-0x29 {
		return Frame{}, errors.New("native Gateway add-info exceeds frame limit")
	}
	body := make([]byte, 0x29+len(addInfo))
	binary.LittleEndian.PutUint32(body[0:4], 0x0002002a)
	copy(body[4:36], guid)
	// body[36] is the native builder's zero terminator for the GUID.
	binary.LittleEndian.PutUint32(body[0x25:0x29], uint32(len(addInfo)))
	copy(body[0x29:], addInfo)
	return NewFrame(LoginCommand, body)
}

// NativeLoginFields are the externally supplied string fields consumed by the
// candidate's buildFlashLoginPacket at VA 0x140265420. Cookie and BJID have
// explicit native templates; optional add-info and JOINLOG strings are passed
// through the candidate's empty-format append path when non-empty.
type NativeLoginFields struct {
	GUID         string
	Cookie       string
	CookieSource string // allowlisted evidence label only; never serialized into the wire
	BJID         string
	AddInfo      []byte
	JoinLog      []byte
}

// BuildNativeLoginAddInfo applies the statically observed candidate order:
// cookie\x11<cookie>\x12, bj\x11<BJID>\x12, optional raw add-info, then raw
// JOINLOG. Category and FanTicket are setup fields but are not read by this
// P1 builder. Empty-format pass-through is inferred from the call convention
// and the empty format literal at 0x1407fb28c; retain this qualification.
func BuildNativeLoginAddInfo(fields NativeLoginFields) ([]byte, error) {
	if len(fields.Cookie) > 4096 || strings.ContainsAny(fields.Cookie, "\x00\x11\x12") {
		return nil, errors.New("native Gateway cookie field is invalid")
	}
	if fields.BJID == "" || len(fields.BJID) > 128 || !isASCII(fields.BJID) || strings.ContainsAny(fields.BJID, "\x00\x11\x12") {
		return nil, errors.New("native Gateway BJID must be 1-128 ASCII bytes without field delimiters")
	}
	if len(fields.AddInfo) > 1<<16 || len(fields.JoinLog) > 1<<16 || len(fields.AddInfo)+len(fields.JoinLog) > 1<<17 {
		return nil, errors.New("native Gateway optional login fields exceed the size limit")
	}
	if bytesContainNUL(fields.AddInfo) || bytesContainNUL(fields.JoinLog) {
		return nil, errors.New("native Gateway optional login fields must not contain NUL")
	}
	addInfo := make([]byte, 0, len("cookie\x11\x12")+len(fields.Cookie)+len("bj\x11\x12")+len(fields.BJID)+len(fields.AddInfo)+len(fields.JoinLog))
	addInfo = append(addInfo, "cookie\x11"...)
	addInfo = append(addInfo, fields.Cookie...)
	addInfo = append(addInfo, 0x12)
	addInfo = append(addInfo, "bj\x11"...)
	addInfo = append(addInfo, fields.BJID...)
	addInfo = append(addInfo, 0x12)
	addInfo = append(addInfo, fields.AddInfo...)
	addInfo = append(addInfo, fields.JoinLog...)
	return addInfo, nil
}

// NewNativeLoginFrameFromFields builds the complete candidate P1 request from
// the supplied GUID/cookie/BJID and optional pass-through fields.
func NewNativeLoginFrameFromFields(fields NativeLoginFields) (Frame, error) {
	addInfo, err := BuildNativeLoginAddInfo(fields)
	if err != nil {
		return Frame{}, err
	}
	defer clear(addInfo)
	return NewNativeLoginFrame(fields.GUID, addInfo)
}

func bytesContainNUL(b []byte) bool {
	for _, value := range b {
		if value == 0 {
			return true
		}
	}
	return false
}

// NewGatewayPreludeFrame wraps a caller-supplied service-6 payload in the
// candidate's 0x03df body layout. The candidate writer at 0x1401dcce0 confirms
// the outer header/check calculation. BuildService6Payload reproduces the
// static transform offline; this is not live-acceptance evidence and does not
// supply missing normal session inputs.
func NewGatewayPreludeFrame(service6Payload []byte) (Frame, error) {
	if len(service6Payload) > MaxFrameBody-8 {
		return Frame{}, errors.New("Gateway prelude payload exceeds frame limit")
	}
	body := make([]byte, 8+len(service6Payload))
	binary.LittleEndian.PutUint32(body[0:4], 6)
	binary.LittleEndian.PutUint32(body[4:8], uint32(len(service6Payload)))
	copy(body[8:], service6Payload)
	return NewFrame(PreludeCommand, body)
}

// CertTicketFields holds a normal Gateway certificate request. FanTicket is
// sensitive and is kept only in memory. The direct GatewaySocket writer +8
// calls in VAs 0x1402658b0/0x14026c690 establish outbound TCP direction;
// gateway/certificate.go has also verified the current live response.
type CertTicketFields struct {
	SessionID  uint32
	TicketType uint32
	BJID       string
	Category   string
	FanTicket  []byte
}

// NewCertTicketFrame mirrors the 0x47+FanTicket body constructed by native
// handleLoginAndSendCertTicket/sendAgentGwSvcCertTicketx. SessionID is the
// current broadcast number on the verified public-viewing request path.
func NewCertTicketFrame(fields CertTicketFields) (Frame, error) {
	if fields.TicketType == 0 {
		return Frame{}, errors.New("Gateway TicketType is required")
	}
	if fields.BJID == "" || !isASCII(fields.BJID) || strings.ContainsRune(fields.BJID, '\x00') {
		return Frame{}, errors.New("Gateway BJID must be non-empty ASCII without NUL")
	}
	if len(fields.FanTicket) > MaxFanTicket {
		return Frame{}, errors.New("Gateway FanTicket exceeds the allowed length")
	}
	category, err := strconv.ParseUint(strings.TrimSpace(fields.Category), 10, 32)
	if err != nil {
		return Frame{}, errors.New("Gateway Category is not an unsigned decimal value")
	}

	body := make([]byte, 0x47+len(fields.FanTicket))
	binary.LittleEndian.PutUint32(body[0x00:0x04], fields.SessionID)
	binary.LittleEndian.PutUint32(body[0x04:0x08], fields.TicketType)
	bjid := fields.BJID
	if len(bjid) > 25 {
		bjid = bjid[:25]
	}
	copy(body[0x21:0x3a], bjid)
	binary.LittleEndian.PutUint32(body[0x3b:0x3f], 0x2a)
	binary.LittleEndian.PutUint32(body[0x3f:0x43], uint32(category))
	binary.LittleEndian.PutUint32(body[0x43:0x47], uint32(len(fields.FanTicket)))
	copy(body[0x47:], fields.FanTicket)
	return NewFrame(CertCommand, body)
}

func NewGUID() (string, error) {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", errors.New("could not generate Gateway session GUID")
	}
	return strings.ToUpper(hex.EncodeToString(b[:])), nil
}

func NewSessionID() (uint32, error) {
	var b [4]byte
	if _, err := rand.Read(b[:]); err != nil {
		return 0, errors.New("could not generate Gateway session id")
	}
	return binary.LittleEndian.Uint32(b[:]), nil
}

func isASCII(s string) bool {
	for i := 0; i < len(s); i++ {
		if s[i] < 0x20 || s[i] > 0x7e {
			return false
		}
	}
	return true
}
