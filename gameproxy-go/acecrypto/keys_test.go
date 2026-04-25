package acecrypto

import (
	"bytes"
	"testing"
)

func TestKeyTableSize(t *testing.T) {
	if len(KeyTable) != 10 {
		t.Fatalf("KeyTable len = %d, want 10", len(KeyTable))
	}
	for i, k := range KeyTable {
		if len(k) == 0 {
			t.Errorf("KeyTable[%d] is empty — seed 不应为空", i)
		}
	}
}

func TestSeedBytesBounds(t *testing.T) {
	if _, ok := SeedBytes(-1); ok {
		t.Error("SeedBytes(-1) should fail")
	}
	if _, ok := SeedBytes(10); ok {
		t.Error("SeedBytes(10) should fail")
	}
	for i := 0; i < 10; i++ {
		b, ok := SeedBytes(i)
		if !ok {
			t.Errorf("SeedBytes(%d) failed", i)
		}
		if len(b) == 0 {
			t.Errorf("SeedBytes(%d) returned empty", i)
		}
	}
}

// KeyTable[5] 反编译/Ghidra 里观察到的是 "[TK" = {0x5b,0x54,0x4b}，校验别被改动。
func TestKeyTableIndex5(t *testing.T) {
	want := []byte{0x5b, 0x54, 0x4b}
	if !bytes.Equal(KeyTable[5], want) {
		t.Fatalf("KeyTable[5] = %x, want %x", KeyTable[5], want)
	}
}

// XOR 0x17 解混淆后的每条 seed 应该和原 seed 不同。
func TestKeyTableDeobfDiffers(t *testing.T) {
	deobf := KeyTableDeobf()
	for i := range KeyTable {
		if bytes.Equal(deobf[i], KeyTable[i]) {
			t.Errorf("KeyTableDeobf[%d] 与原 seed 相同，XOR 0x17 不应该不变", i)
		}
	}
}

// Mode0PadA/B 是 32B init constant，检查非全零。
func TestMode0PadsNonZero(t *testing.T) {
	allZero := true
	for _, b := range Mode0PadA {
		if b != 0 {
			allZero = false
			break
		}
	}
	// Mode0PadA 首字节是 0x00 是对的，但全零就不对了
	var zeroCnt int
	for _, b := range Mode0PadA {
		if b == 0 {
			zeroCnt++
		}
	}
	if allZero {
		t.Error("Mode0PadA is all zero")
	}
	if Mode0PadA[15] != 0x0f {
		t.Errorf("Mode0PadA[15] = %02x, want 0x0f", Mode0PadA[15])
	}
	if Mode0PadB[0] != 0x21 {
		t.Errorf("Mode0PadB[0] = %02x, want 0x21", Mode0PadB[0])
	}
}

// Mode1Sbox 大小 2048，首尾字节已知。
func TestMode1SboxSize(t *testing.T) {
	if len(Mode1Sbox) != 2048 {
		t.Fatalf("Mode1Sbox len = %d, want 2048", len(Mode1Sbox))
	}
	if Mode1Sbox[0] != 0x79 {
		t.Errorf("Mode1Sbox[0] = %02x, want 0x79", Mode1Sbox[0])
	}
	if Mode1Sbox[2047] != 0x19 {
		t.Errorf("Mode1Sbox[2047] = %02x, want 0x19", Mode1Sbox[2047])
	}
}
