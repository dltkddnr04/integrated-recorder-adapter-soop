package iss

import (
	"bytes"
	"crypto/subtle"
	"encoding/base64"
	"encoding/binary"
)

// GatewayCertificateShape contains only field boundaries and recognizer facts.
// The body and all possible credential bytes remain in the caller's memory.
type GatewayCertificateShape struct {
	BodyBytes            int    `json:"body_bytes"`
	DeclaredBytes        uint32 `json:"declared_bytes"`
	TextBytes            int    `json:"text_bytes_at_16"`
	TextTerminated       bool   `json:"text_terminated"`
	TextBase64           bool   `json:"text_is_base64"`
	DecodedBytes         int    `json:"text_decoded_bytes"`
	DecodedTailSeedValid bool   `json:"decoded_tail_seed_valid"`
	ServerSeedOffsets    []int  `json:"server_seed_offsets,omitempty"`
}

func InspectGatewayCertificate(body []byte) GatewayCertificateShape {
	r := GatewayCertificateShape{BodyBytes: len(body)}
	if len(body) < 16 || len(body) > 8192 {
		return r
	}
	r.DeclaredBytes = binary.LittleEndian.Uint32(body[12:16])
	tail := body[16:]
	n := bytes.IndexByte(tail, 0)
	if n >= 0 {
		r.TextBytes = n
		r.TextTerminated = true
		decoded, err := base64.StdEncoding.DecodeString(string(tail[:n]))
		if err == nil {
			r.TextBase64 = true
			r.DecodedBytes = len(decoded)
			if len(decoded) >= 28 {
				r.DecodedTailSeedValid = validSeed(decoded[len(decoded)-28 : len(decoded)-8])
			}
		}
		clear(decoded)
	}
	for i := 16; i+20 <= len(body); i++ {
		if validSeed(body[i : i+20]) {
			r.ServerSeedOffsets = append(r.ServerSeedOffsets, i)
		}
	}
	return r
}

// validSeed checks a normally issued 20-byte session token using the same
// reversible salt adjustment and four-byte digest check as sendCcpSvcAuth.
// It does not generate or replace server credentials.
func validSeed(seed []byte) bool {
	if len(seed) != 20 {
		return false
	}
	var plain [20]byte
	adjust := [6]byte{1, 255, 253, 254, 1, 2}
	for i, b := range seed {
		plain[i] = b - adjust[i%6]
	}
	digest := wireHash(plain[:4])
	valid := subtle.ConstantTimeCompare(digest[:], plain[4:]) == 1
	clear(plain[:])
	clear(digest[:])
	return valid
}
