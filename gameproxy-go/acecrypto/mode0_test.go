package acecrypto

import (
	"bytes"
	"math"
	"testing"
)

func TestMode0RoundTrip(t *testing.T) {
	seeds := [][]byte{
		{0x5b, 0x54, 0x4b},                                     // KeyTable[5]
		{0x43, 0x48, 0x41, 0x47, 0x51, 0x58},                   // KeyTable[0]
		{0x57, 0x49, 0x58, 0x5f, 0x38, 0x33, 0x71, 0x70, 0x74}, // KeyTable[9]
	}
	payloads := [][]byte{
		[]byte("hello world"),
		bytes.Repeat([]byte{0}, 32),
		bytes.Repeat([]byte{0xff}, 64),
		[]byte("\x01\x00\x00\x00\x04\x00\x00\x00" + "ACE_PING"),
	}
	for si, seed := range seeds {
		for pi, pt := range payloads {
			ct, err := encryptMode0(pt, seed)
			if err != nil {
				t.Fatalf("seed=%d pl=%d encrypt err=%v", si, pi, err)
			}
			if len(ct) != len(pt) {
				t.Fatalf("seed=%d pl=%d ct len %d != pt %d", si, pi, len(ct), len(pt))
			}
			out, err := decryptMode0(ct, seed)
			if err != nil {
				t.Fatalf("seed=%d pl=%d decrypt err=%v", si, pi, err)
			}
			if !bytes.Equal(out, pt) {
				t.Fatalf("seed=%d pl=%d round-trip mismatch\n pt=%x\n ct=%x\nout=%x",
					si, pi, pt, ct, out)
			}
		}
	}
}

func TestMode0SeedImmutable(t *testing.T) {
	seed := []byte{0x5b, 0x54, 0x4b}
	orig := append([]byte(nil), seed...)
	_, _ = encryptMode0(bytes.Repeat([]byte{0x55}, 24), seed)
	if !bytes.Equal(seed, orig) {
		t.Fatalf("seed mutated: %x vs %x", seed, orig)
	}
}

func TestMode0Entropy(t *testing.T) {
	// 对 0 填充 1024B 加密后，熵应该接近 8 bit（盒子良好混合）。
	seed := []byte{0x5b, 0x54, 0x4b}
	pt := bytes.Repeat([]byte{0}, 1024)
	ct, _ := encryptMode0(pt, seed)
	h := byteEntropy(ct)
	if h < 5.0 {
		t.Fatalf("ciphertext entropy too low: %.3f (want >5)", h)
	}
	t.Logf("mode0 encrypted zero-plaintext entropy=%.3f", h)
}

func byteEntropy(b []byte) float64 {
	if len(b) == 0 {
		return 0
	}
	var counts [256]int
	for _, x := range b {
		counts[x]++
	}
	var h float64
	n := float64(len(b))
	for _, c := range counts {
		if c == 0 {
			continue
		}
		p := float64(c) / n
		h -= p * math.Log2(p)
	}
	return h
}
