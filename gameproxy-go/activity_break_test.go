package main

import (
	"encoding/binary"
	"testing"
)

func buildG6FrameWithOpcode(opcode []byte, payloadLen int) []byte {
	// Build a G6 frame: 25B header + payloadLen B payload
	frame := make([]byte, 25+payloadLen)
	copy(frame[0:6], []byte{0x33, 0x66, 0x00, 0x0a, 0x00, 0x0a})
	// type (offset 6-7)
	frame[6] = 0x40
	frame[7] = 0x13
	// opcode (offset 8-11)
	copy(frame[8:12], opcode)
	// seq (offset 12-15)
	frame[12] = 0x01
	frame[13] = 0x00
	frame[14] = 0x00
	frame[15] = 0x00
	// pay_len (offset 19-20 BE)
	binary.BigEndian.PutUint16(frame[19:21], uint16(payloadLen))
	return frame
}

func TestActivityBreak_Opcode04(t *testing.T) {
	frame := buildG6FrameWithOpcode([]byte{0x01, 0x00, 0x00, 0x04}, 100)
	out, changed := applyActivityBreak(frame)
	if !changed {
		t.Fatal("expected change for opcode 04")
	}
	if out[11] != 0x00 {
		t.Errorf("byte 11 = 0x%02x, want 0x00", out[11])
	}
	// opcode 0/1/2/3 unchanged
	for i, b := range []byte{0x01, 0x00, 0x00} {
		if out[8+i] != b {
			t.Errorf("byte %d = 0x%02x, want 0x%02x", 8+i, out[8+i], b)
		}
	}
}

func TestActivityBreak_Opcode05(t *testing.T) {
	frame := buildG6FrameWithOpcode([]byte{0x01, 0x00, 0x00, 0x05}, 100)
	out, changed := applyActivityBreak(frame)
	if !changed {
		t.Fatal("expected change for opcode 05")
	}
	if out[11] != 0x00 {
		t.Errorf("byte 11 = 0x%02x, want 0x00", out[11])
	}
}

func TestActivityBreak_NoChange_Opcode01(t *testing.T) {
	// shop / lobby_idle 用 opcode 末字节 01
	frame := buildG6FrameWithOpcode([]byte{0x01, 0x00, 0x00, 0x01}, 100)
	_, changed := applyActivityBreak(frame)
	if changed {
		t.Error("opcode 01 should NOT be broken")
	}
}

func TestActivityBreak_NoChange_RuleByte01(t *testing.T) {
	// 第 1 字节非 01（c2s 用 0x00 起始）
	frame := buildG6FrameWithOpcode([]byte{0x00, 0x00, 0x00, 0x04}, 100)
	_, changed := applyActivityBreak(frame)
	if changed {
		t.Error("byte 8 != 0x01 should NOT match")
	}
}

func TestActivityBreak_MultiFrames(t *testing.T) {
	// 3 frames: 01-shop, 04-activity, 01-shop
	f1 := buildG6FrameWithOpcode([]byte{0x01, 0x00, 0x00, 0x01}, 50)
	f2 := buildG6FrameWithOpcode([]byte{0x01, 0x00, 0x00, 0x04}, 50)
	f3 := buildG6FrameWithOpcode([]byte{0x01, 0x00, 0x00, 0x01}, 50)
	combined := append(append(append([]byte{}, f1...), f2...), f3...)

	out, changed := applyActivityBreak(combined)
	if !changed {
		t.Fatal("expected change for f2")
	}
	// f1 unchanged
	if out[11] != 0x01 {
		t.Errorf("f1 byte 11 should be 0x01")
	}
	// f2 broken (offset = len(f1) + 11)
	pos := len(f1) + 11
	if out[pos] != 0x00 {
		t.Errorf("f2 byte 11 (overall %d) = 0x%02x, want 0x00", pos, out[pos])
	}
	// f3 unchanged
	pos3 := len(f1) + len(f2) + 11
	if out[pos3] != 0x01 {
		t.Errorf("f3 byte 11 (overall %d) = 0x%02x, want 0x01", pos3, out[pos3])
	}
}

func TestActivityBreak_NoMagic(t *testing.T) {
	bad := make([]byte, 100)
	bad[0] = 0xff
	_, changed := applyActivityBreak(bad)
	if changed {
		t.Error("non-G6 magic should not match")
	}
}

func TestActivityBreak_TooShort(t *testing.T) {
	short := []byte{0x33, 0x66, 0x00, 0x0a, 0x00, 0x0a, 0x40, 0x13, 0x01, 0x00}
	_, changed := applyActivityBreak(short)
	if changed {
		t.Error("too short")
	}
}
