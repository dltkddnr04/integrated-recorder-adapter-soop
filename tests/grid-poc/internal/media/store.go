// Package media stores received Grid payload bytes without re-serving them.
package media

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
)

const MaxPayloadBytes = 8 << 20

type Saved struct {
	Path   string `json:"path"`
	Bytes  int    `json:"bytes"`
	SHA256 string `json:"sha256"`
}

func SavePayload(dir string, frameNumber uint32, payload []byte) (Saved, error) {
	if len(payload) == 0 || len(payload) > MaxPayloadBytes {
		return Saved{}, fmt.Errorf("payload length %d outside allowed range", len(payload))
	}
	if err := os.MkdirAll(dir, 0700); err != nil {
		return Saved{}, err
	}
	name := fmt.Sprintf("frame-%010d.bin", frameNumber)
	path := filepath.Join(dir, name)
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return Saved{}, err
	}
	_, writeErr := f.Write(payload)
	closeErr := f.Close()
	if writeErr != nil {
		_ = os.Remove(path)
		return Saved{}, writeErr
	}
	if closeErr != nil {
		_ = os.Remove(path)
		return Saved{}, closeErr
	}
	sum := sha256.Sum256(payload)
	return Saved{Path: path, Bytes: len(payload), SHA256: hex.EncodeToString(sum[:])}, nil
}
