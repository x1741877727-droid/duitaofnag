package acecrypto

import "hash/crc32"

// CRC32IEEE 计算标准 IEEE CRC32（多项式 0xEDB88320），
// 与 ACE 上报包中 header[4:8] 处校验的算法一致。
//
// 标准测试向量："123456789" -> 0xCBF43926
func CRC32IEEE(data []byte) uint32 {
	return crc32.ChecksumIEEE(data)
}
