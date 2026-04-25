package acecrypto

import (
	"encoding/binary"
	"errors"
)

// ACE 上报包 header 粗结构（各字段精确 offset 待样本校准）：
//
//	[0]      modeIdx   (0..2)                 明文
//	[1]      keyIdx    (0..9)                 明文
//	[2-3]    ? (保留 / flags，暂未解析)
//	[4-7]    CRC32(plaintext_body) little-endian
//	[8-17]   ? (保留 / 版本 / timestamp，暂未解析)
//	[18-33]  sessionKey (16B, 透传, 不参与加密)
//	[...]    其他 header 字段
//	[HeaderSize-4 : HeaderSize]
//	         bodyLen big-endian (放在 header 末 4B，待校准)
//	[HeaderSize:]
//	         encrypted body (Mode 2 下为 RC6-ECB 16B 对齐)
//
// 所有 offset 都是 **暂定值**，等 ace_constants.json 或样本到位后再校准。
const (
	// HeaderSize 暂按 40B 的粗结构：
	//   [0..17]  : modeIdx/keyIdx/flags/crc32/其它未解析字段
	//   [18..33] : sessionKey (16B)
	//   [34..35] : 未解析保留
	//   [36..39] : bodyLen (BE)
	// 精确 offset 待样本校准。
	HeaderSize = 40

	offModeIdx    = 0
	offKeyIdx     = 1
	offCRC32      = 4  // 4..8 LE
	offSessionKey = 18 // 18..34 (16B)
	lenSessionKey = 16
	offBodyLen    = HeaderSize - 4 // 末尾 4B BE，暂定
)

// ErrShortPacket packet 长度不足 header 要求。
var ErrShortPacket = errors.New("acecrypto: packet too short")

// ErrInvalidMode modeIdx 超出 0..2。
var ErrInvalidMode = errors.New("acecrypto: invalid mode index (expect 0..2)")

// ErrInvalidKeyIdx keyIdx 超出 0..9。
var ErrInvalidKeyIdx = errors.New("acecrypto: invalid key index (expect 0..9)")

// ErrBadCRC 解密后 body 的 CRC32 与 header 声明不符。
var ErrBadCRC = errors.New("acecrypto: body CRC32 mismatch")

// Header 解析后的 ACE 包头。
type Header struct {
	ModeIdx    uint8
	KeyIdx     uint8
	CRC32Body  uint32
	SessionKey [lenSessionKey]byte
	BodyLen    uint32
	// Raw 原始 header 字节，透传用。长度固定为 HeaderSize。
	Raw [HeaderSize]byte
}

// ParseHeader 从 packet 前 HeaderSize 字节解析 header。
// 不做 CRC 校验，只负责字段切分。
func ParseHeader(packet []byte) (*Header, error) {
	if len(packet) < HeaderSize {
		return nil, ErrShortPacket
	}
	h := &Header{
		ModeIdx:   packet[offModeIdx],
		KeyIdx:    packet[offKeyIdx],
		CRC32Body: binary.LittleEndian.Uint32(packet[offCRC32 : offCRC32+4]),
		BodyLen:   binary.BigEndian.Uint32(packet[offBodyLen : offBodyLen+4]),
	}
	copy(h.SessionKey[:], packet[offSessionKey:offSessionKey+lenSessionKey])
	copy(h.Raw[:], packet[:HeaderSize])

	if h.ModeIdx > 2 {
		return h, ErrInvalidMode
	}
	if h.KeyIdx >= uint8(len(KeyTable)) {
		return h, ErrInvalidKeyIdx
	}
	return h, nil
}

// Marshal 把 Header 字段写回 Raw 中对应 offset，返回 HeaderSize 字节。
// 对那些尚未解析的字段（如 [2-3] / [8-17] / [34..offBodyLen]）保持
// Raw 原样，不做任何修改 → 保证透传。
func (h *Header) Marshal() []byte {
	buf := make([]byte, HeaderSize)
	copy(buf, h.Raw[:])

	buf[offModeIdx] = h.ModeIdx
	buf[offKeyIdx] = h.KeyIdx
	binary.LittleEndian.PutUint32(buf[offCRC32:offCRC32+4], h.CRC32Body)
	copy(buf[offSessionKey:offSessionKey+lenSessionKey], h.SessionKey[:])
	binary.BigEndian.PutUint32(buf[offBodyLen:offBodyLen+4], h.BodyLen)
	return buf
}
