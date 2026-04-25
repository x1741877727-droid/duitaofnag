package main

import (
	"bytes"
	"encoding/binary"
	"testing"
)

// B1 实验（2026-04-25）：确保 applyRule2Sized 不再对非 G6 大包做改写
// 背景：旧版本在非 G6 magic 时 fallback applyRule2(tail) → 误伤活动 banner
// 修订后：非 G6 直接放行；G6 小帧仍命中 0A 92 → 0A 11

// 构造一个 G6 frame：magic + type + 各种 padding + pay_len (BE) + magic2 + channel + body
func mkG6(bodyLen int, channel byte, body []byte) []byte {
	if len(body) < bodyLen {
		// 如果提供 body 比声明短，尾部补 0
		tmp := make([]byte, bodyLen)
		copy(tmp, body)
		body = tmp
	}
	body = body[:bodyLen]
	frame := bytes.NewBuffer(nil)
	frame.Write([]byte{0x33, 0x66, 0x00, 0x0a, 0x00, 0x0a}) // magic
	frame.Write([]byte{0x40, 0x13})                         // type
	frame.WriteByte(0x01)                                   // direction
	frame.Write([]byte{0x00, 0x00, 0x00})                   // padding
	frame.WriteByte(0x07)                                   // seq 1B
	frame.Write([]byte{0x00, 0x00, 0x00})                   // padding
	frame.WriteByte(0x19)                                   // hdr_len
	frame.Write([]byte{0x00, 0x00})                         // padding
	binary.Write(frame, binary.BigEndian, uint16(bodyLen))  // pay_len BE
	frame.Write([]byte{0x00, 0x10})                         // magic2
	frame.WriteByte(channel)                                // channel
	frame.WriteByte(0x00)                                   // padding
	frame.Write(body)
	return frame.Bytes()
}

// ---- 核心保护：非 G6 大包应完全不动 ----
func TestRule2Sized_NonG6Packet_Untouched(t *testing.T) {
	// 造一个 14 KB 非 G6 大包，内容含若干 0A 92
	data := make([]byte, 14000)
	for i := 0; i < len(data); i++ {
		data[i] = byte(i & 0xff)
	}
	// 故意塞 0A 92
	data[1000] = 0x0a; data[1001] = 0x92
	data[5000] = 0x0a; data[5001] = 0x92
	data[9999] = 0x0a; data[10000] = 0x92

	orig := make([]byte, len(data))
	copy(orig, data)

	out, changed := applyRule2Sized(data, 50)
	if changed {
		t.Fatalf("non-G6 packet should NOT be modified, but changed=true")
	}
	if !bytes.Equal(out, orig) {
		t.Fatalf("non-G6 packet bytes changed")
	}
}

func TestRule2Sized_NonG6PacketStartingWith0A_Untouched(t *testing.T) {
	// 极端情况：非 G6 包恰好以 0a 92 开头
	data := []byte{0x0a, 0x92, 0xff, 0xff, 0x0a, 0x92, 0xff}
	orig := append([]byte(nil), data...)
	out, changed := applyRule2Sized(data, 50)
	if changed {
		t.Fatalf("should not change non-G6 data starting with 0a 92")
	}
	if !bytes.Equal(out, orig) {
		t.Fatalf("bytes changed")
	}
}

// ---- G6 小帧应仍被改写 ----
func TestRule2Sized_G6SmallFrame_PatchedInBody(t *testing.T) {
	body := []byte{0xaa, 0xbb, 0x0a, 0x92, 0xcc, 0xdd, 0x0a, 0x92, 0xee}
	frame := mkG6(len(body), 0x57, body)
	out, changed := applyRule2Sized(frame, 50)
	if !changed {
		t.Fatalf("G6 small frame containing 0a 92 should be modified")
	}
	// 两个 0a 92 都应该被改成 0a 11
	bodyStart := 25
	if out[bodyStart+2] != 0x0a || out[bodyStart+3] != 0x11 {
		t.Fatalf("first 0a 92 not patched: got %02x %02x", out[bodyStart+2], out[bodyStart+3])
	}
	if out[bodyStart+6] != 0x0a || out[bodyStart+7] != 0x11 {
		t.Fatalf("second 0a 92 not patched: got %02x %02x", out[bodyStart+6], out[bodyStart+7])
	}
	// header 区域应完全不动
	for i := 0; i < 25; i++ {
		if out[i] != frame[i] {
			t.Fatalf("header byte[%d] changed: %02x -> %02x", i, frame[i], out[i])
		}
	}
}

// ---- G6 大帧（body >= maxSmallBody）应跳过 ----
func TestRule2Sized_G6LargeFrame_Skipped(t *testing.T) {
	body := make([]byte, 1000) // 远大于 maxSmallBody=50
	body[100] = 0x0a
	body[101] = 0x92
	frame := mkG6(len(body), 0x57, body)
	orig := append([]byte(nil), frame...)

	out, changed := applyRule2Sized(frame, 50)
	if changed {
		t.Fatalf("large G6 frame should be skipped, but was modified")
	}
	if !bytes.Equal(out, orig) {
		t.Fatalf("large G6 frame bytes changed")
	}
}

// ---- 混合：G6 小帧 + 续包非 G6 字节（TCP 边界切包） ----
func TestRule2Sized_G6ThenNonG6Tail_OnlyFirstPatched(t *testing.T) {
	body := []byte{0x00, 0x00, 0x0a, 0x92, 0xff} // 帧内有 0a 92
	small := mkG6(len(body), 0x57, body)
	// 把一个非 G6 tail 拼在后面（跨 TCP 分片语义）
	tail := make([]byte, 500)
	for i := range tail {
		tail[i] = byte(0x80 + (i & 0x7f))
	}
	tail[50] = 0x0a
	tail[51] = 0x92  // 非 G6 段内的 0a 92 —— 修订版应放行
	data := append(small, tail...)
	origTail := append([]byte(nil), tail...)

	out, changed := applyRule2Sized(data, 50)
	if !changed {
		t.Fatalf("G6 small frame 里 0a 92 应该被改")
	}
	// 小帧 body 里的 0a 92 应被改
	bodyStart := 25
	if out[bodyStart+2] != 0x0a || out[bodyStart+3] != 0x11 {
		t.Fatalf("G6 body 0a 92 not patched")
	}
	// tail 应该完全不动
	outTail := out[len(small):]
	if !bytes.Equal(outTail, origTail) {
		t.Fatalf("non-G6 tail was modified! index of first diff:")
	}
}

// ---- maxSmallBody = 0 回退全量模式 ----
func TestRule2Sized_MaxBodyZero_FallbackFullRule2(t *testing.T) {
	// maxSmallBody=0 应该走全量 applyRule2，即使是非 G6 也改
	// 这是老行为保留 —— 提供给旧命令行 -rule2-max-body=0 使用
	data := []byte{0x0a, 0x92, 0xff, 0xff}
	out, changed := applyRule2Sized(data, 0)
	if !changed {
		t.Fatalf("maxSmallBody=0 应该全量改（fallback），但 changed=false")
	}
	if out[1] != 0x11 {
		t.Fatalf("0a 92 未被改成 0a 11")
	}
}
