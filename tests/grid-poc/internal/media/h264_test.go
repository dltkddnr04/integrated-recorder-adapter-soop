package media

import "testing"

// makeBaselineSPS creates a small synthetic baseline-profile SPS bitstream.
// It contains codec metadata only and no encoded video frames.
func makeBaselineSPS(widthInMBs, heightInMapUnits, cropBottom uint32) []byte {
	w := &testBitWriter{}
	w.bits(66, 8) // profile_idc: baseline
	w.bits(0, 8)  // constraint flags
	w.bits(30, 8) // level_idc
	w.ue(0)       // seq_parameter_set_id
	w.ue(0)       // log2_max_frame_num_minus4
	w.ue(0)       // pic_order_cnt_type
	w.ue(0)       // log2_max_pic_order_cnt_lsb_minus4
	w.ue(1)       // max_num_ref_frames
	w.bits(0, 1)  // gaps_in_frame_num_value_allowed_flag
	w.ue(widthInMBs - 1)
	w.ue(heightInMapUnits - 1)
	w.bits(1, 1) // frame_mbs_only_flag
	w.bits(1, 1) // direct_8x8_inference_flag
	if cropBottom == 0 {
		w.bits(0, 1)
	} else {
		w.bits(1, 1)
		w.ue(0)
		w.ue(0)
		w.ue(0)
		w.ue(cropBottom)
	}
	w.bits(0, 1) // vui_parameters_present_flag
	w.bits(1, 1) // rbsp_stop_one_bit
	for w.n%8 != 0 {
		w.bits(0, 1)
	}
	return w.b
}

type testBitWriter struct {
	b []byte
	n int
}

func (w *testBitWriter) bits(value uint32, count int) {
	for shift := count - 1; shift >= 0; shift-- {
		if w.n%8 == 0 {
			w.b = append(w.b, 0)
		}
		if value&(1<<uint(shift)) != 0 {
			w.b[w.n/8] |= 1 << uint(7-w.n%8)
		}
		w.n++
	}
}

func (w *testBitWriter) ue(value uint32) {
	code := value + 1
	width := 0
	for n := code; n > 0; n >>= 1 {
		width++
	}
	w.bits(0, width-1)
	w.bits(code, width)
}

func TestParseSyntheticSPSDimensionsAndCropping(t *testing.T) {
	for _, tc := range []struct {
		name                  string
		widthMBs, heightMUs   uint32
		cropBottom            uint32
		wantWidth, wantHeight uint32
	}{
		{name: "16x16", widthMBs: 1, heightMUs: 1, wantWidth: 16, wantHeight: 16},
		{name: "cropped 32x30", widthMBs: 2, heightMUs: 2, cropBottom: 1, wantWidth: 32, wantHeight: 30},
	} {
		t.Run(tc.name, func(t *testing.T) {
			info, err := parseSPS(makeBaselineSPS(tc.widthMBs, tc.heightMUs, tc.cropBottom))
			if err != nil || info.Width != tc.wantWidth || info.Height != tc.wantHeight {
				t.Fatalf("SPS result %+v, error %v", info, err)
			}
		})
	}
}

func TestParseSyntheticSPSRejectsTruncation(t *testing.T) {
	sps := makeBaselineSPS(2, 2, 1)
	// The parser stops after the fields needed for dimensions; later VUI and
	// trailing bits may be absent without affecting this specific analysis.
	for n := 0; n < 6; n++ {
		if _, err := parseSPS(sps[:n]); err == nil {
			t.Fatalf("accepted truncated SPS of %d bytes", n)
		}
	}
}
