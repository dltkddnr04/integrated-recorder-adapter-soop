package iss

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"
)

func TestNativePasswordJoinAndLifecycle(t *testing.T) {
	c, err := ParseGatewayCredentials(syntheticCertificate(), "0123456789ABCDEF0123456789ABCDEF", "", 1)
	if err != nil {
		t.Fatal(err)
	}
	baseline, _ := joinBody(c)
	if !bytes.Contains(baseline, []byte("passwd\x11\x12")) {
		t.Fatal("empty password changed")
	}
	secret := "fixture-only"
	if err = c.SetStreamPassword(secret); err != nil {
		t.Fatal(err)
	}
	b, err := joinBody(c)
	if err != nil || !bytes.Contains(b, []byte("passwd\x11"+secret+"\x12")) {
		t.Fatal("password field missing")
	}
	encoded, _ := json.Marshal(c)
	if strings.Contains(string(encoded), secret) {
		t.Fatal("secret serialized")
	}
	for _, bad := range []string{"too-long-fixture", "a\x11b", "a\x12b", "a\x00b", "a\nb", "한글"} {
		if c.SetStreamPassword(bad) == nil {
			t.Fatal("unsafe input accepted")
		}
	}
	retained := c.streamPassword
	c.Close()
	for _, b := range retained {
		if b != 0 {
			t.Fatal("secret not scrubbed")
		}
	}
	if c.SetStreamPassword(secret) == nil {
		t.Fatal("closed credentials accepted")
	}
}
