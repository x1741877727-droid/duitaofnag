package acecrypto

import (
	"encoding/binary"
	"errors"
)

// OICQ TEA (Tencent Game Connection Protocol / tdrcrypt)
//
// 逆向来源：libgcloud.so 0x14f63c (encrypt) / 0x14f75c (decrypt) / 0x14fd50 (cbc wrapper)
// 算法：标准 TEA 16 轮，DELTA=0x9e3779b9，大端字节序，8B 块，16B key
// 包装：Tencent 经典 OICQ CBC 变种（不是标准 CBC）
//   第一块密文直接 TEA 解 → 读 pad byte & 7 得 skip
//   后续块：TEA_decrypt(Ci) XOR Ci-1
//
// 报文结构（解密前明文）：
//   [1 byte pad_head: 高 5 bit 随机, 低 3 bit = skip_len ∈ [0,7]]
//   [skip_len bytes of random pad]
//   [2 bytes of zero mid]
//   [plaintext body (real_len = cipher_len - skip - 10)]
//   [7 bytes of zero trailer]
// 总长度 = 10 + skip + real_len，必须 %8 == 0（skip 自动算出以对齐）

var (
	ErrOICQTEABadLen = errors.New("oicq_tea: ciphertext len must be >=16 and %8==0")
	ErrOICQTEAKeyLen = errors.New("oicq_tea: key must be exactly 16 bytes")
)

const oicqTEARounds = 16
const oicqTEADelta uint32 = 0x9e3779b9
const oicqTEASumFinal uint32 = 0xe3779b90 // = DELTA * 16 mod 2^32

// teaBlockDecrypt 解密一个 8B TEA 块
// v0, v1 是密文大端解析出的两个 uint32
// k 是 [4]uint32 的密钥（从 16B key 大端解析）
func teaBlockDecrypt(v0, v1 uint32, k [4]uint32) (uint32, uint32) {
	sum := uint32(oicqTEASumFinal)
	for i := 0; i < oicqTEARounds; i++ {
		v1 -= ((v0 << 4) + k[2]) ^ (v0 + sum) ^ ((v0 >> 5) + k[3])
		v0 -= ((v1 << 4) + k[0]) ^ (v1 + sum) ^ ((v1 >> 5) + k[1])
		sum -= oicqTEADelta
	}
	return v0, v1
}

// teaBlockEncrypt 加密一个 8B TEA 块
func teaBlockEncrypt(v0, v1 uint32, k [4]uint32) (uint32, uint32) {
	sum := uint32(0)
	for i := 0; i < oicqTEARounds; i++ {
		sum += oicqTEADelta
		v0 += ((v1 << 4) + k[0]) ^ (v1 + sum) ^ ((v1 >> 5) + k[1])
		v1 += ((v0 << 4) + k[2]) ^ (v0 + sum) ^ ((v0 >> 5) + k[3])
	}
	return v0, v1
}

// parseKeyBE 从 16B 密钥大端解析 4 个 uint32
func parseKeyBE(key []byte) [4]uint32 {
	var k [4]uint32
	for i := 0; i < 4; i++ {
		k[i] = binary.BigEndian.Uint32(key[i*4 : i*4+4])
	}
	return k
}

// OICQTEADecrypt 解密 OICQ CBC-TEA 密文
//   cipher: 密文字节（长度必须 8B 对齐且 >= 16）
//   key:    16 字节密钥
// 返回明文字节（去除 padding 和 trailer）
func OICQTEADecrypt(cipher, key []byte) ([]byte, error) {
	if len(cipher) < 16 || len(cipher)%8 != 0 {
		return nil, ErrOICQTEABadLen
	}
	if len(key) != 16 {
		return nil, ErrOICQTEAKeyLen
	}
	k := parseKeyBE(key)
	// 第一块：纯 TEA decrypt
	v0 := binary.BigEndian.Uint32(cipher[0:4])
	v1 := binary.BigEndian.Uint32(cipher[4:8])
	p0, p1 := teaBlockDecrypt(v0, v1, k)
	blockPlain := make([]byte, 8)
	binary.BigEndian.PutUint32(blockPlain[0:4], p0)
	binary.BigEndian.PutUint32(blockPlain[4:8], p1)

	// 输出 buffer：最多 len(cipher) - 10 字节（减 pad + trailer）
	out := make([]byte, 0, len(cipher))

	skip := int(blockPlain[0] & 0x07) // 前置随机 padding 长度
	// 第一块除去 pad_byte 的后 7B（可能含 skip padding + 2B zero mid + 部分 plain）
	out = append(out, blockPlain[1:]...)

	// 后续块：TEA_decrypt(Ci) XOR Ci-1
	prevCipher := cipher[0:8]
	for i := 8; i < len(cipher); i += 8 {
		v0 = binary.BigEndian.Uint32(cipher[i : i+4])
		v1 = binary.BigEndian.Uint32(cipher[i+4 : i+8])
		p0, p1 = teaBlockDecrypt(v0, v1, k)
		binary.BigEndian.PutUint32(blockPlain[0:4], p0)
		binary.BigEndian.PutUint32(blockPlain[4:8], p1)
		// XOR 前块密文
		for j := 0; j < 8; j++ {
			blockPlain[j] ^= prevCipher[j]
		}
		out = append(out, blockPlain...)
		prevCipher = cipher[i : i+8]
	}

	// 去头：跳过 skip + 2 字节（skip 随机 + 2B zero mid）
	// 去尾：7 字节 trailer
	overhead := skip + 2 + 7
	if len(out) < overhead {
		return nil, ErrOICQTEABadLen
	}
	return out[skip+2 : len(out)-7], nil
}

// OICQTEAEncrypt 加密明文为 OICQ CBC-TEA 密文
//   plain: 明文字节
//   key:   16 字节密钥
//   padFill: 随机填充函数；nil 时用 zeros
// skip 由 plain_len 自动决定以使总长 %8 == 0
func OICQTEAEncrypt(plain, key []byte, padFill func([]byte)) ([]byte, error) {
	if len(key) != 16 {
		return nil, ErrOICQTEAKeyLen
	}
	// skip 需满足 (10 + skip + plain_len) % 8 == 0 → skip = (-2 - plain_len) & 7
	skip := (-2 - len(plain)) & 7
	totalBody := 10 + skip + len(plain)
	// 布局: [1B pad_head] [skip B pad] [2B zero mid] [plain] [7B zero trailer]
	buf := make([]byte, totalBody)
	buf[0] = byte(skip & 0x07)
	if padFill != nil {
		padFill(buf[0:1])
		buf[0] = (buf[0] & 0xF8) | byte(skip&0x07)
		padFill(buf[1 : 1+skip])
	}
	// buf[1+skip : 1+skip+2] = 2 bytes zero mid (已为 0)
	copy(buf[1+skip+2:1+skip+2+len(plain)], plain)
	// buf[totalBody-7:] = 7 bytes zero trailer (已为 0)

	k := parseKeyBE(key)
	out := make([]byte, totalBody)

	// 第一块：纯 TEA encrypt
	v0 := binary.BigEndian.Uint32(buf[0:4])
	v1 := binary.BigEndian.Uint32(buf[4:8])
	c0, c1 := teaBlockEncrypt(v0, v1, k)
	binary.BigEndian.PutUint32(out[0:4], c0)
	binary.BigEndian.PutUint32(out[4:8], c1)
	prevCipher := out[0:8]

	// 后续块：TEA_encrypt(Pi XOR Ci-1) = Ci
	for i := 8; i < totalBody; i += 8 {
		for j := 0; j < 8; j++ {
			buf[i+j] ^= prevCipher[j]
		}
		v0 = binary.BigEndian.Uint32(buf[i : i+4])
		v1 = binary.BigEndian.Uint32(buf[i+4 : i+8])
		c0, c1 = teaBlockEncrypt(v0, v1, k)
		binary.BigEndian.PutUint32(out[i:i+4], c0)
		binary.BigEndian.PutUint32(out[i+4:i+8], c1)
		prevCipher = out[i : i+8]
	}
	return out, nil
}
