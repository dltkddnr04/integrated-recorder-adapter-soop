package media

import (
	"bytes"
	"testing"
)

func TestTSPacketizationPreservesPayloadAndContinuity(t *testing.T) {
	for _, n := range []int{1, 175, 176, 177, 183, 184, 185, 1000} {
		for _, withPCR := range []bool{false, true} {
			var out bytes.Buffer
			m := NewTSMux(&out)
			data := bytes.Repeat([]byte{0x25}, n)
			var pcr *uint64
			v := uint64(123456789)
			if withPCR {
				pcr = &v
			}
			if e := m.packetize(256, data, true, pcr); e != nil {
				t.Fatal(e)
			}
			b := out.Bytes()
			var got []byte
			for i := 0; i < len(b); i += 188 {
				p := b[i : i+188]
				if p[0] != 0x47 || int(p[3]&15) != i/188%16 {
					t.Fatal("TS continuity")
				}
				off := 4
				if p[3]&0x20 != 0 {
					off += 1 + int(p[4])
				}
				got = append(got, p[off:]...)
			}
			if !bytes.Equal(got, data) {
				t.Fatalf("payload mismatch n=%d PCR=%v", n, withPCR)
			}
		}
	}
}
