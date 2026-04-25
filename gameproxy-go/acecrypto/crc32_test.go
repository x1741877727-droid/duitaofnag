package acecrypto

import "testing"

// 标准 IEEE CRC32（CRC-32/ISO-HDLC）测试向量：
//
//	"123456789" -> 0xCBF43926
func TestCRC32Standard(t *testing.T) {
	got := CRC32IEEE([]byte("123456789"))
	const want uint32 = 0xCBF43926
	if got != want {
		t.Fatalf("CRC32IEEE(\"123456789\") = %08x, want %08x", got, want)
	}
}

func TestCRC32Empty(t *testing.T) {
	if got := CRC32IEEE(nil); got != 0 {
		t.Fatalf("CRC32IEEE(nil) = %08x, want 0", got)
	}
}
