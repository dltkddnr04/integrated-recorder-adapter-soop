package gateway

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"testing"
)

func syntheticLoginReply(t *testing.T, result int32) Frame {
	t.Helper()
	body := make([]byte, loginReplyMinimumBody)
	copy(body, "private-user-id")
	body[len("private-user-id")] = 0
	frame, err := NewFrame(LoginCommand, body)
	clear(body)
	if err != nil {
		t.Fatal(err)
	}
	frame.Reserved = uint32(result)
	return frame
}

func TestDecodeLoginReplySuccessSynthetic(t *testing.T) {
	got := DecodeLoginReply(syntheticLoginReply(t, 0))
	if got.Status != "success" || got.GatewayResultCode == nil || *got.GatewayResultCode != 0 {
		t.Fatalf("unexpected decoded result: %+v", got)
	}
	if !got.HandlerMinimumSatisfied || !got.FixedFieldsPresent || got.NumericFieldsDecoded != 5 || !got.UserFieldTerminated || !got.SkinFieldTerminated {
		t.Fatalf("reply body layout was not fully checked: %+v", got)
	}
	if got.SensitiveValuesRetained {
		t.Fatal("decoder retained sensitive values")
	}
	encoded, err := json.Marshal(got)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(encoded, []byte("private-user-id")) {
		t.Fatal("sanitized decoder output contains the synthetic user identifier")
	}
}

func TestDecodeLoginReplyNonzeroResultSynthetic(t *testing.T) {
	got := DecodeLoginReply(syntheticLoginReply(t, -7))
	if got.Status != "failure" || got.GatewayResultCode == nil || *got.GatewayResultCode != -7 {
		t.Fatalf("unexpected decoded error result: %+v", got)
	}
}

func TestDecodeLoginReplyShortBodySynthetic(t *testing.T) {
	frame, err := NewFrame(LoginCommand, make([]byte, loginReplyMinimumBody-1))
	if err != nil {
		t.Fatal(err)
	}
	got := DecodeLoginReply(frame)
	if got.Status != "truncated_or_short_body" || got.HandlerMinimumSatisfied {
		t.Fatalf("unexpected short-body result: %+v", got)
	}
}

func TestDecodeLoginReplyRejectsDeclaredLengthMismatchSynthetic(t *testing.T) {
	frame := syntheticLoginReply(t, 0)
	frame.BodySize--
	got := DecodeLoginReply(frame)
	if got.Status != "invalid_declared_length" {
		t.Fatalf("unexpected bad-length result: %+v", got)
	}
}

func TestDecodeLoginReplyRejectsOversizedDeclaredLengthSynthetic(t *testing.T) {
	frame := syntheticLoginReply(t, 0)
	frame.BodySize = uint32(MaxFrameBody + 1)
	got := DecodeLoginReply(frame)
	if got.Status != "invalid_declared_length" {
		t.Fatalf("unexpected oversized-length result: %+v", got)
	}
}

func TestDecodeLoginReplyRejectsBadChecksumSynthetic(t *testing.T) {
	frame := syntheticLoginReply(t, 0)
	frame.Check ^= 1
	got := DecodeLoginReply(frame)
	if got.Status != "invalid_checksum" {
		t.Fatalf("unexpected checksum result: %+v", got)
	}
}

func TestReadFrameRejectsTruncatedLoginReplySynthetic(t *testing.T) {
	frame := syntheticLoginReply(t, 0)
	wire, err := frame.MarshalBinary()
	if err != nil {
		t.Fatal(err)
	}
	wire = wire[:len(wire)-1]
	_, n, err := ReadFrame(bytes.NewReader(wire), maxGatewayReplyBody)
	if !errors.Is(err, io.ErrUnexpectedEOF) {
		t.Fatalf("ReadFrame error = %v, want unexpected EOF", err)
	}
	if n != len(wire) {
		t.Fatalf("ReadFrame consumed %d bytes, want %d", n, len(wire))
	}
}

func TestDecodeLoginReplyUnknownCommandSynthetic(t *testing.T) {
	frame := syntheticLoginReply(t, 0)
	frame.Command = 0xdead
	got := DecodeLoginReply(frame)
	if got.Status != "unknown_response" || got.GatewayResultCode != nil {
		t.Fatalf("unexpected unknown-command result: %+v", got)
	}
}

func TestDecodeLoginReplyNegativeFrameSynthetic(t *testing.T) {
	frame := syntheticLoginReply(t, 0)
	frame.NegativeReply = true
	got := DecodeLoginReply(frame)
	if got.Status != "negative_gateway_frame" || got.GatewayResultCode != nil {
		t.Fatalf("unexpected negative-frame result: %+v", got)
	}
}

func TestDecodeLoginReplyRejectsUnterminatedStringsSynthetic(t *testing.T) {
	t.Run("user", func(t *testing.T) {
		frame := syntheticLoginReply(t, 0)
		for i := 0; i < loginReplyUserIDEnd; i++ {
			frame.Body[i] = 'u'
		}
		got := DecodeLoginReply(frame)
		if got.Status != "unterminated_string_field" || got.UserFieldTerminated {
			t.Fatalf("unexpected user-field result: %+v", got)
		}
	})
	t.Run("skin", func(t *testing.T) {
		frame := syntheticLoginReply(t, 0)
		for i := loginReplySkinFile; i < len(frame.Body); i++ {
			frame.Body[i] = 's'
		}
		got := DecodeLoginReply(frame)
		if got.Status != "unterminated_string_field" || !got.UserFieldTerminated || got.SkinFieldTerminated {
			t.Fatalf("unexpected skin-field result: %+v", got)
		}
	})
}
