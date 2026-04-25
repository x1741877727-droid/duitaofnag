package acecrypto

import (
	"bytes"
	"testing"
)

// 自洽测试：encrypt + decrypt 能还原原文
func TestOICQTEARoundTrip(t *testing.T) {
	key := []byte("0123456789ABCDEF")
	plaintexts := [][]byte{
		{0x01, 0x02, 0x03},
		[]byte("hello world"),
		bytes.Repeat([]byte{0xAA}, 64),
		bytes.Repeat([]byte{0x55}, 128),
	}
	for _, plain := range plaintexts {
		cipher, err := OICQTEAEncrypt(plain, key, nil)
		if err != nil {
			t.Fatalf("encrypt err: %v", err)
		}
		if len(cipher)%8 != 0 || len(cipher) < 16 {
			t.Fatalf("cipher len invalid: %d", len(cipher))
		}
		dec, err := OICQTEADecrypt(cipher, key)
		if err != nil {
			t.Fatalf("decrypt err: %v", err)
		}
		if !bytes.Equal(dec, plain) {
			t.Errorf("round-trip mismatch:\n  plain=%x\n  dec  =%x", plain, dec)
		}
	}
}

// 测试各种明文长度（skip 自动计算）
func TestOICQTEAVariousLens(t *testing.T) {
	key := []byte("testkeytestkey16")
	for plainLen := 0; plainLen <= 32; plainLen++ {
		plain := make([]byte, plainLen)
		for i := range plain {
			plain[i] = byte(i + 1)
		}
		cipher, err := OICQTEAEncrypt(plain, key, nil)
		if err != nil {
			t.Fatalf("len=%d encrypt err: %v", plainLen, err)
		}
		if len(cipher)%8 != 0 {
			t.Errorf("len=%d cipher not 8-aligned: %d", plainLen, len(cipher))
		}
		dec, err := OICQTEADecrypt(cipher, key)
		if err != nil {
			t.Errorf("len=%d decrypt err: %v", plainLen, err)
			continue
		}
		if !bytes.Equal(dec, plain) {
			t.Errorf("len=%d mismatch: got %x want %x", plainLen, dec, plain)
		}
	}
}

// 错误输入测试
func TestOICQTEAErrors(t *testing.T) {
	// 密文长度不对齐
	_, err := OICQTEADecrypt(make([]byte, 20), make([]byte, 16))
	if err != ErrOICQTEABadLen {
		t.Errorf("expected ErrOICQTEABadLen, got %v", err)
	}
	// 密钥长度错误
	_, err = OICQTEADecrypt(make([]byte, 16), make([]byte, 10))
	if err != ErrOICQTEAKeyLen {
		t.Errorf("expected ErrOICQTEAKeyLen, got %v", err)
	}
}
