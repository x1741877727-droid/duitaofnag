package acecrypto

import (
	"encoding/binary"
	"errors"
	"math/bits"
)

// RC6-32/20/b 实现，用于 ACE 上报包 Mode 2。
//
// 参数固定为：
//   w = 32 (word size)
//   r = 20 (rounds)
//   b = 可变 (key byte length)，ACE 里 seed 一般为 3~8 字节
//
// 标准 magic constants:
//   P32 = 0xB7E15163
//   Q32 = 0x9E3779B9
//
// 标准 RC6 对 16B block (4 个 uint32 A,B,C,D) 进行加密 / 解密，
// 扩展出 2r+4 = 44 个 uint32 的轮密钥 S[0..43]。

const (
	rc6P32     uint32 = 0xB7E15163
	rc6Q32     uint32 = 0x9E3779B9
	rc6Rounds         = 20
	rc6SLen           = 2*rc6Rounds + 4 // 44
	rc6BlockSz        = 16
)

// ErrRC6BadBlockSize 明文/密文长度不是 16 的倍数。
var ErrRC6BadBlockSize = errors.New("acecrypto/rc6: input length must be multiple of 16 bytes")

// RC6Cipher 持有一次 keySetup 后的 44 个 uint32 轮密钥。
type RC6Cipher struct {
	S [rc6SLen]uint32
}

// NewRC6Cipher 按 RC6 key schedule 从 key 字节展开出 S 表。
//
// RC6 标准 key schedule：
//
//	L = key 按 little-endian 切成 c = ceil(b/u) 个 uint32, u=4
//	S[0] = P32; S[i] = S[i-1] + Q32, i=1..2r+3
//	A = B = i = j = 0
//	v = 3 * max(c, 2r+4)
//	for cnt in 0..v-1:
//	    A = S[i] = rotl(S[i] + A + B, 3); i = (i+1) mod (2r+4)
//	    B = L[j] = rotl(L[j] + A + B, (A+B) & 31); j = (j+1) mod c
//
// 注：ACE 反编译里循环跑了 132 = 3*44 次，这符合 b <= 8B (c <= 2)
// 情况下 max(c, 44) = 44 → 3*44 = 132，证明它用的是标准 RC6 而不是变体。
func NewRC6Cipher(key []byte) *RC6Cipher {
	c := (len(key) + 3) / 4
	if c < 1 {
		c = 1 // 保证 L 至少 1 个 word
	}
	L := make([]uint32, c)
	// little-endian 装载，不足 4B 的部分为 0
	tmp := make([]byte, c*4)
	copy(tmp, key)
	for i := 0; i < c; i++ {
		L[i] = binary.LittleEndian.Uint32(tmp[i*4:])
	}

	cip := &RC6Cipher{}
	cip.S[0] = rc6P32
	for i := 1; i < rc6SLen; i++ {
		cip.S[i] = cip.S[i-1] + rc6Q32
	}

	v := 3 * rc6SLen
	if c > rc6SLen {
		v = 3 * c
	}

	var A, B uint32
	var i, j int
	for cnt := 0; cnt < v; cnt++ {
		A = bits.RotateLeft32(cip.S[i]+A+B, 3)
		cip.S[i] = A
		i = (i + 1) % rc6SLen

		B = bits.RotateLeft32(L[j]+A+B, int((A+B)&31))
		L[j] = B
		j = (j + 1) % c
	}
	return cip
}

// EncryptBlock 对 16B 明文就地加密。
// 标准 RC6 加密：
//
//	B += S[0]; D += S[1]
//	for i=1..r:
//	    t = rotl(B*(2B+1), 5)
//	    u = rotl(D*(2D+1), 5)
//	    A = rotl(A^t, u&31) + S[2i]
//	    C = rotl(C^u, t&31) + S[2i+1]
//	    (A,B,C,D) = (B,C,D,A)
//	A += S[2r+2]; C += S[2r+3]
func (c *RC6Cipher) EncryptBlock(dst, src []byte) {
	_ = src[15]
	_ = dst[15]
	A := binary.LittleEndian.Uint32(src[0:4])
	B := binary.LittleEndian.Uint32(src[4:8])
	C := binary.LittleEndian.Uint32(src[8:12])
	D := binary.LittleEndian.Uint32(src[12:16])

	B += c.S[0]
	D += c.S[1]

	for i := 1; i <= rc6Rounds; i++ {
		t := bits.RotateLeft32(B*(2*B+1), 5)
		u := bits.RotateLeft32(D*(2*D+1), 5)
		A = bits.RotateLeft32(A^t, int(u&31)) + c.S[2*i]
		C = bits.RotateLeft32(C^u, int(t&31)) + c.S[2*i+1]
		A, B, C, D = B, C, D, A
	}
	A += c.S[2*rc6Rounds+2]
	C += c.S[2*rc6Rounds+3]

	binary.LittleEndian.PutUint32(dst[0:4], A)
	binary.LittleEndian.PutUint32(dst[4:8], B)
	binary.LittleEndian.PutUint32(dst[8:12], C)
	binary.LittleEndian.PutUint32(dst[12:16], D)
}

// DecryptBlock 对 16B 密文就地解密。
// 标准 RC6 解密（加密的逆过程）：
//
//	C -= S[2r+3]; A -= S[2r+2]
//	for i=r..1:
//	    (A,B,C,D) = (D,A,B,C)
//	    u = rotl(D*(2D+1), 5)
//	    t = rotl(B*(2B+1), 5)
//	    C = rotr(C - S[2i+1], t&31) ^ u
//	    A = rotr(A - S[2i],   u&31) ^ t
//	D -= S[1]; B -= S[0]
func (c *RC6Cipher) DecryptBlock(dst, src []byte) {
	_ = src[15]
	_ = dst[15]
	A := binary.LittleEndian.Uint32(src[0:4])
	B := binary.LittleEndian.Uint32(src[4:8])
	C := binary.LittleEndian.Uint32(src[8:12])
	D := binary.LittleEndian.Uint32(src[12:16])

	C -= c.S[2*rc6Rounds+3]
	A -= c.S[2*rc6Rounds+2]

	for i := rc6Rounds; i >= 1; i-- {
		A, B, C, D = D, A, B, C
		u := bits.RotateLeft32(D*(2*D+1), 5)
		t := bits.RotateLeft32(B*(2*B+1), 5)
		C = bits.RotateLeft32(C-c.S[2*i+1], -int(t&31)) ^ u
		A = bits.RotateLeft32(A-c.S[2*i], -int(u&31)) ^ t
	}
	D -= c.S[1]
	B -= c.S[0]

	binary.LittleEndian.PutUint32(dst[0:4], A)
	binary.LittleEndian.PutUint32(dst[4:8], B)
	binary.LittleEndian.PutUint32(dst[8:12], C)
	binary.LittleEndian.PutUint32(dst[12:16], D)
}

// EncryptECB 用 ECB 模式连续加密多个 16B block。ACE 上报包里的
// body 目前看起来就是裸 ECB 拼接（后续若发现 CBC/IV 再改）。
func (c *RC6Cipher) EncryptECB(dst, src []byte) error {
	if len(src)%rc6BlockSz != 0 || len(dst) < len(src) {
		return ErrRC6BadBlockSize
	}
	for off := 0; off < len(src); off += rc6BlockSz {
		c.EncryptBlock(dst[off:off+rc6BlockSz], src[off:off+rc6BlockSz])
	}
	return nil
}

// DecryptECB 用 ECB 模式连续解密多个 16B block。
func (c *RC6Cipher) DecryptECB(dst, src []byte) error {
	if len(src)%rc6BlockSz != 0 || len(dst) < len(src) {
		return ErrRC6BadBlockSize
	}
	for off := 0; off < len(src); off += rc6BlockSz {
		c.DecryptBlock(dst[off:off+rc6BlockSz], src[off:off+rc6BlockSz])
	}
	return nil
}
