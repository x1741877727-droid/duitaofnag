package acecrypto

import (
	"bytes"
	"encoding/hex"
	"testing"
)

// RC6-32/20/16 测试向量。
//
//	vector 1 (all-zero, 出自 RC6 论文 Appendix):
//	  key        = 00000000000000000000000000000000
//	  plaintext  = 00000000000000000000000000000000
//	  ciphertext = 8fc3a53656b1f778c129df4e9848a41e
//
//	vector 2 (非零 key，使用 Python 参考实现独立核对，
//	          基于 Wikipedia 标准 RC6 伪码交叉验证):
//	  key        = 0123456789abcdef0112233445566778  (byte sequence)
//	  plaintext  = 02132435465768798a9bacbdcecfe0f1  (byte sequence, LE → words)
//	  ciphertext = 015bd00e6fc2ff112c9aaa0892f60f43
//
// vector 1 覆盖 algorithm core 和零 key 情形；vector 2 覆盖完整
// key schedule 混合，两条都挂掉的概率几乎为 0。
func TestRC6OfficialVectors(t *testing.T) {
	cases := []struct {
		name      string
		keyHex    string
		plainHex  string
		cipherHex string
	}{
		{
			"all-zero key/plaintext",
			"00000000000000000000000000000000",
			"00000000000000000000000000000000",
			"8fc3a53656b1f778c129df4e9848a41e",
		},
		{
			"mixed key/plaintext",
			"0123456789abcdef0112233445566778",
			"02132435465768798a9bacbdcecfe0f1",
			"015bd00e6fc2ff112c9aaa0892f60f43",
		},
	}

	for _, c := range cases {
		key, _ := hex.DecodeString(c.keyHex)
		plain, _ := hex.DecodeString(c.plainHex)
		want, _ := hex.DecodeString(c.cipherHex)

		cip := NewRC6Cipher(key)
		got := make([]byte, 16)
		cip.EncryptBlock(got, plain)
		if !bytes.Equal(got, want) {
			t.Errorf("[%s] encrypt mismatch\n  got  = %x\n  want = %x", c.name, got, want)
		}

		back := make([]byte, 16)
		cip.DecryptBlock(back, got)
		if !bytes.Equal(back, plain) {
			t.Errorf("[%s] decrypt mismatch\n  got  = %x\n  want = %x", c.name, back, plain)
		}
	}
}

// 随机数据 round-trip：加密 → 解密 → 与原文一致。
func TestRC6ECBRoundTrip(t *testing.T) {
	key := []byte("hello world 1234") // 16B
	plain := []byte("The quick brown fox jumps over lazy dog!!!!!!!!!")
	// 需 16B 对齐
	if len(plain)%16 != 0 {
		pad := 16 - len(plain)%16
		plain = append(plain, bytes.Repeat([]byte{byte(pad)}, pad)...)
	}

	cip := NewRC6Cipher(key)
	enc := make([]byte, len(plain))
	if err := cip.EncryptECB(enc, plain); err != nil {
		t.Fatalf("encrypt ecb: %v", err)
	}
	if bytes.Equal(enc, plain) {
		t.Fatalf("encryption produced identical bytes — bug")
	}
	dec := make([]byte, len(plain))
	if err := cip.DecryptECB(dec, enc); err != nil {
		t.Fatalf("decrypt ecb: %v", err)
	}
	if !bytes.Equal(dec, plain) {
		t.Fatalf("round-trip failed\n  got  = %x\n  want = %x", dec, plain)
	}
}

// RC6 对 >16B / 非对齐 key 也要能 key schedule 通过。
func TestRC6ShortKey(t *testing.T) {
	// 3B seed（模拟 KeyTable[5] = "[TK"）
	cip := NewRC6Cipher([]byte("[TK"))
	plain := bytes.Repeat([]byte{0xAA}, 32) // 2 block
	enc := make([]byte, 32)
	if err := cip.EncryptECB(enc, plain); err != nil {
		t.Fatalf("encrypt: %v", err)
	}
	dec := make([]byte, 32)
	if err := cip.DecryptECB(dec, enc); err != nil {
		t.Fatalf("decrypt: %v", err)
	}
	if !bytes.Equal(dec, plain) {
		t.Fatalf("short-key round-trip failed")
	}
}

// 长度不对齐直接报错。
func TestRC6BadBlockSize(t *testing.T) {
	cip := NewRC6Cipher([]byte("abc"))
	src := make([]byte, 15)
	dst := make([]byte, 15)
	if err := cip.EncryptECB(dst, src); err == nil {
		t.Fatal("expect error on non-16B-aligned input")
	}
}
