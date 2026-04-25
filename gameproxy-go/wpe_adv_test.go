package main

import (
	"bytes"
	"testing"
)

func TestPatchWPEAdvanced_Rule1(t *testing.T) {
	// 构造 pos 61 处 = 01 0A 00 23, pos 69 = 0xFF 的包（模拟 pos61 命中）
	data := make([]byte, 100)
	copy(data[61:], []byte{0x01, 0x0a, 0x00, 0x23, 0x55, 0x55, 0x55, 0x55, 0x55, 0x55, 0x55, 0xFF})
	out, changed := patchWPEAdvanced(data)
	if !changed {
		t.Fatal("expected change")
	}
	// +3 (pos 64) 应改成 0x37
	if out[64] != 0x37 {
		t.Errorf("pos64 want 0x37, got 0x%02x", out[64])
	}
	// +11 (pos 72) 应改成 0x00
	if out[72] != 0x00 {
		t.Errorf("pos72 want 0x00, got 0x%02x", out[72])
	}
}

func TestPatchWPEAdvanced_Rule2(t *testing.T) {
	// 0a 92 在 pos 30, +1 (pos 31) 改 0x11
	data := make([]byte, 50)
	data[30] = 0x0a
	data[31] = 0x92
	out, changed := patchWPEAdvanced(data)
	if !changed {
		t.Fatal("expected change")
	}
	if out[31] != 0x11 {
		t.Errorf("pos31 want 0x11, got 0x%02x", out[31])
	}
}

func TestPatchWPEAdvanced_NoMatch(t *testing.T) {
	data := []byte{0x00, 0x01, 0x02, 0x03, 0x04, 0x05}
	out, changed := patchWPEAdvanced(data)
	if changed {
		t.Error("should not change")
	}
	if !bytes.Equal(out, data) {
		t.Error("buffer should be unchanged")
	}
}

func TestPatchWPEAdvanced_Multiple(t *testing.T) {
	// 两个 Rule1 命中 + 一个 Rule2 命中
	data := make([]byte, 200)
	copy(data[10:], []byte{0x01, 0x0a, 0x00, 0x23, 0, 0, 0, 0, 0, 0, 0, 0xFF}) // pos 10
	copy(data[80:], []byte{0x01, 0x0a, 0x00, 0x23, 0, 0, 0, 0, 0, 0, 0, 0xFF}) // pos 80
	data[150] = 0x0a
	data[151] = 0x92
	out, changed := patchWPEAdvanced(data)
	if !changed {
		t.Fatal("expected change")
	}
	if out[13] != 0x37 || out[21] != 0x00 {
		t.Errorf("first match wrong: pos13=0x%02x pos21=0x%02x", out[13], out[21])
	}
	if out[83] != 0x37 || out[91] != 0x00 {
		t.Errorf("second match wrong: pos83=0x%02x pos91=0x%02x", out[83], out[91])
	}
	if out[151] != 0x11 {
		t.Errorf("rule2 wrong: pos151=0x%02x", out[151])
	}
}
