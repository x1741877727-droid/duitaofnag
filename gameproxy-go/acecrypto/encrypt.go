package acecrypto

// Encrypt 顶层加密入口。
//
// 输入 plaintext body 和一个已初始化的 header（调用方需自行填好
// ModeIdx / KeyIdx / SessionKey / Raw 里其他字段）。
// 函数会：
//  1. 自动填入 header.BodyLen 和 header.CRC32Body
//  2. 根据 header.ModeIdx 选择对应算法加密 body
//  3. 返回 header.Marshal() ++ encrypted body
//
// Mode 0 / Mode 1 目前占位，返回 ErrMode{0,1}NotImplemented。
func Encrypt(plaintext []byte, h *Header) ([]byte, error) {
	if h == nil {
		return nil, ErrShortPacket
	}
	if h.ModeIdx > 2 {
		return nil, ErrInvalidMode
	}
	if h.KeyIdx >= uint8(len(KeyTable)) {
		return nil, ErrInvalidKeyIdx
	}

	seed, ok := SeedBytes(int(h.KeyIdx))
	if !ok {
		return nil, ErrInvalidKeyIdx
	}

	var encBody []byte
	var err error
	switch h.ModeIdx {
	case 0:
		encBody, err = encryptMode0(plaintext, seed)
	case 1:
		encBody, err = encryptMode1(plaintext, seed)
	case 2:
		if len(plaintext)%rc6BlockSz != 0 {
			return nil, ErrRC6BadBlockSize
		}
		cip := NewRC6Cipher(seed)
		encBody = make([]byte, len(plaintext))
		err = cip.EncryptECB(encBody, plaintext)
	}
	if err != nil {
		return nil, err
	}

	h.BodyLen = uint32(len(encBody))
	h.CRC32Body = CRC32IEEE(plaintext)

	hdr := h.Marshal()
	out := make([]byte, 0, len(hdr)+len(encBody))
	out = append(out, hdr...)
	out = append(out, encBody...)
	return out, nil
}
