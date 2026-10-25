package iss

import (
	"encoding/hex"
	"testing"
)

// Published RIPEMD-128 vectors establish the compression and short-input
// finalization independently of the SOOP packet builders.
func TestWireHashPublishedVectors(t *testing.T) {
	for _, v := range []struct{ in, want string }{
		{"", "cdf26213a150dc3ecb610f18f6b38b46"},
		{"a", "86be7afa339d0fc7cfc785e72f578d33"},
		{"abc", "c14a12199c66e4ba84636b0f69144c77"},
		{"message digest", "9e327b3d6e523062afc1132d7df9d1b8"},
	} {
		got := wireHash([]byte(v.in))
		if hex.EncodeToString(got[:]) != v.want {
			t.Fatalf("RIPEMD128 vector failed for %q: %x", v.in, got)
		}
	}
}
