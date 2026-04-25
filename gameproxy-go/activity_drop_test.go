package main

import (
	"bytes"
	"encoding/binary"
	"testing"
)

func buildFrameWithChannel(channelByte byte, payloadLen int) []byte {
	// channel bytes at [21:25] = 00 10 <channelByte> 00
	frame := make([]byte, 25+payloadLen)
	copy(frame[0:6], []byte{0x33, 0x66, 0x00, 0x0a, 0x00, 0x0a})
	frame[6] = 0x40; frame[7] = 0x13 // type
	// opcode
	frame[8] = 0x01; frame[9] = 0x00; frame[10] = 0x00; frame[11] = 0x01
	// pay_len BE at [19:21]
	binary.BigEndian.PutUint16(frame[19:21], uint16(payloadLen))
	// channel bytes[21:25]
	frame[21] = 0x00
	frame[22] = 0x10
	frame[23] = channelByte
	frame[24] = 0x00
	// fill payload with distinct byte for identification
	for i := 25; i < len(frame); i++ {
		frame[i] = byte(channelByte ^ 0xAA)
	}
	return frame
}

func TestActivityDrop_Channel51(t *testing.T) {
	frame := buildFrameWithChannel(0x51, 100)
	out, changed := applyActivityDropFrames(frame)
	if !changed { t.Fatal("expected drop for channel 0x51") }
	if len(out) != 0 { t.Errorf("expected empty output, got %d bytes", len(out)) }
}

func TestActivityDrop_Channel65(t *testing.T) {
	frame := buildFrameWithChannel(0x65, 100)
	out, changed := applyActivityDropFrames(frame)
	if !changed { t.Fatal("expected drop for channel 0x65") }
	if len(out) != 0 { t.Errorf("expected empty output, got %d bytes", len(out)) }
}

func TestActivityDrop_Channel53NotDropped(t *testing.T) {
	// 0x53 is battle channel — must NOT drop
	frame := buildFrameWithChannel(0x53, 100)
	out, changed := applyActivityDropFrames(frame)
	if changed { t.Error("channel 0x53 (battle) should NOT be dropped") }
	if !bytes.Equal(out, frame) { t.Error("output should equal input") }
}

func TestActivityDrop_Channel6dNotDropped(t *testing.T) {
	// 0x6d = lobby, must NOT drop
	frame := buildFrameWithChannel(0x6d, 100)
	_, changed := applyActivityDropFrames(frame)
	if changed { t.Error("channel 0x6d (lobby) should NOT drop") }
}

func TestActivityDrop_Channel47NotDropped(t *testing.T) {
	// 0x47 = shop, must NOT drop
	frame := buildFrameWithChannel(0x47, 100)
	_, changed := applyActivityDropFrames(frame)
	if changed { t.Error("channel 0x47 (shop) should NOT drop") }
}

func TestActivityDrop_MixedStream(t *testing.T) {
	// battle (keep) + activity 0x51 (drop) + battle (keep)
	f1 := buildFrameWithChannel(0x53, 50)
	f2 := buildFrameWithChannel(0x51, 50)
	f3 := buildFrameWithChannel(0x53, 50)
	stream := append(append(append([]byte{}, f1...), f2...), f3...)

	out, changed := applyActivityDropFrames(stream)
	if !changed { t.Fatal("should have dropped f2") }
	// Expected: f1 + f3 (f2 removed)
	expected := append([]byte{}, f1...)
	expected = append(expected, f3...)
	if !bytes.Equal(out, expected) {
		t.Errorf("output mismatch\n  got  %d bytes\n  want %d bytes", len(out), len(expected))
	}
}

func TestActivityDrop_AllActivityDropped(t *testing.T) {
	f1 := buildFrameWithChannel(0x51, 30)
	f2 := buildFrameWithChannel(0x65, 40)
	stream := append(append([]byte{}, f1...), f2...)
	out, changed := applyActivityDropFrames(stream)
	if !changed { t.Fatal("should have dropped both") }
	if len(out) != 0 { t.Errorf("expected empty, got %d bytes", len(out)) }
}

func TestActivityDrop_NoG6Magic(t *testing.T) {
	bad := make([]byte, 100)
	bad[0] = 0xff
	out, changed := applyActivityDropFrames(bad)
	if changed { t.Error("non-G6 should not change") }
	if !bytes.Equal(out, bad) { t.Error("output should equal input") }
}

func TestActivityDrop_IncompleteFrame(t *testing.T) {
	// payload_len says 1000 but data is only 50
	frame := make([]byte, 50)
	copy(frame[0:6], []byte{0x33, 0x66, 0x00, 0x0a, 0x00, 0x0a})
	binary.BigEndian.PutUint16(frame[19:21], 1000)
	frame[21] = 0x00; frame[22] = 0x10; frame[23] = 0x51; frame[24] = 0x00
	out, changed := applyActivityDropFrames(frame)
	if changed { t.Error("incomplete frame should not change") }
	if !bytes.Equal(out, frame) { t.Error("incomplete frame should pass through") }
}
