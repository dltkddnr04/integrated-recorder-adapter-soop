package gateway

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"strconv"
	"syscall"
	"time"
)

const maxGatewayReplyBody = 64 << 10

// SequenceResult contains only sanitized framing and transport facts. No
// endpoint, GUID, cookie, ticket, or response body is retained.
type SequenceResult struct {
	SessionMode           string              `json:"session_mode"`
	Attempted             bool                `json:"attempted"`
	Connected             bool                `json:"connected"`
	GUIDGeneratedLocally  bool                `json:"guid_generated_locally"`
	CookieFromPublicAU    bool                `json:"cookie_from_public_au_cookie_hypothesis"`
	CookieSource          string              `json:"gateway_cookie_source"`
	BJIDFromLiveChannel   bool                `json:"bjid_from_live_channel"`
	OptionalAddInfoEmpty  bool                `json:"optional_addinfo_empty"`
	JoinLogEmpty          bool                `json:"joinlog_empty"`
	PreludeCommand        string              `json:"prelude_command,omitempty"`
	PreludeBytes          int                 `json:"prelude_frame_bytes,omitempty"`
	PreludeWritten        bool                `json:"prelude_fully_written"`
	LoginCommand          string              `json:"login_command,omitempty"`
	LoginBytes            int                 `json:"login_frame_bytes,omitempty"`
	LoginWritten          bool                `json:"login_fully_written"`
	ReplyBytes            int                 `json:"reply_bytes_received"`
	ReplyFrameComplete    bool                `json:"reply_frame_complete"`
	ReplyCommand          string              `json:"reply_command,omitempty"`
	ReplyReserved         string              `json:"reply_reserved,omitempty"`
	ReplyBodyBytes        int                 `json:"reply_body_bytes,omitempty"`
	ReplyCheck            string              `json:"reply_check,omitempty"`
	ReplyChecksumVariant  string              `json:"reply_checksum_variant,omitempty"`
	ReplyNegative         bool                `json:"reply_negative_frame"`
	ReplyApplication      *LoginReplyAnalysis `json:"reply_application_analysis,omitempty"`
	GatewayLoginAccepted  bool                `json:"gateway_login_accepted"`
	ReadOutcome           string              `json:"read_outcome"`
	Interpretation        string              `json:"interpretation,omitempty"`
	FailureStage          string              `json:"failure_stage,omitempty"`
	FailureClass          string              `json:"failure_class,omitempty"`
	DurationMS            int64               `json:"duration_ms"`
	SensitiveValuesLogged bool                `json:"sensitive_values_logged"`
}

// loginPreludeThenCertificate performs the normal Gateway login sequence and
// continues on the same socket to consume the server-issued certificate.
// The 0x0415 direction is established by the direct GatewaySocket vtable +8
// calls in handleLoginAndSendCertTicket and sendAgentGwSvcCertTicketx.
func loginPreludeThenCertificate(ctx context.Context, address string, fields NativeLoginFields, timestamp, counter uint32, afterLogin func(net.Conn)) (result SequenceResult) {
	started := time.Now()
	cookieSource := fields.CookieSource
	switch cookieSource {
	case "live_api_TK", "scoped_AuthTicket_cookie":
	case "":
		cookieSource = "anonymous_au_hypothesis"
	default:
		cookieSource = "caller_source_unverified"
	}
	result = SequenceResult{
		SessionMode:           "gateway_login_and_certificate",
		GUIDGeneratedLocally:  true,
		CookieFromPublicAU:    cookieSource == "anonymous_au_hypothesis",
		CookieSource:          cookieSource,
		BJIDFromLiveChannel:   true,
		OptionalAddInfoEmpty:  len(fields.AddInfo) == 0,
		JoinLogEmpty:          len(fields.JoinLog) == 0,
		PreludeCommand:        "0x03df",
		LoginCommand:          "0x03e2",
		ReadOutcome:           "not_attempted",
		SensitiveValuesLogged: false,
	}
	defer func() { result.DurationMS = time.Since(started).Milliseconds() }()
	if _, _, err := validatedPublicEndpoint(address); err != nil {
		result.FailureStage, result.FailureClass = "preflight", "api_endpoint_invalid"
		result.ReadOutcome = "not_attempted"
		return result
	}
	prelude, err := NewNativeService6Frame(fields.GUID, timestamp, counter)
	if err != nil {
		result.FailureStage, result.FailureClass = "preflight_service6", "packet_build_error"
		result.ReadOutcome = "not_attempted"
		return result
	}
	preludeWire, err := prelude.MarshalBinary()
	if err != nil {
		result.FailureStage, result.FailureClass = "preflight_service6", "packet_encode_error"
		result.ReadOutcome = "not_attempted"
		return result
	}
	result.PreludeBytes = len(preludeWire)
	login, err := NewNativeLoginFrameFromFields(fields)
	if err != nil {
		clear(preludeWire)
		result.FailureStage, result.FailureClass = "preflight_login", "packet_build_error"
		result.ReadOutcome = "not_attempted"
		return result
	}
	loginWire, err := login.MarshalBinary()
	if err != nil {
		clear(preludeWire)
		result.FailureStage, result.FailureClass = "preflight_login", "packet_encode_error"
		result.ReadOutcome = "not_attempted"
		return result
	}
	result.LoginBytes = len(loginWire)
	defer clear(preludeWire)
	defer clear(loginWire)
	result.Attempted = true

	dialer := net.Dialer{Timeout: 3 * time.Second}
	conn, err := dialer.DialContext(ctx, "tcp", address)
	if err != nil {
		result.FailureStage, result.FailureClass = "tcp_connect", classifyNetworkError(err)
		result.ReadOutcome = "not_read"
		return result
	}
	result.Connected = true
	defer conn.Close()
	_ = conn.SetWriteDeadline(time.Now().Add(2 * time.Second))
	if err := writeAll(conn, preludeWire); err != nil {
		result.FailureStage, result.FailureClass = "write_service6", classifyNetworkError(err)
		result.ReadOutcome = "not_read"
		return result
	}
	result.PreludeWritten = true
	if err := writeAll(conn, loginWire); err != nil {
		result.FailureStage, result.FailureClass = "write_p1", classifyNetworkError(err)
		result.ReadOutcome = "not_read"
		return result
	}
	result.LoginWritten = true
	_ = conn.SetReadDeadline(time.Now().Add(4 * time.Second))
	frame, readBytes, readErr := ReadFrame(conn, maxGatewayReplyBody)
	result.ReplyBytes = readBytes
	if readErr != nil {
		result.FailureStage = "read_gateway_reply"
		result.FailureClass = classifyFrameReadError(readErr)
		result.ReadOutcome = result.FailureClass
		if frame.Command != 0 || frame.BodySize != 0 {
			populateReplyHeader(&result, frame)
		}
		clear(frame.Body)
		return result
	}
	result.ReplyFrameComplete = true
	result.ReadOutcome = "frame_received"
	populateReplyHeader(&result, frame)
	analysis := DecodeLoginReply(frame)
	result.ReplyApplication = &analysis
	result.Interpretation = analysis.Explanation
	clear(frame.Body)
	if analysis.Status != "success" {
		return result
	}
	result.GatewayLoginAccepted = true
	if afterLogin != nil {
		afterLogin(conn)
	}
	return result
}

func validatedPublicEndpoint(address string) (string, string, error) {
	host, portText, err := net.SplitHostPort(address)
	if err != nil {
		return "", "", err
	}
	port, err := strconv.Atoi(portText)
	if err != nil || port < 1 || port > 65535 {
		return "", "", errors.New("endpoint port is outside the valid range")
	}
	ip := net.ParseIP(host)
	if ip == nil || !ip.IsGlobalUnicast() || ip.IsPrivate() || ip.IsLoopback() || ip.IsLinkLocalUnicast() {
		return "", "", errors.New("endpoint is not a public IP")
	}
	return host, portText, nil
}

func writeAll(w io.Writer, data []byte) error {
	for len(data) > 0 {
		n, err := w.Write(data)
		if err != nil {
			return err
		}
		if n == 0 {
			return io.ErrShortWrite
		}
		data = data[n:]
	}
	return nil
}

func populateReplyHeader(result *SequenceResult, frame Frame) {
	result.ReplyCommand = fmt.Sprintf("0x%08x", frame.Command)
	result.ReplyReserved = fmt.Sprintf("0x%08x", frame.Reserved)
	result.ReplyBodyBytes = int(frame.BodySize)
	result.ReplyCheck = fmt.Sprintf("0x%08x", frame.Check)
	result.ReplyChecksumVariant = frame.ChecksumVariant
	result.ReplyNegative = frame.NegativeReply
}

func classifyFrameReadError(err error) string {
	switch {
	case errors.Is(err, ErrGatewayChecksum):
		return "checksum_mismatch"
	case errors.Is(err, io.EOF), errors.Is(err, io.ErrUnexpectedEOF):
		return "peer_closed_or_truncated"
	case isTimeout(err):
		return "read_timeout"
	default:
		return classifyNetworkError(err)
	}
}

func classifyNetworkError(err error) string {
	switch {
	case isTimeout(err):
		return "timeout"
	case errors.Is(err, syscall.ECONNREFUSED):
		return "connection_refused"
	case errors.Is(err, syscall.ECONNRESET):
		return "connection_reset"
	case errors.Is(err, io.EOF):
		return "peer_closed"
	default:
		return "network_error"
	}
}

func isTimeout(err error) bool {
	var networkError net.Error
	return errors.As(err, &networkError) && networkError.Timeout()
}
