package gateway

import (
	"crypto/aes"
	"crypto/cipher"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"strings"

	"github.com/integrated-recorder/source.soop/tests/grid-poc/internal/wirehash"
)

const (
	Service6PayloadSize = 128
	maxService6GUIDSize = 256
)

var (
	service6AESKey = [16]byte{0x45, 0xcb, 0x10, 0x1d, 0x26, 0x3d, 0x47, 0x51, 0x5b, 0x64, 0x75, 0x7b, 0x85, 0x8f, 0x99, 0x9f}
	service6AESIV  = [16]byte{0xb6, 0xc3, 0xca, 0xdd, 0xe7, 0xf1, 0xfb, 0x04, 0x0e, 0x18, 0x22, 0x28, 0x32, 0x3c, 0x46, 0x4c}
	service6Prefix = [12]byte{0x75, 0x6a, 0x5e, 0x74, 0x76, 0x66, 0x66, 0x67, 0x61, 0x34, 0x33, 0x37}
	service6Salt   = [18]byte{0x75, 0x6f, 0x6e, 0x6b, 0x74, 0x73, 0x71, 0x72, 0x60, 0x68, 0x72, 0x71, 0x67, 0x65, 0x69, 0x2f, 0x2e, 0x34}
)

// BuildService6Payload ports the byte construction in buildAuthPacket at
// candidate VA 0x140264c90. guid is the exact byte string supplied in the
// Gateway setup JSON; timestamp and counter are explicit so this function is
// deterministic and can be compared without reading process state.
//
// The transform is AES-128-CTR with a fixed key/initial counter, zero padding
// to 16-byte blocks, and a 16-byte RIPEMD-128 prefix. The candidate binary identity
// is not authenticated and this function is a static reconstruction, not a
// current-server acceptance claim.
func BuildService6Payload(guid string, timestamp, counter uint32) ([]byte, error) {
	if len(guid) == 0 || len(guid) > maxService6GUIDSize {
		return nil, fmt.Errorf("Gateway GUID length must be 1..%d bytes", maxService6GUIDSize)
	}
	if strings.IndexByte(guid, 0) >= 0 {
		return nil, errors.New("Gateway GUID must not contain NUL")
	}

	guidDigest := wirehash.Sum([]byte(guid))
	guidHex := strings.ToUpper(hex.EncodeToString(guidDigest[:]))
	// The native builder writes LE32(1), LE64(42), the 32-character uppercase
	// RIPEMD-128 hex string, and its terminating NUL. Its length field includes NUL.
	authData := make([]byte, 4+8+len(guidHex)+1)
	binary.LittleEndian.PutUint32(authData[0:4], 1)
	binary.LittleEndian.PutUint64(authData[4:12], 42)
	copy(authData[12:], guidHex)

	dataLen := uint32(len(authData))
	mixed := make([]byte, 16+len(service6Prefix)+len(authData))
	checksum := dataLen | 0x06000000
	for i, b := range authData {
		shifted := uint32(b) << (uint(i&3) * 8)
		if i&1 == 1 {
			checksum ^= shifted
		} else {
			checksum |= shifted
		}
	}

	// The 16-byte native header interleaves byte lanes from timestamp,
	// checksum/mixer, counter and auth-data length.
	mixed[0] = byte(timestamp)
	mixed[1] = byte(checksum >> 24)
	mixed[2] = byte(counter >> 16)
	mixed[3] = byte(dataLen >> 8)
	mixed[4] = byte(timestamp >> 16)
	mixed[5] = byte(checksum >> 8)
	mixed[6] = byte(counter)
	mixed[7] = byte(dataLen)
	mixed[8] = byte(timestamp >> 24)
	mixed[9] = byte(checksum >> 16)
	mixed[10] = byte(counter >> 8)
	mixed[11] = byte(dataLen >> 24)
	mixed[12] = byte(timestamp >> 8)
	mixed[13] = byte(checksum)
	mixed[14] = byte(counter >> 24)
	mixed[15] = byte(dataLen >> 16)
	copy(mixed[16:28], service6Prefix[:])
	copy(mixed[28:], authData)

	firstCiphertext, err := aesCTRZeroPadded(mixed)
	clear(mixed)
	clear(authData)
	if err != nil {
		return nil, err
	}
	stageTwo := make([]byte, len(service6Salt)+len(firstCiphertext))
	copy(stageTwo, service6Salt[:])
	copy(stageTwo[len(service6Salt):], firstCiphertext)
	clear(firstCiphertext)

	stageTwoPadded := zeroPad16(stageTwo)
	digest := wirehash.Sum(stageTwoPadded)
	clear(stageTwoPadded)
	secondCiphertext, err := aesCTRZeroPadded(stageTwo)
	clear(stageTwo)
	if err != nil {
		return nil, err
	}
	payload := make([]byte, 0, len(digest)+len(secondCiphertext))
	payload = append(payload, digest[:]...)
	payload = append(payload, secondCiphertext...)
	clear(secondCiphertext)
	if len(payload) != Service6PayloadSize {
		clear(payload)
		return nil, fmt.Errorf("reconstructed Service 6 payload size %d, want %d", len(payload), Service6PayloadSize)
	}
	return payload, nil
}

// InitialService6Counter reproduces the candidate's first-call MSVC rand()
// sequence when its process-global counter starts at zero. Subsequent native
// calls use previous+1; the caller should persist that state explicitly.
func InitialService6Counter(unixSeconds uint32) uint32 {
	state := unixSeconds*214013 + 2531011
	return (state >> 16) & 0x7fff
}

// NewNativeService6Frame wraps the reconstructed 128-byte payload in inner
// SVC=6 and the current candidate's 0x03df outer command.
func NewNativeService6Frame(guid string, timestamp, counter uint32) (Frame, error) {
	payload, err := BuildService6Payload(guid, timestamp, counter)
	if err != nil {
		return Frame{}, err
	}
	defer clear(payload)
	return NewGatewayPreludeFrame(payload)
}

func aesCTRZeroPadded(input []byte) ([]byte, error) {
	if len(input) == 0 || len(input) > MaxFrameBody {
		return nil, errors.New("AES-CTR input length outside supported bounds")
	}
	padded := zeroPad16(input)
	block, err := aes.NewCipher(service6AESKey[:])
	if err != nil {
		clear(padded)
		return nil, err
	}
	output := make([]byte, len(padded))
	stream := cipher.NewCTR(block, service6AESIV[:])
	stream.XORKeyStream(output, padded)
	clear(padded)
	return output, nil
}

func zeroPad16(input []byte) []byte {
	out := make([]byte, (len(input)+aes.BlockSize-1)&^(aes.BlockSize-1))
	copy(out, input)
	return out
}
