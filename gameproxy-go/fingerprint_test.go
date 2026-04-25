package main

import (
	"bytes"
	"encoding/binary"
	"testing"
)

// build a single G6 frame with given payload bytes (after offset 25)
// frame[0:6] = magic, frame[19:21] BE = payload_len, frame[25:] = payload
func buildG6Frame(payload []byte) []byte {
	frame := make([]byte, 25+len(payload))
	copy(frame[0:6], []byte{0x33, 0x66, 0x00, 0x0a, 0x00, 0x0a})
	binary.BigEndian.PutUint16(frame[19:21], uint16(len(payload)))
	copy(frame[25:], payload)
	return frame
}

func TestFingerprint_Match(t *testing.T) {
	// payload 长度需 >= 31，使 frame total >= 56
	// payload[23:31] = 3c d0 ec aa 88 be 0a 92 → frame[48:56] 匹配
	payload := make([]byte, 100)
	copy(payload[23:31], []byte{0x3c, 0xd0, 0xec, 0xaa, 0x88, 0xbe, 0x0a, 0x92})
	frame := buildG6Frame(payload)

	out, changed := applyRule2Fingerprint(frame)
	if !changed {
		t.Fatal("expected change")
	}
	if out[55] != 0x11 {
		t.Errorf("byte 55 = 0x%02x, want 0x11", out[55])
	}
	// other bytes unchanged
	for i := 0; i < len(frame); i++ {
		if i == 55 {
			continue
		}
		if out[i] != frame[i] {
			t.Errorf("byte %d changed unexpectedly", i)
		}
	}
}

func TestFingerprint_NoMatch_WrongPrefix(t *testing.T) {
	payload := make([]byte, 100)
	// 不同 prefix，但有 0A 92
	copy(payload[23:31], []byte{0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0x0a, 0x92})
	frame := buildG6Frame(payload)

	_, changed := applyRule2Fingerprint(frame)
	if changed {
		t.Error("should NOT match - prefix bytes wrong")
	}
}

func TestFingerprint_NoMatch_WrongOffset(t *testing.T) {
	payload := make([]byte, 200)
	// fingerprint 在错误位置
	copy(payload[100:108], []byte{0x3c, 0xd0, 0xec, 0xaa, 0x88, 0xbe, 0x0a, 0x92})
	frame := buildG6Frame(payload)

	_, changed := applyRule2Fingerprint(frame)
	if changed {
		t.Error("should NOT match - fingerprint at wrong offset")
	}
}

func TestFingerprint_OnlyOA92_NoFingerprint(t *testing.T) {
	// 包里有 0A 92 但不是 fingerprint
	payload := make([]byte, 200)
	copy(payload[50:52], []byte{0x0a, 0x92})
	copy(payload[80:82], []byte{0x0a, 0x92})
	frame := buildG6Frame(payload)

	_, changed := applyRule2Fingerprint(frame)
	if changed {
		t.Error("should NOT match - 0A 92 not at offset 54")
	}
}

func TestFingerprint_MultipleFrames(t *testing.T) {
	// 两个 frame 拼接，第二个含 fingerprint
	payload1 := make([]byte, 100)
	frame1 := buildG6Frame(payload1) // 125B 不含

	payload2 := make([]byte, 100)
	copy(payload2[23:31], []byte{0x3c, 0xd0, 0xec, 0xaa, 0x88, 0xbe, 0x0a, 0x92})
	frame2 := buildG6Frame(payload2)

	combined := append(append([]byte{}, frame1...), frame2...)

	out, changed := applyRule2Fingerprint(combined)
	if !changed {
		t.Fatal("should match second frame")
	}
	// frame2 起始 = len(frame1) = 125, byte 55 of frame2 = 125+55 = 180
	pos := len(frame1) + 55
	if out[pos] != 0x11 {
		t.Errorf("frame2 byte 55 (overall %d) = 0x%02x, want 0x11", pos, out[pos])
	}
}

func TestFingerprint_RealWorldShape(t *testing.T) {
	// 模拟真实 ACE 包：size 在 345-1138 范围
	for _, sz := range []int{345, 505, 537, 1138} {
		payloadLen := sz - 25
		payload := make([]byte, payloadLen)
		// random-ish bytes
		for i := range payload {
			payload[i] = byte(i ^ 0x55)
		}
		// fingerprint 在 payload offset 23..30
		copy(payload[23:31], []byte{0x3c, 0xd0, 0xec, 0xaa, 0x88, 0xbe, 0x0a, 0x92})
		frame := buildG6Frame(payload)

		out, changed := applyRule2Fingerprint(frame)
		if !changed {
			t.Errorf("size=%d: expected change", sz)
			continue
		}
		if out[55] != 0x11 {
			t.Errorf("size=%d: byte 55 = 0x%02x, want 0x11", sz, out[55])
		}
		// confirm frame is otherwise unchanged
		expected := append([]byte{}, frame...)
		expected[55] = 0x11
		if !bytes.Equal(out, expected) {
			t.Errorf("size=%d: only byte 55 should change", sz)
		}
	}
}

func TestFingerprint_TooShort(t *testing.T) {
	// 帧太短不会越界
	short := []byte{0x33, 0x66, 0x00, 0x0a, 0x00, 0x0a}
	_, changed := applyRule2Fingerprint(short)
	if changed {
		t.Error("too-short frame should not change")
	}
}
