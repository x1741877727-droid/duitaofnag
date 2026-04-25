package acecrypto

import (
	"bytes"
	"testing"
)

func TestMode1RoundTrip(t *testing.T) {
	seeds := [][]byte{
		{0x5b, 0x54, 0x4b},
		{0x43, 0x48, 0x41, 0x47, 0x51, 0x58},
	}
	// 测试 16B/32B/48B 的块 + 尾部 fallback 到 Mode 0
	payloads := [][]byte{
		bytes.Repeat([]byte{0x00}, 16),
		bytes.Repeat([]byte{0xaa}, 32),
		append(bytes.Repeat([]byte{0x11}, 16), []byte("tail")...), // 20B
		[]byte("0123456789abcdef0123456789abcdef"),
	}
	for si, seed := range seeds {
		for pi, pt := range payloads {
			ct, err := encryptMode1(pt, seed)
			if err != nil {
				t.Fatalf("seed=%d pl=%d enc err=%v", si, pi, err)
			}
			if len(ct) != len(pt) {
				t.Fatalf("len mismatch %d vs %d", len(ct), len(pt))
			}
			out, err := decryptMode1(ct, seed)
			if err != nil {
				t.Fatalf("seed=%d pl=%d dec err=%v", si, pi, err)
			}
			if !bytes.Equal(out, pt) {
				t.Fatalf("seed=%d pl=%d round-trip fail\n pt=%x\n ct=%x\nout=%x",
					si, pi, pt, ct, out)
			}
		}
	}
}

func TestMode1KeyScheduleDeterministic(t *testing.T) {
	seed := []byte{0x5b, 0x54, 0x4b}
	a := mode1KeySchedule(seed)
	b := mode1KeySchedule(seed)
	if a != b {
		t.Fatalf("key schedule non-deterministic")
	}
	// 40 个 round key 应当不全相同
	allSame := true
	for i := 1; i < len(a); i++ {
		if a[i] != a[0] {
			allSame = false
			break
		}
	}
	if allSame {
		t.Fatalf("all round keys equal (schedule broken)")
	}
}

func TestMode1Entropy(t *testing.T) {
	// ECB 下相同 plaintext block 会产生相同 ct block，所以用 counter plaintext
	seed := []byte{0x5b, 0x54, 0x4b}
	pt := make([]byte, 1024)
	for i := range pt {
		pt[i] = byte(i)
	}
	ct, _ := encryptMode1(pt, seed)
	h := byteEntropy(ct)
	if h < 5.0 {
		t.Fatalf("mode1 ciphertext entropy too low: %.3f", h)
	}
	t.Logf("mode1 encrypted counter-plaintext entropy=%.3f", h)
}
