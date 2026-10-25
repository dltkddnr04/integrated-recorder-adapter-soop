package gateway

import (
	"context"
	"encoding/binary"
	"net"
	"time"
)

type CertificateResult struct {
	RequestBytes       int    `json:"request_bytes"`
	ReplyBytes         int    `json:"reply_bytes"`
	ReplyCommand       uint32 `json:"reply_command"`
	ReplyResult        int32  `json:"reply_result"`
	Outcome            string `json:"outcome"`
	BodyBytes          int    `json:"body_bytes"`
	DeclaredFieldBytes uint32 `json:"declared_field_bytes"`
	TicketType         uint32 `json:"ticket_type"`
}

// LoginAndRequestCertificate continues a normally initialized Gateway session.
// The 0x0415 client-to-Gateway direction is established by the same +0x50
// GatewaySocket and vtable +8 calls in candidate VAs 0x1402658b0/0x14026c690.
// The response body is passed to consume in memory and cleared afterward.
func LoginAndRequestCertificate(ctx context.Context, address string, login NativeLoginFields, ts, counter uint32, fields CertTicketFields, consume func([]byte)) (SequenceResult, CertificateResult) {
	cr := CertificateResult{Outcome: "not_attempted"}
	lr := loginPreludeThenCertificate(ctx, address, login, ts, counter, func(c net.Conn) {
		f, err := NewCertTicketFrame(fields)
		if err != nil {
			cr.Outcome = "certificate_build_failed"
			return
		}
		defer clear(f.Body)
		wire, err := f.MarshalBinary()
		if err != nil {
			cr.Outcome = "certificate_encode_failed"
			return
		}
		defer clear(wire)
		_ = c.SetWriteDeadline(time.Now().Add(2 * time.Second))
		if writeAll(c, wire) != nil {
			cr.Outcome = "certificate_write_failed"
			return
		}
		cr.RequestBytes = len(wire)
		_ = c.SetReadDeadline(time.Now().Add(4 * time.Second))
		r, n, err := ReadFrame(c, maxGatewayReplyBody)
		defer clear(r.Body)
		cr.ReplyBytes = n
		cr.ReplyCommand = r.Command
		cr.ReplyResult = int32(r.Reserved)
		cr.BodyBytes = len(r.Body)
		if err != nil {
			cr.Outcome = "certificate_read_or_frame_error"
			return
		}
		if r.Command != CertCommand || r.NegativeReply || int32(r.Reserved) < 0 {
			cr.Outcome = "certificate_rejected_or_unexpected"
			return
		}
		if len(r.Body) < 16 {
			cr.Outcome = "certificate_body_short"
			return
		}
		cr.TicketType = binary.LittleEndian.Uint32(r.Body[4:8])
		cr.DeclaredFieldBytes = binary.LittleEndian.Uint32(r.Body[12:16])
		cr.Outcome = "certificate_reply_received"
		if consume != nil {
			consume(r.Body)
		}
	})
	return lr, cr
}
