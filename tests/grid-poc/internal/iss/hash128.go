package iss

import "github.com/integrated-recorder/source.soop/tests/grid-poc/internal/wirehash"

func wireHash(data []byte) [16]byte { return wirehash.Sum(data) }
