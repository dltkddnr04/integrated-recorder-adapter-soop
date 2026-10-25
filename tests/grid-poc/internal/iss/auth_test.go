package iss

import (
	"bytes"
	"encoding/binary"
	"testing"
)

// This synthetic seed is used ONLY for offline boundary/crypto tests.
func syntheticCertificate() []byte {
	b := make([]byte, 16+8+28)
	binary.LittleEndian.PutUint32(b[12:], 36)
	copy(b[16:], "offline")
	var seed [20]byte
	binary.LittleEndian.PutUint32(seed[:4], 1700000000)
	h := wireHash(seed[:4])
	copy(seed[4:], h[:])
	salt := []byte{1, 255, 253, 254, 1, 2}
	for i := range seed {
		seed[i] += salt[i%6]
	}
	copy(b[24:], seed[:])
	return b
}
func TestIssuedCertificateBoundsAndLifecycle(t *testing.T) {
	b := syntheticCertificate()
	c, e := ParseGatewayCredentials(b, "0123456789ABCDEF0123456789ABCDEF", "log\x11offline\x12", 1)
	if e != nil {
		t.Fatal(e)
	}
	a, e := authBody(c, 1)
	if e != nil || len(a) != 136 {
		t.Fatalf("auth length=%d err=%v", len(a), e)
	}
	a2, _ := authBody(c, 1)
	if !bytes.Equal(a, a2) {
		t.Fatal("auth not deterministic")
	}
	j, e := joinBody(c)
	if e != nil || int(binary.LittleEndian.Uint32(j[16:])) != len(j)-21 {
		t.Fatal("join bounds")
	}
	c.Close()
	if _, e := authBody(c, 1); e == nil {
		t.Fatal("closed credentials reused")
	}
	for _, n := range []int{0, 15, 43, len(b) - 1} {
		if _, e := ParseGatewayCredentials(b[:n], "offline", "", 1); e == nil {
			t.Fatalf("truncated %d accepted", n)
		}
	}
	b[12]++
	if _, e := ParseGatewayCredentials(b, "offline", "", 1); e == nil {
		t.Fatal("bad declared length accepted")
	}
}
func TestParentJSONNULTerminationAndBounds(t *testing.T) {
	b := jsonBody([]byte("{\"list\":[{\"quality\":1}]}\x00"))
	p, _, e := parseParents(b)
	if e != nil || len(p) != 1 {
		t.Fatal("NUL-inclusive server JSON rejected")
	}
	binary.LittleEndian.PutUint32(b, 999999)
	if _, _, e := parseParents(b); e == nil {
		t.Fatal("length overrun accepted")
	}
}

func TestCaptureWireLimit(t *testing.T) {
	for _, tc := range []struct {
		configured int64
		want       int64
	}{
		{0, 64 << 20},
		{128 << 20, 128 << 20},
		{512 << 20, 512 << 20},
		{513 << 20, 64 << 20},
		{-1, 64 << 20},
	} {
		if got := captureWireLimit(tc.configured); got != tc.want {
			t.Fatalf("captureWireLimit(%d)=%d, want %d", tc.configured, got, tc.want)
		}
	}
}
