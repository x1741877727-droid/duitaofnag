// ace_decrypt_verify 对 /tmp/ace_packets.bin 里抽到的候选包试跑 Mode 2 RC6 解密。
// 输出到 stdout：穷举 (headerSize, keyIdx, seedKind) 组合，挑"解出来像样"的报告。
package main

import (
	"encoding/binary"
	"fmt"
	"io"
	"os"
	"strings"

	"gameproxy/acecrypto"
)

func main() {
	f, err := os.Open("/tmp/ace_packets.bin")
	if err != nil {
		fmt.Println("open bin:", err)
		os.Exit(1)
	}
	defer f.Close()

	// 读所有包
	var packets [][]byte
	for {
		var lenHdr [4]byte
		if _, err := io.ReadFull(f, lenHdr[:]); err != nil {
			break
		}
		n := binary.BigEndian.Uint32(lenHdr[:])
		buf := make([]byte, n)
		if _, err := io.ReadFull(f, buf); err != nil {
			break
		}
		packets = append(packets, buf)
	}
	fmt.Printf("loaded %d candidate packets\n", len(packets))

	// 只挑 mode=2 的（header[9]==2）
	var mode2 [][]byte
	for _, p := range packets {
		if len(p) >= 16 && p[0] == 0x01 && p[9] == 0x02 {
			mode2 = append(mode2, p)
		}
	}
	fmt.Printf("mode2 candidates (header[9]==2): %d\n", len(mode2))
	if len(mode2) == 0 {
		return
	}

	// 候选 header 大小（body 必须 16B 对齐）
	// 对 137B 包：header=9,25,41,57 都合法
	headerSizes := []int{9, 25, 41, 57}

	// 按字节大多数是 printable ASCII 或其它"合理"性评估 plaintext
	score := func(pt []byte) (int, string) {
		var printable, zeros, ascii int
		for _, b := range pt {
			if b == 0 {
				zeros++
			} else if b >= 0x20 && b < 0x7f {
				printable++
				ascii++
			} else if b >= 0x20 {
				printable++ // 非 ASCII 可打印？基本没用
			}
		}
		sc := ascii*2 + zeros // ASCII 权重高，0 也是合理的（结构化 header/length 字段）
		sample := pt
		if len(sample) > 48 {
			sample = sample[:48]
		}
		return sc, fmt.Sprintf("%s", sanitize(sample))
	}

	// 穷举
	type result struct {
		pktIdx   int
		headerSz int
		keyIdx   int
		seedKind string // "raw" or "xor17"
		score    int
		preview  string
		hex      string
	}
	var top []result

	seeds := map[string][10][]byte{
		"raw":   acecrypto.KeyTable,
		"xor17": acecrypto.KeyTableDeobf(),
	}

	for pi, pkt := range mode2[:min(10, len(mode2))] { // 取前 10 个包试
		for _, hs := range headerSizes {
			bodyLen := len(pkt) - hs
			if bodyLen <= 0 || bodyLen%16 != 0 {
				continue
			}
			body := pkt[hs:]
			for keyIdx := 0; keyIdx < 10; keyIdx++ {
				for kind, table := range seeds {
					seed := table[keyIdx]
					cip := acecrypto.NewRC6Cipher(seed)
					out := make([]byte, len(body))
					if err := cip.DecryptECB(out, body); err != nil {
						continue
					}
					sc, prev := score(out)
					if sc > 20 {
						preview := out[:min(48, len(out))]
						top = append(top, result{
							pktIdx: pi, headerSz: hs, keyIdx: keyIdx, seedKind: kind,
							score: sc, preview: prev, hex: fmt.Sprintf("%x", preview),
						})
					}
				}
			}
		}
	}

	// 按 score 降序
	for i := 0; i < len(top); i++ {
		for j := i + 1; j < len(top); j++ {
			if top[j].score > top[i].score {
				top[i], top[j] = top[j], top[i]
			}
		}
	}

	fmt.Println("\n=== TOP candidates (score > 20) ===")
	if len(top) == 0 {
		fmt.Println("  NONE — RC6 解密完全没解出可读明文")
	}
	for i, r := range top {
		if i >= 20 {
			break
		}
		fmt.Printf("[%2d] pkt#%d hdr=%d keyIdx=%d seed=%s score=%d\n    ascii: %s\n    hex  : %s\n",
			i, r.pktIdx, r.headerSz, r.keyIdx, r.seedKind, r.score, r.preview, r.hex)
	}

	// 同时，用我们当前 acecrypto.Decrypt 的"默认 header layout" 跑一遍，看结果
	fmt.Println("\n=== Using acecrypto.Decrypt() on first 5 mode2 packets ===")
	for i, pkt := range mode2[:min(5, len(mode2))] {
		plain, h, err := acecrypto.Decrypt(pkt)
		fmt.Printf("[%d] len=%d err=%v", i, len(pkt), err)
		if h != nil {
			fmt.Printf(" mode=%d keyIdx=%d bodyLen=%d crc=%08x", h.ModeIdx, h.KeyIdx, h.BodyLen, h.CRC32Body)
		}
		if plain != nil {
			n := min(32, len(plain))
			fmt.Printf("  plain[:%d]=%x", n, plain[:n])
		}
		fmt.Println()
	}
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func sanitize(b []byte) string {
	var sb strings.Builder
	for _, c := range b {
		if c >= 0x20 && c < 0x7f {
			sb.WriteByte(c)
		} else {
			sb.WriteByte('.')
		}
	}
	return sb.String()
}
