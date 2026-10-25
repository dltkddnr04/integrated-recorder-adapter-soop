package media

import (
	"bytes"
	"errors"
	"io"
	"os"
)

type VideoInfo struct {
	Codec          string `json:"codec"`
	Width          uint32 `json:"width"`
	Height         uint32 `json:"height"`
	FPSNumerator   uint32 `json:"fps_numerator"`
	FPSDenominator uint32 `json:"fps_denominator"`
	CropBottom     uint32 `json:"crop_bottom_units"`
}
type bits struct {
	b   []byte
	n   int
	err error
}

func (r *bits) get(n int) uint32 {
	if n < 0 || n > 32 || r.n+n > len(r.b)*8 {
		r.err = io.ErrUnexpectedEOF
		return 0
	}
	var v uint32
	for i := 0; i < n; i++ {
		v = v<<1 | uint32(r.b[r.n/8]>>uint(7-r.n%8)&1)
		r.n++
	}
	return v
}
func (r *bits) ue() uint32 {
	z := 0
	for r.err == nil && r.get(1) == 0 {
		z++
		if z > 30 {
			r.err = errors.New("SPS golomb overflow")
			return 0
		}
	}
	return (1 << uint(z)) - 1 + r.get(z)
}
func (r *bits) se() int32 {
	v := r.ue()
	if v&1 != 0 {
		return int32((v + 1) / 2)
	}
	return -int32(v / 2)
}
func AnalyzeH264(path string) (VideoInfo, error) {
	f, e := os.Open(path)
	if e != nil {
		return VideoInfo{}, e
	}
	defer f.Close()
	b, e := io.ReadAll(io.LimitReader(f, 1<<20))
	if e != nil {
		return VideoInfo{}, e
	}
	for i := 0; i+4 < len(b); i++ {
		n := 0
		if bytes.HasPrefix(b[i:], []byte{0, 0, 0, 1}) {
			n = 4
		} else if bytes.HasPrefix(b[i:], []byte{0, 0, 1}) {
			n = 3
		}
		if n == 0 || i+n >= len(b) || b[i+n]&31 != 7 {
			continue
		}
		start := i + n + 1
		end := start
		for end+3 < len(b) && !bytes.HasPrefix(b[end:], []byte{0, 0, 1}) && !bytes.HasPrefix(b[end:], []byte{0, 0, 0, 1}) {
			end++
		}
		return parseSPS(b[start:end])
	}
	return VideoInfo{}, errors.New("SPS unavailable")
}
func parseSPS(nal []byte) (VideoInfo, error) {
	var rbsp []byte
	zeros := 0
	for _, v := range nal {
		if zeros >= 2 && v == 3 {
			zeros = 0
			continue
		}
		rbsp = append(rbsp, v)
		if v == 0 {
			zeros++
		} else {
			zeros = 0
		}
	}
	r := &bits{b: rbsp}
	profile := r.get(8)
	r.get(16)
	r.ue()
	chroma := uint32(1)
	separate := uint32(0)
	switch profile {
	case 100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135:
		chroma = r.ue()
		if chroma > 3 {
			return VideoInfo{}, errors.New("SPS chroma")
		}
		if chroma == 3 {
			separate = r.get(1)
		}
		r.ue()
		r.ue()
		r.get(1)
		if r.get(1) != 0 {
			count := 8
			if chroma == 3 {
				count = 12
			}
			for i := 0; i < count; i++ {
				if r.get(1) == 0 {
					continue
				}
				size := 16
				if i >= 6 {
					size = 64
				}
				last, next := int32(8), int32(8)
				for j := 0; j < size; j++ {
					if next != 0 {
						next = (last + r.se() + 256) % 256
					}
					if next != 0 {
						last = next
					}
				}
			}
		}
	}
	r.ue()
	poc := r.ue()
	if poc == 0 {
		r.ue()
	} else if poc == 1 {
		r.get(1)
		r.se()
		r.se()
		n := r.ue()
		if n > 256 {
			return VideoInfo{}, errors.New("SPS cycle limit")
		}
		for i := uint32(0); i < n; i++ {
			r.se()
		}
	}
	r.ue()
	r.get(1)
	w, h := r.ue()+1, r.ue()+1
	only := r.get(1)
	if only == 0 {
		r.get(1)
	}
	r.get(1)
	var left, right, top, bottom uint32
	if r.get(1) != 0 {
		left = r.ue()
		right = r.ue()
		top = r.ue()
		bottom = r.ue()
	}
	subW, subH := uint32(1), uint32(1)
	if separate == 0 {
		switch chroma {
		case 1:
			subW, subH = 2, 2
		case 2:
			subW = 2
		}
	}
	width, height := uint64(w)*16, uint64(h)*16*uint64(2-only)
	cx, cy := uint64(subW)*uint64(left+right), uint64(subH)*uint64(2-only)*uint64(top+bottom)
	if width <= cx || height <= cy || width > 16384 || height > 16384 {
		return VideoInfo{}, errors.New("SPS dimensions invalid")
	}
	v := VideoInfo{Codec: "h264", Width: uint32(width - cx), Height: uint32(height - cy), CropBottom: bottom}
	if r.get(1) != 0 {
		if r.get(1) != 0 {
			aspect := r.get(8)
			if aspect == 255 {
				r.get(16)
				r.get(16)
			}
		}
		if r.get(1) != 0 {
			r.get(1)
		}
		if r.get(1) != 0 {
			r.get(3)
			r.get(1)
			if r.get(1) != 0 {
				r.get(24)
			}
		}
		if r.get(1) != 0 {
			r.ue()
			r.ue()
		}
		if r.get(1) != 0 {
			units, scale := r.get(32), r.get(32)
			r.get(1)
			if units > 0 && units < 1<<30 {
				v.FPSNumerator = scale
				v.FPSDenominator = 2 * units
			}
		}
	}
	if r.err != nil {
		return VideoInfo{}, r.err
	}
	return v, nil
}
