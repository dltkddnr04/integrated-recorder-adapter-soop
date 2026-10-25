package gateway

import (
	"crypto/sha256"
	"encoding/hex"
	"testing"
)

func TestBuildService6PayloadShapeAndDeterministicFixture(t *testing.T) {
	const guid = "offline-fixture-guid"
	const timestamp = uint32(0x65a1b2c3)
	const counter = uint32(0x00001234)

	payload, err := BuildService6Payload(guid, timestamp, counter)
	if err != nil {
		t.Fatal(err)
	}
	if len(payload) != Service6PayloadSize {
		t.Fatalf("payload length = %d, want %d", len(payload), Service6PayloadSize)
	}
	if got, want := hex.EncodeToString(payload[:16]), "0a4218677087ca38bbb341823bdcf196"; got != want {
		t.Fatalf("payload RIPEMD-128 prefix = %s, want %s", got, want)
	}

	frame, err := NewNativeService6Frame(guid, timestamp, counter)
	if err != nil {
		t.Fatal(err)
	}
	wire, err := frame.MarshalBinary()
	if err != nil {
		t.Fatal(err)
	}
	if len(wire) != 152 {
		t.Fatalf("wire frame length = %d, want 152", len(wire))
	}
	if got, want := hex.EncodeToString(wire[:16]), "df030000000000008800000057030000"; got != want {
		t.Fatalf("outer header = %s, want %s", got, want)
	}
	if got, want := hex.EncodeToString(wire[16:24]), "0600000080000000"; got != want {
		t.Fatalf("inner service/length = %s, want %s", got, want)
	}
	sum := sha256.Sum256(wire)
	if got, want := hex.EncodeToString(sum[:]), "a3d388000d4b217ab082ead4e2d261a7bfa32bc18aee323bc571eba66828f21a"; got != want {
		t.Fatalf("fixture wire SHA-256 = %s, want %s", got, want)
	}
}

func TestInitialService6CounterMatchesMSVCRandSequence(t *testing.T) {
	if got, want := InitialService6Counter(0x65a1b2c3), uint32(0x5b06); got != want {
		t.Fatalf("initial counter = 0x%x, want fixture value 0x%x", got, want)
	}
}

func TestBuildService6PayloadRejectsInvalidGUID(t *testing.T) {
	for _, guid := range []string{"", "has\x00nul"} {
		if _, err := BuildService6Payload(guid, 1, 1); err == nil {
			t.Fatalf("BuildService6Payload(%q) unexpectedly succeeded", guid)
		}
	}
}
