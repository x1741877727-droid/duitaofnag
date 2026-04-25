package acecrypto

import "fmt"

// Decrypt 顶层解密入口。
//
// 输入 packet = header (HeaderSize) + encrypted body。
// 返回解密后的 plaintext body 以及已解析的 header。
//
// - Mode 0 / Mode 1：占位，返回 ErrMode0NotImplemented / ErrMode1NotImplemented。
// - Mode 2：RC6-ECB，key = KeyTable[keyIdx] 原串。
//
// 成功解密后会用 CRC32(plaintext) 对照 header.CRC32Body，
// 不一致则返回 ErrBadCRC（plaintext 仍会返回，供调试）。
func Decrypt(packet []byte) ([]byte, *Header, error) {
	h, err := ParseHeader(packet)
	if err != nil {
		return nil, h, err
	}
	if len(packet) < HeaderSize+int(h.BodyLen) {
		return nil, h, fmt.Errorf("acecrypto: body length %d exceeds packet (%d bytes after header)",
			h.BodyLen, len(packet)-HeaderSize)
	}
	encBody := packet[HeaderSize : HeaderSize+int(h.BodyLen)]

	seed, ok := SeedBytes(int(h.KeyIdx))
	if !ok {
		return nil, h, ErrInvalidKeyIdx
	}

	var plaintext []byte
	switch h.ModeIdx {
	case 0:
		plaintext, err = decryptMode0(encBody, seed)
	case 1:
		plaintext, err = decryptMode1(encBody, seed)
	case 2:
		if len(encBody)%rc6BlockSz != 0 {
			return nil, h, ErrRC6BadBlockSize
		}
		cip := NewRC6Cipher(seed)
		plaintext = make([]byte, len(encBody))
		err = cip.DecryptECB(plaintext, encBody)
	default:
		return nil, h, ErrInvalidMode
	}
	if err != nil {
		return plaintext, h, err
	}

	if got := CRC32IEEE(plaintext); got != h.CRC32Body {
		return plaintext, h, fmt.Errorf("%w: header=%08x got=%08x", ErrBadCRC, h.CRC32Body, got)
	}
	return plaintext, h, nil
}
