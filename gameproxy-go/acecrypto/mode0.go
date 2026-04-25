package acecrypto

// Mode 0: ACE 自研流密码 / 置换盒算法
//
// 基于 libtersafe.so 反编译：
//   - init  @ RVA 0x59cb80  (FUN_0059cb80)
//   - proc  @ RVA 0x59cce8  (FUN_0059cce8)
//
// 算法组成：
//  1. whitened key K[0..7] = seed[0..7] XOR {0x8a,0xe6,0x9b,0xf3,0xc1,0x7d,0x40,0x25}
//  2. 初始 sbox (256B) 由 Mode0PadA/PadB 交替填入并每轮 +0x10
//     迭代 16 次，每次写 16B：PadA(+n*0x10) 8B | PadB(+n*0x10) 8B
//     这里 PadA+PadB 在 libtersafe 数据段是连续 16B (UNK_0019a160..a170)，
//     因此本实现把 Mode0PadA/B 连起来当作 16B 的 pad 模板 P[16]。
//  3. LCG 洗牌：seed = (K[6]<<8 | K[5]<<16 | K[7]) ^ (K[2]<<8 | K[1]<<16 | K[3])
//     对 i in [0,256)：state = state*0x343fd + 0x269ec3 (MS VC rand LCG)
//                      idx = (state >> 16) & 0xff
//                      swap sbox[i], sbox[idx]
//  4. encrypt 模式 (flag bit0 == 0)：sbox 取反 (inverse permutation)
//     decrypt 模式 (flag bit0 == 1)：sbox 保留原洗牌结果
//  5. 处理每字节：
//       encrypt: ct[i] = i   ^ K[i&7] ^ invSbox[(pt[i] ^ K[i&7]) & 0xff]
//       decrypt: pt[i] = 0   ^ K[i&7] ^   sbox[(ct[i] ^ i ^ K[i&7]) & 0xff]

// mode0WhitenPad 对应 init 里 key[0..7] ^ 这 8 字节。
var mode0WhitenPad = [8]byte{0x8a, 0xe6, 0x9b, 0xf3, 0xc1, 0x7d, 0x40, 0x25}

// mode0Init 根据 seed + flag(bit0) 准备 whitened key (8B) 和 sbox (256B)。
// encrypt flag=0, decrypt flag=1. 与反编译 FUN_0059cb80 一一对应。
func mode0Init(seed []byte, flag uint32) (wk [8]byte, sbox [256]byte) {
	// 1) whitened key: seed[0..7] XOR mode0WhitenPad. 不足 8B 的部分用 0 补。
	var k8 [8]byte
	for i := 0; i < 8; i++ {
		var s byte
		if i < len(seed) {
			s = seed[i]
		}
		k8[i] = s ^ mode0WhitenPad[i]
	}
	wk = k8

	// 2) 初始 sbox：Mode0PadA(16B) + Mode0PadB 在反编译里是同一个 16B pad 被展开
	//    成 uVar5(低8)/uVar6(高8)。看 dumped 常量 PadA={00..0f} PadB={21..30}，
	//    但反编译展示每次写 16B：先 PadA 再 PadB。下面按 "16B pad" 填入 16 轮，每轮 +0x10。
	// 反编译里 uVar5=_UNK_0019a160 (PadA[0..7]), uVar6=_UNK_0019a168 (PadA[8..15])。
	// 每轮写 16B = PadA 本身，下一轮每字节 +0x10。
	pad := Mode0PadA
	for round := 0; round < 16; round++ {
		base := round * 16
		for j := 0; j < 16; j++ {
			sbox[base+j] = pad[j] + byte(round*0x10)
		}
	}

	// 3) LCG 洗牌
	// seed = (K[6]<<8 | K[5]<<16 | K[7]) ^ (K[2]<<8 | K[1]<<16 | K[3])
	state := uint32(k8[6])<<8 | uint32(k8[5])<<16 | uint32(k8[7])
	state ^= uint32(k8[2])<<8 | uint32(k8[1])<<16 | uint32(k8[3])
	for i := 0; i < 256; i++ {
		state = state*0x343fd + 0x269ec3
		idx := (state >> 16) & 0xff
		sbox[i], sbox[idx] = sbox[idx], sbox[i]
	}

	// 4) encrypt 模式下取 inverse permutation
	if (flag & 1) == 0 {
		var src [256]byte = sbox
		for i := 0; i < 256; i++ {
			sbox[src[i]] = byte(i)
		}
	}
	return
}

// mode0Process 按字节流加/解密 in → out，长度相同。flag bit0：0=encrypt 1=decrypt。
func mode0Process(seed, in []byte, flag uint32) []byte {
	wk, sbox := mode0Init(seed, flag)
	out := make([]byte, len(in))
	enc := (flag & 1) == 0
	for i := 0; i < len(in); i++ {
		k := wk[i&7]
		if enc {
			// ct[i] = i ^ k ^ sbox[(pt[i] ^ k) & 0xff]   (sbox 已取反)
			out[i] = byte(i) ^ k ^ sbox[(in[i]^k)&0xff]
		} else {
			// pt[i] = k ^ sbox[(ct[i] ^ i ^ k) & 0xff]
			out[i] = k ^ sbox[(in[i]^byte(i)^k)&0xff]
		}
	}
	return out
}

func encryptMode0(plaintext, seed []byte) ([]byte, error) {
	return mode0Process(seed, plaintext, 0), nil
}

func decryptMode0(ciphertext, seed []byte) ([]byte, error) {
	return mode0Process(seed, ciphertext, 1), nil
}
