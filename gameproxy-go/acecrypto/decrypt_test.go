package acecrypto

import (
	"bytes"
	"errors"
	"testing"
)

// 用 KeyTable[5]="[TK" 做 Mode 2 的 round-trip：构造 header + plaintext →
// Encrypt → Decrypt 回来与原文一致。
func TestMode2RoundTrip(t *testing.T) {
	// 48B = 3 个 16B block
	plain := []byte("hello ACE mode2 rc6 round-trip test message!!!!!")
	for len(plain)%16 != 0 {
		plain = append(plain, '!')
	}

	h := &Header{
		ModeIdx: 2,
		KeyIdx:  5,
	}
	// 填点东西进 SessionKey 和 Raw 里未解析区域，验证透传
	for i := range h.SessionKey {
		h.SessionKey[i] = byte(i + 1)
	}
	h.Raw[2] = 0xAB
	h.Raw[3] = 0xCD
	h.Raw[9] = 0xEF

	packet, err := Encrypt(plain, h)
	if err != nil {
		t.Fatalf("Encrypt: %v", err)
	}
	if len(packet) != HeaderSize+len(plain) {
		t.Fatalf("packet size = %d, want %d", len(packet), HeaderSize+len(plain))
	}

	got, h2, err := Decrypt(packet)
	if err != nil {
		t.Fatalf("Decrypt: %v", err)
	}
	if !bytes.Equal(got, plain) {
		t.Fatalf("plaintext mismatch\n  got  = %x\n  want = %x", got, plain)
	}
	if h2.ModeIdx != 2 || h2.KeyIdx != 5 {
		t.Fatalf("header mismatch: mode=%d key=%d", h2.ModeIdx, h2.KeyIdx)
	}
	if h2.SessionKey != h.SessionKey {
		t.Fatalf("session key not preserved")
	}
	if h2.CRC32Body != CRC32IEEE(plain) {
		t.Fatalf("crc32 not preserved")
	}
	// 检查透传字段
	if h2.Raw[2] != 0xAB || h2.Raw[3] != 0xCD || h2.Raw[9] != 0xEF {
		t.Fatalf("raw header bytes not preserved: %02x %02x %02x",
			h2.Raw[2], h2.Raw[3], h2.Raw[9])
	}
}

// Mode 0/1 的 round-trip 通过顶层 Encrypt/Decrypt API 验证。
func TestMode0And1TopLevelRoundTrip(t *testing.T) {
	plain := bytes.Repeat([]byte{0x5a}, 16)
	for _, mode := range []uint8{0, 1} {
		h := &Header{ModeIdx: mode, KeyIdx: 0}
		packet, err := Encrypt(plain, h)
		if err != nil {
			t.Fatalf("mode %d encrypt err=%v", mode, err)
		}
		out, _, err := Decrypt(packet)
		if err != nil {
			t.Fatalf("mode %d decrypt err=%v", mode, err)
		}
		if !bytes.Equal(out, plain) {
			t.Fatalf("mode %d round-trip fail", mode)
		}
	}
}

// CRC 不对时解密应报错。
func TestBadCRC(t *testing.T) {
	plain := bytes.Repeat([]byte{0x55}, 32)
	h := &Header{ModeIdx: 2, KeyIdx: 5}
	packet, err := Encrypt(plain, h)
	if err != nil {
		t.Fatal(err)
	}
	// 篡改 CRC 字段
	packet[offCRC32] ^= 0xFF
	_, _, err = Decrypt(packet)
	if !errors.Is(err, ErrBadCRC) {
		t.Fatalf("expect ErrBadCRC, got %v", err)
	}
}

// 非法 modeIdx / keyIdx。
func TestInvalidIdx(t *testing.T) {
	buf := make([]byte, HeaderSize+16)
	buf[0] = 3 // mode 3 不合法
	if _, _, err := Decrypt(buf); !errors.Is(err, ErrInvalidMode) {
		t.Errorf("want ErrInvalidMode, got %v", err)
	}
	buf[0] = 2
	buf[1] = 10 // key 10 不合法
	if _, _, err := Decrypt(buf); !errors.Is(err, ErrInvalidKeyIdx) {
		t.Errorf("want ErrInvalidKeyIdx, got %v", err)
	}
}

// packet 太短。
func TestShortPacket(t *testing.T) {
	if _, _, err := Decrypt([]byte{1, 2, 3}); !errors.Is(err, ErrShortPacket) {
		t.Errorf("want ErrShortPacket, got %v", err)
	}
}
