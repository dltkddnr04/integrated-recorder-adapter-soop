package gateway

import (
	"bytes"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"testing"
)

func TestLoginFrameMatchesPublished78ByteVector(t *testing.T) {
	guid := "0123456789abcdef0123456789abcdef"
	frame, err := NewLoginFrame(guid)
	if err != nil {
		t.Fatal(err)
	}
	wire, err := frame.MarshalBinary()
	if err != nil {
		t.Fatal(err)
	}
	if len(wire) != 78 {
		t.Fatalf("login frame size = %d, want 78", len(wire))
	}
	if got, want := hex.EncodeToString(wire[:16]), "e2030000000000003e000000dc030000"; got != want {
		t.Fatalf("login header = %s, want %s", got, want)
	}
	if got, want := string(wire[16:20]), string([]byte{0x2a, 0x00, 0x02, 0x00}); got != want {
		t.Fatalf("login marker = % x, want % x", []byte(got), []byte(want))
	}
	if got := string(wire[20:52]); got != guid {
		t.Fatalf("login GUID = %q, want supplied GUID", got)
	}
	if got, want := wire[52:56], []byte{0x15, 0, 0, 0}; !bytes.Equal(got, want) {
		t.Fatalf("login add-info length = % x, want % x", got, want)
	}
	if got, want := wire[56:], []byte{0, 'a', 'd', '_', 'l', 'a', 'n', 'g', 0x11, 'k', 'o', 0x12, 'i', 's', '_', 'a', 'u', 't', 'o', 0x11, '0', 0x12}; !bytes.Equal(got, want) {
		t.Fatalf("login add-info = % x, want % x", got, want)
	}
}

func TestNativeLoginFrameUsesCurrentBinaryOffsets(t *testing.T) {
	guid := "0123456789ABCDEF0123456789ABCDEF"
	// Opaque synthetic bytes validate the recovered buffer offsets only; they
	// are not the current binary's serialized session fields.
	addInfo := []byte{0xa0, 0xa1, 0xa2, 0xa3, 0xa4, 0xa5, 0xa6, 0xa7, 0xa8, 0xa9, 0xaa, 0xab, 0xac, 0xad, 0xae, 0xaf, 0xb0, 0xb1, 0xb2, 0xb3, 0xb4}
	if len(addInfo) != 21 {
		t.Fatalf("fixture add-info length = %d, want 21", len(addInfo))
	}
	frame, err := NewNativeLoginFrame(guid, addInfo)
	if err != nil {
		t.Fatal(err)
	}
	wire, err := frame.MarshalBinary()
	if err != nil {
		t.Fatal(err)
	}
	if len(wire) != 78 {
		t.Fatalf("native login frame size = %d, want 78", len(wire))
	}
	if got, want := hex.EncodeToString(wire[:16]), "e2030000000000003e000000dc030000"; got != want {
		t.Fatalf("native login header = %s, want %s", got, want)
	}
	body := wire[WireHeaderSize:]
	if got, want := body[:4], []byte{0x2a, 0x00, 0x02, 0x00}; !bytes.Equal(got, want) {
		t.Fatalf("native login marker = % x, want % x", got, want)
	}
	if got := string(body[4:36]); got != guid {
		t.Fatalf("native login GUID = %q, want supplied GUID", got)
	}
	if body[36] != 0 {
		t.Fatalf("native login GUID terminator = 0x%02x, want NUL", body[36])
	}
	if got, want := body[37:41], []byte{21, 0, 0, 0}; !bytes.Equal(got, want) {
		t.Fatalf("native add-info length = % x, want % x", got, want)
	}
	if !bytes.Equal(body[41:], addInfo) {
		t.Fatalf("native add-info = % x, want % x", body[41:], addInfo)
	}
}

func TestNativeLoginFieldsEncoder(t *testing.T) {
	fields := NativeLoginFields{
		GUID:    "0123456789ABCDEF0123456789ABCDEF",
		Cookie:  "anon-cookie",
		BJID:    "public-broadcaster",
		AddInfo: []byte("ad_lang\x11ko\x12"),
		JoinLog: []byte("is_auto\x110\x12"),
	}
	got, err := BuildNativeLoginAddInfo(fields)
	if err != nil {
		t.Fatal(err)
	}
	want := []byte("cookie\x11anon-cookie\x12bj\x11public-broadcaster\x12ad_lang\x11ko\x12is_auto\x110\x12")
	if !bytes.Equal(got, want) {
		t.Fatalf("add-info = % x, want % x", got, want)
	}
	frame, err := NewNativeLoginFrameFromFields(fields)
	if err != nil {
		t.Fatal(err)
	}
	if frame.Command != LoginCommand || frame.BodySize != uint32(0x29+len(want)) {
		t.Fatalf("native P1 command/body size = %#x/%d", frame.Command, frame.BodySize)
	}
	if got, expected := frame.Body[0x25:0x29], []byte{byte(len(want)), 0, 0, 0}; !bytes.Equal(got, expected) {
		t.Fatalf("native P1 add-info length = % x, want % x", got, expected)
	}
	wire, err := frame.MarshalBinary()
	if err != nil {
		t.Fatal(err)
	}
	if got, expected := sha256.Sum256(wire), [32]byte{0xdb, 0x5a, 0x43, 0xce, 0x45, 0x26, 0x14, 0x0f, 0xa5, 0xb9, 0x53, 0xf8, 0x7d, 0xf0, 0x34, 0xe2, 0xcd, 0x92, 0x20, 0x98, 0xfc, 0xe3, 0xd0, 0x45, 0xec, 0x1e, 0xa6, 0xb7, 0xa8, 0xf8, 0x94, 0x16}; got != expected {
		t.Fatalf("synthetic native P1 vector SHA-256 = %x, want %x", got, expected)
	}
}

func TestNativeLoginFieldsRejectsDelimiterInjection(t *testing.T) {
	_, err := BuildNativeLoginAddInfo(NativeLoginFields{Cookie: "bad\x12value", BJID: "public-broadcaster"})
	if err == nil {
		t.Fatal("cookie containing a field delimiter was accepted")
	}
}

func TestGatewayPreludeEnvelopeUsesInnerServiceSix(t *testing.T) {
	payload := []byte{0xde, 0xad, 0xbe, 0xef}
	frame, err := NewGatewayPreludeFrame(payload)
	if err != nil {
		t.Fatal(err)
	}
	wire, err := frame.MarshalBinary()
	if err != nil {
		t.Fatal(err)
	}
	if frame.Command != PreludeCommand || frame.BodySize != 12 {
		t.Fatalf("prelude frame command/body = 0x%x/%d", frame.Command, frame.BodySize)
	}
	if got, want := wire[WireHeaderSize:], []byte{6, 0, 0, 0, 4, 0, 0, 0, 0xde, 0xad, 0xbe, 0xef}; !bytes.Equal(got, want) {
		t.Fatalf("prelude body = % x, want % x", got, want)
	}
}

func TestCertTicketFrameUsesSyntheticFields(t *testing.T) {
	ticket := []byte("fixture-fan-ticket")
	frame, err := NewCertTicketFrame(CertTicketFields{
		SessionID:  12345,
		TicketType: 1,
		BJID:       "fixture_channel",
		Category:   "123",
		FanTicket:  ticket,
	})
	if err != nil {
		t.Fatal(err)
	}
	wire, err := frame.MarshalBinary()
	if err != nil {
		t.Fatal(err)
	}
	if len(wire) != WireHeaderSize+0x47+len(ticket) {
		t.Fatalf("cert frame size = %d", len(wire))
	}
	if got := binary.LittleEndian.Uint32(frame.Body[0:4]); got != 12345 {
		t.Fatalf("session field = %d", got)
	}
	if got := binary.LittleEndian.Uint32(frame.Body[4:8]); got != 1 {
		t.Fatalf("ticket type = %d", got)
	}
	if got := string(bytes.TrimRight(frame.Body[0x21:0x3a], "\x00")); got != "fixture_channel" {
		t.Fatalf("broadcaster field = %q", got)
	}
	if got := binary.LittleEndian.Uint32(frame.Body[0x3f:0x43]); got != 123 {
		t.Fatalf("category field = %d", got)
	}
	if got := binary.LittleEndian.Uint32(frame.Body[0x43:0x47]); got != uint32(len(ticket)) {
		t.Fatalf("ticket length = %d", got)
	}
	if !bytes.Equal(frame.Body[0x47:], ticket) {
		t.Fatal("synthetic ticket payload offset differs")
	}
}

func TestReadFrameChecksChecksumAndBodyLimit(t *testing.T) {
	f, err := NewFrame(LoginCommand, []byte("ok"))
	if err != nil {
		t.Fatal(err)
	}
	wire, err := f.MarshalBinary()
	if err != nil {
		t.Fatal(err)
	}
	got, n, err := ReadFrame(bytes.NewReader(wire), 8)
	if err != nil || n != len(wire) || got.Command != LoginCommand || string(got.Body) != "ok" {
		t.Fatalf("ReadFrame() = (%+v, %d, %v)", got, n, err)
	}
	badChecksum := append([]byte(nil), wire...)
	badChecksum[12] ^= 1
	partial, _, err := ReadFrame(bytes.NewReader(badChecksum), 8)
	if !errors.Is(err, ErrGatewayChecksum) || partial.Command != LoginCommand || string(partial.Body) != "ok" {
		t.Fatalf("checksum mismatch result = (%+v, %v)", partial, err)
	}
	if _, _, err := ReadFrame(bytes.NewReader(wire), 1); err == nil {
		t.Fatal("expected body limit error")
	}
}

func TestReadFrameRecognizesObservedNegativeReplyHeader(t *testing.T) {
	// The 16-byte header is from the live probe; the body bytes here are only
	// padding for parser coverage and are not represented as captured evidence.
	header, err := hex.DecodeString("e2030000ffffffff000100001dfdffff")
	if err != nil {
		t.Fatal(err)
	}
	wire := append(header, make([]byte, 256)...)
	f, n, err := ReadFrame(bytes.NewReader(wire), 1024)
	if err != nil || n != len(wire) {
		t.Fatalf("ReadFrame() = (%+v, %d, %v)", f, n, err)
	}
	if !f.NegativeReply || f.ChecksumVariant != "ones_complement_xor" || f.Command != LoginCommand || f.BodySize != 256 {
		t.Fatalf("negative reply classification = %+v", f)
	}
}

func TestNewCertTicketFrameRejectsUnusableFields(t *testing.T) {
	base := CertTicketFields{SessionID: 1, TicketType: 1, BJID: "test", Category: "12", FanTicket: []byte("ticket")}
	cases := []struct {
		name string
		edit func(*CertTicketFields)
	}{
		{"bad_category", func(f *CertTicketFields) { f.Category = "12x" }},
		{"empty_bjid", func(f *CertTicketFields) { f.BJID = "" }},
		{"nul_bjid", func(f *CertTicketFields) { f.BJID = "test\x00id" }},
		{"oversized_ticket", func(f *CertTicketFields) { f.FanTicket = make([]byte, MaxFanTicket+1) }},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			input := base
			tc.edit(&input)
			if _, err := NewCertTicketFrame(input); err == nil {
				t.Fatal("expected invalid fields to be rejected")
			}
		})
	}
}

func TestNewCertTicketFrameAllowsNativeEmptyTicketAndTruncatesBJID(t *testing.T) {
	frame, err := NewCertTicketFrame(CertTicketFields{
		SessionID:  42,
		TicketType: 1,
		BJID:       stringsRepeat("b", 30),
		Category:   "12",
		FanTicket:  nil,
	})
	if err != nil {
		t.Fatal(err)
	}
	wire, err := frame.MarshalBinary()
	if err != nil {
		t.Fatal(err)
	}
	if len(wire) != WireHeaderSize+0x47 {
		t.Fatalf("empty-ticket frame size = %d, want %d", len(wire), WireHeaderSize+0x47)
	}
	if got := wire[WireHeaderSize+0x21 : WireHeaderSize+0x3a]; string(got) != stringsRepeat("b", 25) {
		t.Fatalf("native 25-byte BJID copy = %q", got)
	}
	if got := binary.LittleEndian.Uint32(wire[WireHeaderSize+0x43 : WireHeaderSize+0x47]); got != 0 {
		t.Fatalf("empty ticket length = %d, want 0", got)
	}
}

func stringsRepeat(value string, count int) string {
	var b bytes.Buffer
	for i := 0; i < count; i++ {
		b.WriteString(value)
	}
	return b.String()
}
