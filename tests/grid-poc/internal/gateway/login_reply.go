package gateway

import "encoding/binary"

const (
	loginReplyMinimumBody = 0x6f
	loginReplyUserIDEnd   = 0x1a
	loginReplyKeepAlive   = 0x1a
	loginReplyMode        = 0x1e
	loginReplyExpireTime  = 0x22
	loginReplyIPAddress   = 0x26
	loginReplyAuxiliary   = 0x2a
	loginReplySkinFile    = 0x2e
)

// LoginReplyAnalysis contains only non-sensitive facts. The native handler
// turns the header dword at +4 into a field named RESULT, then reads five
// signed 32-bit body values and two NUL-terminated strings. This decoder never
// retains or returns either string or any body bytes.
type LoginReplyAnalysis struct {
	Status                  string `json:"status"`
	GatewayResultCode       *int32 `json:"gateway_result_code,omitempty"`
	BodyBytes               int    `json:"body_bytes"`
	HandlerMinimumSatisfied bool   `json:"handler_minimum_satisfied"`
	FixedFieldsPresent      bool   `json:"fixed_fields_present"`
	NumericFieldsDecoded    int    `json:"numeric_fields_decoded"`
	UserFieldTerminated     bool   `json:"user_field_terminated"`
	SkinFieldTerminated     bool   `json:"skin_field_terminated"`
	AuxiliaryValuePositive  bool   `json:"auxiliary_value_positive"`
	SensitiveValuesRetained bool   `json:"sensitive_values_retained"`
	Explanation             string `json:"explanation"`
}

// DecodeLoginReply safely validates the application-level shape handled at
// candidate VA 0x1402658b0. Strings are checked only for a terminator within
// their fixed/remaining body bounds; their contents are deliberately ignored.
// The handler labels header offset +4 as RESULT. Zero is reported as success
// and non-zero as failure, while the explanation records that this is based on
// the field label and framing (the native function itself forwards RESULT and
// does not branch on it).
func DecodeLoginReply(frame Frame) LoginReplyAnalysis {
	a := LoginReplyAnalysis{
		Status:                  "unknown_response",
		BodyBytes:               len(frame.Body),
		SensitiveValuesRetained: false,
	}
	if frame.Command != LoginCommand {
		a.Explanation = "command is not the Gateway login response command"
		return a
	}
	if frame.NegativeReply {
		a.Status = "negative_gateway_frame"
		a.Explanation = "frame uses the negative checksum variant; application body was not interpreted"
		return a
	}
	if frame.BodySize != uint32(len(frame.Body)) || len(frame.Body) > MaxFrameBody {
		a.Status = "invalid_declared_length"
		a.Explanation = "declared body size does not match the bounded body buffer"
		return a
	}
	if frame.Check != frame.Command^frame.BodySize {
		a.Status = "invalid_checksum"
		a.Explanation = "frame checksum does not match the ordinary Gateway XOR variant"
		return a
	}
	if len(frame.Body) < loginReplyMinimumBody {
		a.Status = "truncated_or_short_body"
		a.Explanation = "body is shorter than the native handler minimum of 0x6f bytes"
		return a
	}
	a.HandlerMinimumSatisfied = true
	a.FixedFieldsPresent = true

	// The native helper at 0x140266dd0 scans C strings until NUL. Bound those
	// scans to the body to avoid reproducing an out-of-bounds read on malformed
	// network input.
	a.UserFieldTerminated = containsNUL(frame.Body[:loginReplyUserIDEnd])
	a.SkinFieldTerminated = containsNUL(frame.Body[loginReplySkinFile:])
	if !a.UserFieldTerminated || !a.SkinFieldTerminated {
		a.Status = "unterminated_string_field"
		a.Explanation = "a native C-string field has no NUL terminator within its available body range"
		return a
	}

	result := int32(frame.Reserved)
	a.GatewayResultCode = &result
	fieldOffsets := [...]int{loginReplyKeepAlive, loginReplyMode, loginReplyExpireTime, loginReplyIPAddress, loginReplyAuxiliary}
	var numericFields [len(fieldOffsets)]int32
	for i, offset := range fieldOffsets {
		numericFields[i] = int32(binary.LittleEndian.Uint32(frame.Body[offset : offset+4]))
	}
	a.NumericFieldsDecoded = len(numericFields)
	a.AuxiliaryValuePositive = numericFields[len(numericFields)-1] > 0
	if result == 0 {
		a.Status = "success"
		a.Explanation = "RESULT is zero and the login-handler body layout is bounded and structurally valid; native handler forwards RESULT without its own success branch"
	} else {
		a.Status = "failure"
		a.Explanation = "RESULT is non-zero; native handler forwards the code without defining a more specific mapping"
	}
	return a
}

func containsNUL(data []byte) bool {
	for _, value := range data {
		if value == 0 {
			return true
		}
	}
	return false
}
