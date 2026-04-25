package main

import (
	"bytes"
	"encoding/binary"
	"encoding/hex"
	"io"
	"net"
	"sync"
	"time"
)

// carteamMarkers: 包内出现这些字串 = 队伍协议消息，dump 用于反向 status 字段
var carteamMarkers = [][]byte{
	[]byte("notify_update_carteam_member"),
	[]byte("change_carteam_rsp"),
	[]byte("change_carteam_req"),
	[]byte("notify_join_carteam"),
	[]byte("prepare_carteam"),
	[]byte("ready_carteam"),
	[]byte("team_info_notify"),
}

// logCarteamPacket：包内含 carteam 协议字串就完整 dump 到日志
//
// 用途：反向 carteam 协议结构（特别是 status / 准备态字段）
// 取消 vs 准备 两次操作的包做 byte-level diff = 1 byte 差异 = status 字段
func logCarteamPacket(data []byte, dir string, connID string, clientIP string, dstPort int) {
	if len(data) < 28 {
		return // 太短不可能含完整 marker
	}
	var matched string
	for _, m := range carteamMarkers {
		if bytes.Contains(data, m) {
			matched = string(m)
			break
		}
	}
	if matched == "" {
		return
	}
	// 大包截前 4KB 防日志爆炸
	dumpLen := len(data)
	if dumpLen > 4096 {
		dumpLen = 4096
	}
	logInfo("[CARTEAM] dir=%s conn=%s ip=%s port=%d marker=%s size=%d hex=%x",
		dir, connID, clientIP, dstPort, matched, len(data), data[:dumpLen])
}

// parseFrame17500 解析 17500 控制帧头
// 结构发现（2026-04-22 pcap 分析）：
//
//	offset 0-5:  33 66 00 0a 00 0a            (magic)
//	offset 6-7:  type (LE uint16), 主要类型 0x1340
//	offset 8-11: 01 00 00 00                  (const)
//	offset 12-15: srv_seq (LE uint32)
//	offset 16:   0x19                          (固定)
//	offset 17:   0x00                          (固定)
//	offset 18:   0x00                          (固定)
//	offset 19-20: payload_len (BE uint16)      ★ 声明的 payload 字节数
//	offset 21:   0x00                          (固定)
//	offset 22-23: 00 10                        (固定)
//	offset 24:   0x79                          (固定)
//	offset 25+:  encrypted payload
//
// total_frame_size = payload_len + 25（header 固定 25 字节）
// 如果 len(data) < total_frame_size，剩余字节在后续无 magic 头的 Read 里
func parseFrame17500(data []byte) (hasMagic bool, typeCode uint16, totalSize int) {
	if len(data) < 21 {
		return false, 0, 0
	}
	if data[0] != 0x33 || data[1] != 0x66 || data[2] != 0x00 ||
		data[3] != 0x0a || data[4] != 0x00 || data[5] != 0x0a {
		return false, 0, 0
	}
	typeCode = binary.LittleEndian.Uint16(data[6:8])
	payloadLen := binary.BigEndian.Uint16(data[19:21])
	totalSize = int(payloadLen) + 25
	return true, typeCode, totalSize
}

// aceHeartbeatTypes 外层 ACE 帧类型白名单：只有这些 type 的帧才允许 Rule 1 扫描
// 心跳/data1 上报类型 —— 含 01 0A 00 23 设备指纹子包，改写它 = 防封
// 登录握手类型（0x072a, 0x0336, 0x0435 等）不在此列，不会被扫描
var aceHeartbeatTypes = map[uint16]bool{
	0x01da: true, // data1 主通道心跳上报
	0x01ed: true,
	0x01f2: true,
}

// applyRule1 C→S 方向设备指纹伪装（01 0A 00 23 → +3=0x37, +11=0x00）
// 三重精准过滤（防止误伤地图选择/战令/账号等大数据包）：
//   1. 外层 ACE 帧 type 必须在心跳白名单（aceHeartbeatTypes）
//   2. 包总长度必须 <= 500B（真实心跳上报 ≤ 218B，中位数 ~200B）
//   3. pattern `01 0A 00 23` 必须出现在固定的指纹子包位置区间（pos 50-80，
//      历史 pcap 观察 pos 61 稳定）
func applyRule1(data []byte) ([]byte, bool) {
	if len(data) < 6 {
		return data, false
	}
	// 1. 必须是 ACE 帧（magic 01 00 00 00-04）
	if data[0] != 0x01 || data[1] != 0x00 || data[2] != 0x00 || data[3] > 0x04 {
		return data, false
	}
	// 2. 外层帧 type 必须是心跳型（0x01da / 0x01ed / 0x01f2）
	frameType := uint16(data[4]) | uint16(data[5])<<8
	if !aceHeartbeatTypes[frameType] {
		return data, false
	}
	// 3. ★ 新增：总长度必须在心跳包范围内（<= 500B），大包多半是地图/战令/好友数据
	const heartbeatMaxSize = 500
	if len(data) > heartbeatMaxSize {
		return data, false
	}
	// 通过所有过滤，扫描并改写 01 0A 00 23 子包
	// ★ 另加：pattern 位置约束（pos 50-80，心跳包典型位置）
	var out []byte
	changed := false
	const patternMinPos = 50
	const patternMaxPos = 80
	i := patternMinPos
	for i <= len(data)-4 && i <= patternMaxPos {
		j := bytes.Index(data[i:patternMaxPos+4], []byte{0x01, 0x0a, 0x00, 0x23})
		if j < 0 {
			break
		}
		pos := i + j
		if out == nil {
			out = make([]byte, len(data))
			copy(out, data)
		}
		out[pos+3] = 0x37
		if pos+11 < len(out) {
			out[pos+11] = 0x00
		}
		changed = true
		i = pos + 4
	}
	if changed {
		return out, true
	}
	return data, false
}

// extractUUIDFromACE 从 C→S ACE 218B 帧提取设备 UUID
// 返回 32 字符 ASCII UUID 或空串
// 实测帧结构（pcap 验证）：
//   byte[78] = 0x21     (33 字节 pascal length prefix: 1B len + 32B UUID)
//   byte[79:111] = 32 字节 ASCII UUID（0-9 A-F 大写十六进制）
func extractUUIDFromACE(data []byte) string {
	if len(data) < 111 {
		return ""
	}
	// ACE magic + 外层 type 0x01da
	if data[0] != 0x01 || data[1] != 0x00 || data[2] != 0x00 || data[3] != 0x00 {
		return ""
	}
	if data[4] != 0xda || data[5] != 0x01 {
		return ""
	}
	// UUID 长度前缀必须是 0x21 (33)
	if data[78] != 0x21 {
		return ""
	}
	uuid := data[79:111]
	for _, b := range uuid {
		if b < 0x20 || b >= 0x7f {
			return ""
		}
	}
	return string(uuid)
}

// applyRule2Sized 带帧级 size 过滤的 Rule 2（G6 Connector 精细化）
// 背景（2026-04-23 libgcloud.so 逆向 + 2026-04-25 B1 调查）：
//   - 17500 端口同时承载 **G6 frame** 和 **非 G6 大包**（活动 banner/资源下发）
//   - G6 frame: 头 25B 明文 magic `33 66 00 0a 00 0a`，body TDR 加密
//   - 非 G6 大包（14-21KB）：另一种协议，结构未定 — **2026-04-25 实证 Rule2 命中 465 次**
//   - 早期版本在 "非 G6 时 fallback applyRule2(tail)" → 导致活动延迟 55-170 秒
//
// 2026-04-25 B1 实验修订：
//   - 非 G6 字节：**直接跳过**，不再 fallback 全量扫
//   - G6 frame 里 body < maxSmallBody 的才改 `0A 92 → 0A 11`（保留原行为）
//   - =0 时全量模式（回到 applyRule2，兼容旧）
//
// 目标：让活动 banner / 大资源下发 0 改写，防封仍由 G6 control frame Rule2 + 443 fingerprint 保证
func applyRule2Sized(data []byte, maxSmallBody int) ([]byte, bool) {
	if len(data) < 2 {
		return data, false
	}
	if maxSmallBody <= 0 {
		return applyRule2(data)
	}
	// 前置：整包必须以 G6 magic 开头，否则放行（非 G6 大包保护）
	if len(data) < 6 || data[0] != 0x33 || data[1] != 0x66 || data[2] != 0x00 ||
		data[3] != 0x0a || data[4] != 0x00 || data[5] != 0x0a {
		return data, false
	}
	var out []byte
	changed := false
	pos := 0
	// 逐帧解析 17500 frame，判断 body size
	for pos+21 <= len(data) {
		if data[pos] != 0x33 || data[pos+1] != 0x66 || data[pos+2] != 0x00 ||
			data[pos+3] != 0x0a || data[pos+4] != 0x00 || data[pos+5] != 0x0a {
			// 流中途丢 G6 magic（跨 TCP 包应用层分片、或协议切换）
			// B1 决策：停在这里，不 fallback 到全量扫 —— 避免误改非 G6 段
			break
		}
		payloadLen := int(binary.BigEndian.Uint16(data[pos+19 : pos+21]))
		frameTotal := payloadLen + 25
		frameEnd := pos + frameTotal
		if frameEnd > len(data) {
			frameEnd = len(data)
		}
		if payloadLen < maxSmallBody {
			// 小帧：在 body 段跑 Rule 2
			bodyStart := pos + 25
			i := bodyStart
			for i <= frameEnd-2 {
				if data[i] == 0x0a && data[i+1] == 0x92 {
					if out == nil {
						out = make([]byte, len(data))
						copy(out, data)
					}
					out[i+1] = 0x11
					changed = true
					i += 2
				} else {
					i++
				}
			}
		}
		// 大帧：跳过不动
		if frameEnd >= len(data) {
			break
		}
		pos = frameEnd
	}
	if changed {
		return out, true
	}
	return data, false
}

// applyRule2Fingerprint 用 8B fingerprint 精确匹配 ACE 规则下发包（443 S→C 用）
// 2026-04-24 离线分析 1893 个 conn / 49304 G6 frame 后发现：
//   bytes[48:56] == 3c d0 ec aa 88 be 0a 92 是 ACE 规则下发的 100% 唯一签名
//   全部 114 次命中集中在 conn_000051（443 G6 ACE 专用流）
//   其他业务包（活动/商城/Banner/战斗/聊天）零误命中
//   碰撞概率 < 1/2^64
// patch: 命中后将 byte 55（即 0x92 位置）改为 0x11
// 帧格式: bytes[0:6] G6 magic, bytes[19:21] payload_len BE, bytes[25:] payload
func applyRule2Fingerprint(data []byte) ([]byte, bool) {
	if len(data) < 56 {
		return data, false
	}
	var out []byte
	changed := false
	pos := 0
	for pos+21 <= len(data) {
		// G6 magic check
		if data[pos] != 0x33 || data[pos+1] != 0x66 || data[pos+2] != 0x00 ||
			data[pos+3] != 0x0a || data[pos+4] != 0x00 || data[pos+5] != 0x0a {
			break
		}
		payloadLen := int(binary.BigEndian.Uint16(data[pos+19 : pos+21]))
		frameTotal := payloadLen + 25
		frameEnd := pos + frameTotal
		if frameEnd > len(data) {
			frameEnd = len(data)
		}
		// Fingerprint check at frame offset 48..55
		if pos+56 <= len(data) &&
			data[pos+48] == 0x3c && data[pos+49] == 0xd0 &&
			data[pos+50] == 0xec && data[pos+51] == 0xaa &&
			data[pos+52] == 0x88 && data[pos+53] == 0xbe &&
			data[pos+54] == 0x0a && data[pos+55] == 0x92 {
			if out == nil {
				out = make([]byte, len(data))
				copy(out, data)
			}
			out[pos+55] = 0x11
			changed = true
		}
		if frameEnd >= len(data) {
			break
		}
		pos = frameEnd
	}
	if changed {
		return out, true
	}
	return data, false
}

// applyActivityDropFrames 从 17500 s2c TCP 流中移除活动资源大包 frame
// 2026-04-24 多次标签采集 + 3-way diff 发现：
//   活动资源大包 s2c frame bytes[21:25] in {00 10 51 00, 00 10 65 00}
//   match3 对战时 100% 用 00 10 53 00 (不重叠)
//   lobby_idle / shop 时段 0 大包 s2c
//   可拦 55/62 = 88.7% 活动大包，0 误伤战斗
// 实现：frame-level DROP，整帧从 stream 移除（不是改字节）
//   因为 TCP 是流，多个 frame 拼一起，需要 reassemble 删除 activity frame 后的剩余流
// 注意：如果 frame 跨 TCP segment 会丢帧，但 Go relay buffer 通常完整
func applyActivityDropFrames(data []byte) ([]byte, bool) {
	if len(data) < 25 {
		return data, false
	}
	out := make([]byte, 0, len(data))
	changed := false
	pos := 0
	for pos+25 <= len(data) {
		// G6 magic check
		if data[pos] != 0x33 || data[pos+1] != 0x66 || data[pos+2] != 0x00 ||
			data[pos+3] != 0x0a || data[pos+4] != 0x00 || data[pos+5] != 0x0a {
			// 不是 G6 frame，后面剩余（可能是 TCP 续包）原样保留
			out = append(out, data[pos:]...)
			pos = len(data)
			break
		}
		payLen := int(binary.BigEndian.Uint16(data[pos+19 : pos+21]))
		frameEnd := pos + 25 + payLen
		if frameEnd > len(data) {
			// incomplete frame, keep as-is (don't break stream)
			out = append(out, data[pos:]...)
			pos = len(data)
			break
		}
		// Activity channel check: bytes[21:25] == 00 10 {51|65} 00
		isActivity := data[pos+21] == 0x00 && data[pos+22] == 0x10 &&
			(data[pos+23] == 0x51 || data[pos+23] == 0x65) &&
			data[pos+24] == 0x00
		if isActivity {
			// DROP this entire frame
			changed = true
		} else {
			out = append(out, data[pos:frameEnd]...)
		}
		pos = frameEnd
	}
	if changed {
		return out, true
	}
	return data, false
}

// applyActivityBreak 破坏活动 s2c 包：把 opcode 末字节 0x04/0x05 改成 0x00
// 2026-04-24 用户标签采集 + 3-way diff 发现：
//   activity s2c frame bytes[8:12] 100% 命中 01 00 00 04 (77.8%) 或 01 00 00 05 (22.2%)
//   shop / lobby_idle 0% 命中（187 + 181 包验证）
//   把末字节改 0x00 让客户端 G6 router 找不到 handler，静默丢弃 frame
//   不影响 server（server 不知道客户端没正确解析），不触发重传
//   优于 DROP：避免客户端重试反复转圈
func applyActivityBreak(data []byte) ([]byte, bool) {
	if len(data) < 12 {
		return data, false
	}
	var out []byte
	changed := false
	pos := 0
	for pos+12 <= len(data) {
		// G6 magic check
		if data[pos] != 0x33 || data[pos+1] != 0x66 || data[pos+2] != 0x00 ||
			data[pos+3] != 0x0a || data[pos+4] != 0x00 || data[pos+5] != 0x0a {
			break
		}
		// activity opcode: 01 00 00 04 or 01 00 00 05 at offset 8-11
		if data[pos+8] == 0x01 && data[pos+9] == 0x00 && data[pos+10] == 0x00 &&
			(data[pos+11] == 0x04 || data[pos+11] == 0x05) {
			if out == nil {
				out = make([]byte, len(data))
				copy(out, data)
			}
			out[pos+11] = 0x00 // break opcode → client router 丢弃
			changed = true
		}
		// advance to next frame
		if pos+21 > len(data) {
			break
		}
		payloadLen := int(binary.BigEndian.Uint16(data[pos+19 : pos+21]))
		frameTotal := payloadLen + 25
		if pos+frameTotal > len(data) {
			break
		}
		pos += frameTotal
	}
	if changed {
		return out, true
	}
	return data, false
}

// applyRule2 只跑 Rule 2（S→C 17500 用）：0A 92 → +1=0x11
func applyRule2(data []byte) ([]byte, bool) {
	if len(data) < 2 {
		return data, false
	}
	var out []byte
	changed := false
	i := 0
	for i <= len(data)-2 {
		j := bytes.Index(data[i:], []byte{0x0a, 0x92})
		if j < 0 {
			break
		}
		pos := i + j
		if out == nil {
			out = make([]byte, len(data))
			copy(out, data)
		}
		out[pos+1] = 0x11
		changed = true
		i = pos + 2
	}
	if changed {
		return out, true
	}
	return data, false
}

// patchWPEAdvanced [DEPRECATED] 旧版同时应用 Rule 1 + Rule 2，会误伤登录数据
// 保留兼容，但 relay 里已拆分成 applyRule1/applyRule2 按方向使用
func patchWPEAdvanced(data []byte) ([]byte, bool) {
	if len(data) < 4 {
		return data, false
	}
	var out []byte
	changed := false
	// Rule 1: 01 0A 00 23 → +3=0x37, +11=0x00
	i := 0
	for i <= len(data)-4 {
		j := bytes.Index(data[i:], []byte{0x01, 0x0a, 0x00, 0x23})
		if j < 0 {
			break
		}
		pos := i + j
		if out == nil {
			out = make([]byte, len(data))
			copy(out, data)
		}
		out[pos+3] = 0x37
		if pos+11 < len(out) {
			out[pos+11] = 0x00
		}
		changed = true
		i = pos + 4
	}
	// Rule 2: 0A 92 → +1=0x11
	src := data
	if out != nil {
		src = out
	}
	i = 0
	for i <= len(src)-2 {
		j := bytes.Index(src[i:], []byte{0x0a, 0x92})
		if j < 0 {
			break
		}
		pos := i + j
		if out == nil {
			out = make([]byte, len(data))
			copy(out, data)
			src = out
		}
		out[pos+1] = 0x11
		changed = true
		i = pos + 2
	}
	if changed {
		return out, true
	}
	return data, false
}

// 17500 状态机阶段（agent2 方案核心）
const (
	stageWaitHandshake = iota // 0: 初始，未见任何握手
	stageLoginReq             // 1: 已见 C2S 1001 握手请求
	stageLoginData            // 2: 已见 C2S 2001 登录请求
	stagePostLogin            // 3: 已见 C2S 4013 大帧（登录数据上传完成），服务端即将推活动
)

// parseC2SForState 解析 C2S 帧驱动状态机（不改数据，只观察）
//   C2S 登录序列（跨会话 100% 一致）：
//     1001 握手 → 2001 登录 → 4013 大帧(登录数据上传, len>=1000) → 进入 POST_LOGIN
//   POST_LOGIN 状态持续 15 秒作为"活动推送窗口"
func parseC2SForState(data []byte, st *connActState, dstAddr string) {
	// 可能一次 Read 包含多帧堆叠，循环解析
	pos := 0
	for pos+8 <= len(data) {
		// 必须以 magic 开头（33 66 00 0a 00 0a）
		if data[pos] != 0x33 || data[pos+1] != 0x66 ||
			data[pos+2] != 0x00 || data[pos+3] != 0x0a ||
			data[pos+4] != 0x00 || data[pos+5] != 0x0a {
			return
		}
		// type bytes 原样（byte[6]byte[7]）
		b6 := data[pos+6]
		b7 := data[pos+7]
		// 读 payload_len（BE uint16 at offset 19-20）
		if pos+21 > len(data) {
			return
		}
		payloadLen := int(binary.BigEndian.Uint16(data[pos+19 : pos+21]))
		frameTotal := payloadLen + 25

		st.mu.Lock()
		switch {
		case st.stage == stageWaitHandshake && b6 == 0x10 && b7 == 0x01:
			st.stage = stageLoginReq
			logInfo("[ACT-FSM] %s → LOGIN_REQ (C2S 1001, len=%d)", dstAddr, frameTotal)
		case st.stage == stageLoginReq && b6 == 0x20 && b7 == 0x01:
			st.stage = stageLoginData
			logInfo("[ACT-FSM] %s → LOGIN_DATA (C2S 2001, len=%d)", dstAddr, frameTotal)
		case st.stage == stageLoginData && b6 == 0x40 && b7 == 0x13 && frameTotal >= 1000:
			st.stage = stagePostLogin
			st.postLoginAt = time.Now()
			logInfo("[ACT-FSM] %s → POST_LOGIN (C2S 4013 len=%d, 启动 15s 活动窗口)",
				dstAddr, frameTotal)
		}
		st.mu.Unlock()

		// 下一帧
		if frameTotal <= 0 || pos+frameTotal > len(data) {
			return
		}
		pos += frameTotal
	}
}

// connActState 单连接的活动拦截状态（C→S 和 S→C goroutine 共享）
type connActState struct {
	mu           sync.Mutex
	stage        int
	postLoginAt  time.Time
	uuid         string // 从 C→S ACE 帧提取的设备 UUID（=模拟器指纹）
}

// relay 双向转发：C→S 解析帧驱动状态机，S→C 根据状态 drop 活动
func (s *Socks5Server) relay(client, remote net.Conn,
	dstAddr string, dstPort int, connID string) {

	clientIP := extractClientIP(client)
	connStart := time.Now()

	// 活动状态机（仅 17500 连接有效，其他端口无影响）
	actState := &connActState{stage: stageWaitHandshake}

	var wg sync.WaitGroup
	wg.Add(2)

	closeOnce := sync.Once{}
	closeBoth := func() {
		closeOnce.Do(func() {
			client.Close()
			remote.Close()
		})
	}

	// C→S
	go func() {
		defer wg.Done()
		defer closeBoth()

		buf := make([]byte, 65536)
		seq := 0
		for {
			n, err := client.Read(buf)
			if n > 0 {
				data := buf[:n]
				if s.capture != nil && connID != "" {
					s.capture.SavePacket(connID, copyBytes(data), "send", seq)
				}
				// 反向 carteam 协议用：dump 所有 carteam 相关包
				logCarteamPacket(data, "C2S", connID, clientIP, dstPort)
				// UUID 提取：443 端口 ACE 连接才有 UUID，按 clientIP 写入全局表
				// 17500 连接没有 UUID 载荷，但共享同 clientIP 可读到
				if actState.uuid == "" {
					if uuid := extractUUIDFromACE(data); uuid != "" {
						actState.mu.Lock()
						actState.uuid = uuid
						actState.mu.Unlock()
						s.uuidMu.Lock()
						prev := s.uuidByClient[clientIP]
						s.uuidByClient[clientIP] = uuid
						s.uuidMu.Unlock()
						if prev != uuid {
							logInfo("[UUID] clientIP=%s conn=%s dst=%s:%d UUID=%s",
								clientIP, connID, dstAddr, dstPort, uuid)
						}
					}
				}
				// [SIZE-LEARN C→S] 17500 客户端请求帧也按 UUID 打标签
				// 用户做动作时通常先发 C→S 请求（type+size 稳定），再收 S→C 响应
				// 小帧 >= 100 记（C→S 很多 type=0x1001/0x2001 小请求也要记）
				if dstPort == 17500 {
					s.uuidMu.Lock()
					uuidTag := s.uuidByClient[clientIP]
					s.uuidMu.Unlock()
					if uuidTag == "" {
						uuidTag = "?"
					}
					lp := 0
					for lp+21 <= len(data) {
						has, typ, total := parseFrame17500(data[lp:])
						if !has {
							break
						}
						if total >= 100 {
							logInfo("[SIZE-LEARN C→S] uuid=%s type=0x%04x size=%d",
								uuidTag, typ, total)
						}
						if total <= 0 || lp+total > len(data) {
							break
						}
						lp += total
					}
				}
				// 回滚：纯 1.fp 原版 —— 全程无过滤、无宽限期、无状态机
				// 只要经过 proxy 的所有流量都跑 Rule 1 + Rule 2
				// 这是 24 分钟实测不封号的配置，登录 race 靠"加载页开 FM"策略规避
				if !s.rule1Disabled && !s.rule2Disabled {
					if adv, changed := patchWPEAdvanced(data); changed {
						s.uuidMu.Lock()
						uidTag := s.uuidByClient[clientIP]
						s.uuidMu.Unlock()
						if uidTag == "" {
							uidTag = "?"
						}
						logInfo("[WPE-ADV C→S] 命中 len=%d dst=%s:%d uuid=%s",
							len(data), dstAddr, dstPort, uidTag)
						data = adv
						if s.capture != nil && connID != "" {
							s.capture.SavePacket(connID, copyBytes(data), "send_mod", seq)
						}
					}
				}
				if nw, werr := remote.Write(data); werr != nil {
					break
				} else {
					s.clientTracker.AddBytes(clientIP, 0, int64(nw))
				}
				seq++
			}
			if err != nil {
				break
			}
		}
	}()

	// S→C
	go func() {
		defer wg.Done()
		defer closeBoth()

		buf := make([]byte, 65536)
		seq := 0
		// 帧跟踪状态（per-connection）
		//   pendingBytes: 当前帧尚未读完的剩余字节数（>0 表示下一次 Read 是续包）
		//   dropping:     上一帧是否被 drop（续包也随之 drop）
		var pendingBytes int
		var dropping bool
		// 活动拦截（agent2 状态机 + 修正宽限期）
		//   实测发现：POST_LOGIN 后 0-3 秒到达的 10377/30761 等大帧 = 登录响应（绝对保留）
		//   POST_LOGIN + 3-15 秒 = 真正活动推送（drop）
		//   登录阶段 FSM 尚未进入 POST_LOGIN，完全不动
		const actGracePeriod = 3 * time.Second       // 登录响应宽限期
		const actWindowAfterLogin = 15 * time.Second // 活动窗口截止
		const actMinFrameSize = 8000
		for {
			n, err := remote.Read(buf)
			if n > 0 {
				data := buf[:n]
				if s.capture != nil && connID != "" {
					s.capture.SavePacket(connID, copyBytes(data), "recv", seq)
				}
				// 反向 carteam 协议：dump S→C 方向的队伍状态广播包
				logCarteamPacket(data, "S2C", connID, clientIP, dstPort)
				// [SIZE-LEARN] S→C 17500 大帧按 UUID 打 size 标签
				// 用途：用户做特定动作（开地图/活动/商店）时，可事后按 UUID 分组
				// 统计出每台模拟器独立的 size→动作映射表（避免跨台混淆）
				// 只记录 >= 1000 字节的帧（小帧是心跳，不区分动作）
				if dstPort == 17500 {
					s.uuidMu.Lock()
					uuidTag := s.uuidByClient[clientIP]
					s.uuidMu.Unlock()
					if uuidTag == "" {
						uuidTag = "?"
					}
					lp := 0
					for lp+21 <= len(data) {
						has, typ, total := parseFrame17500(data[lp:])
						if !has {
							break
						}
						if total >= 1000 {
							logInfo("[SIZE-LEARN] uuid=%s type=0x%04x size=%d",
								uuidTag, typ, total)
						}
						if total <= 0 {
							break
						}
						if lp+total > len(data) {
							// 续包，长度声明已记，继续跳出
							break
						}
						lp += total
					}
				}
				// R2 实验（2026-04-25）：17500 S→C 非 G6 大包 = 活动指纹
				// 6/6 标签交叉验证：活动 burst = 非 G6 + ~780KB/~5s burst
				// 登录 burst 更小（~370KB）且出现在 conn 存活 <8s 时
				// 所以 conn age > N 秒 + 非 G6 → 活动嫌疑
				if dstPort == 17500 && s.dropNonG617500AfterSec > 0 {
					age := time.Since(connStart)
					if age >= time.Duration(s.dropNonG617500AfterSec)*time.Second {
						// 非 G6 magic 检查
						if len(data) < 6 || data[0] != 0x33 || data[1] != 0x66 ||
							data[2] != 0x00 || data[3] != 0x0a ||
							data[4] != 0x00 || data[5] != 0x0a {
							if s.dropNonG617500Enforce {
								logInfo("[NON-G6-DROP] conn=%s len=%d age=%.1fs ENFORCE",
									connID, len(data), age.Seconds())
								seq++
								continue // 跳过 Write，包被吃掉
							} else {
								logInfo("[NON-G6-LOG] conn=%s len=%d age=%.1fs would-drop",
									connID, len(data), age.Seconds())
								// 仅日志，继续正常转发
							}
						}
					}
				}

				// 17500 精准帧跟踪：逐帧解析
				// v3 规则（跨会话稳定 size 指纹）：
				//   drop 条件 = type=0x1340 AND size 在 [15000, 16500]
				//   这个范围是"活动 banner 帧"独有（2 次会话实测 size=15609/15689）
				//   登录窗口大帧全部在此范围外（<15000 或 >20000），误伤概率极低
				if dstPort == 17500 && s.blockActivity {
					out := make([]byte, 0, len(data))
					pos := 0
					// 1) 消费上次未读完的续包字节
					if pendingBytes > 0 {
						take := pendingBytes
						if take > len(data) {
							take = len(data)
						}
						if dropping {
							logInfo("[ACT-DROP续] take=%d remaining=%d", take, pendingBytes-take)
						} else {
							out = append(out, data[:take]...)
						}
						pendingBytes -= take
						if pendingBytes == 0 {
							dropping = false
						}
						pos += take
					}
					// 2) 逐帧解析剩余部分
					for pos < len(data) {
						if len(data)-pos < 21 {
							out = append(out, data[pos:]...)
							pos = len(data)
							break
						}
						has, typ, total := parseFrame17500(data[pos:])
						if !has {
							// 没有 magic → 可能是上游 flush 的非控制帧数据，原样 forward
							out = append(out, data[pos:]...)
							pos = len(data)
							break
						}
						srvSeq := binary.LittleEndian.Uint32(data[pos+12 : pos+16])
						frameInBuf := total
						if pos+total > len(data) {
							frameInBuf = len(data) - pos
						}
						// 状态机判定：必须 POST_LOGIN 且在 [宽限期, 活动窗口结束] 之间
						actState.mu.Lock()
						elapsed := time.Since(actState.postLoginAt)
						inActWindow := actState.stage == stagePostLogin &&
							elapsed >= actGracePeriod && elapsed <= actWindowAfterLogin
						actState.mu.Unlock()
						shouldDropFrame := typ == 0x1340 && total >= actMinFrameSize && inActWindow
						if shouldDropFrame {
							logInfo("[ACT-DROP-FSM] srvSeq=0x%x size=%d thisPart=%d POST_LOGIN+%dms",
								srvSeq, total, frameInBuf, elapsed.Milliseconds())
							if frameInBuf < total {
								pendingBytes = total - frameInBuf
								dropping = true
							}
						} else {
							out = append(out, data[pos:pos+frameInBuf]...)
							if frameInBuf < total {
								pendingBytes = total - frameInBuf
								dropping = false
							}
						}
						pos += frameInBuf
					}
					// 如果 out 为空 → 整个 Read 都 drop，跳过 Write
					if len(out) == 0 {
						seq++
						continue
					}
					data = out
				}
				// Rule 2 S→C：17500 用 G6 帧 size-gated 精细化；443 ACE 仍用全量
				if !s.rule2Disabled {
					if dstPort == 17500 {
						// 1. 拦活动 v2: frame-level DROP activity channel (00 10 51/65 00)
						if s.dropActivityFrames {
							if d2, chd := applyActivityDropFrames(data); chd {
								logInfo("[ACT-DROP S→C] before=%d after=%d src=%s:%d",
									len(data), len(d2), dstAddr, dstPort)
								data = d2
							}
						}
						// 2. Rule 2 on remaining frames
						if adv, changed := applyRule2Sized(data, s.rule2MaxBody); changed {
							logInfo("[WPE-ADV S→C] 命中 len=%d src=%s:%d maxBody=%d",
								len(data), dstAddr, dstPort, s.rule2MaxBody)
							data = adv
							if s.capture != nil && connID != "" {
								s.capture.SavePacket(connID, copyBytes(data), "recv_mod", seq)
							}
						}
					} else if dstPort == 443 {
						// 优先用 fingerprint 精确匹配（默认开启），fallback 到旧版
						var adv []byte
						var changed bool
						if s.rule2Fingerprint {
							adv, changed = applyRule2Fingerprint(data)
						} else {
							adv, changed = patchWPEAdvanced(data)
						}
						if changed {
							mode := "fingerprint"
							if !s.rule2Fingerprint {
								mode = "legacy"
							}
							logInfo("[WPE-ADV S→C] 命中 len=%d src=%s:%d mode=%s", len(data), dstAddr, dstPort, mode)
							data = adv
						}
						// 然后再跑 activity break（独立动作，跟 Rule2 不冲突）
						if s.blockActivityFingerprint {
							if act, actChanged := applyActivityBreak(data); actChanged {
								logInfo("[ACT-BREAK S→C] 命中 len=%d src=%s:%d", len(data), dstAddr, dstPort)
								data = act
								changed = true
							}
						}
						if changed && s.capture != nil && connID != "" {
							s.capture.SavePacket(connID, copyBytes(data), "recv_mod", seq)
						}
					}
				}
				if nw, werr := client.Write(data); werr != nil {
					break
				} else {
					s.clientTracker.AddBytes(clientIP, int64(nw), 0)
				}
				seq++
			}
			if err != nil {
				break
			}
		}
	}()

	wg.Wait()
}

// relayPassthrough 纯透传
func (s *Socks5Server) relayPassthrough(client, remote net.Conn) {
	var wg sync.WaitGroup
	wg.Add(2)

	closeOnce := sync.Once{}
	closeBoth := func() {
		closeOnce.Do(func() {
			client.Close()
			remote.Close()
		})
	}

	go func() {
		defer wg.Done()
		defer closeBoth()
		io.Copy(remote, client)
	}()
	go func() {
		defer wg.Done()
		defer closeBoth()
		io.Copy(client, remote)
	}()
	wg.Wait()
}

func copyBytes(data []byte) []byte {
	c := make([]byte, len(data))
	copy(c, data)
	return c
}

func hexHead(data []byte, n int) string {
	if len(data) < n {
		n = len(data)
	}
	return hex.EncodeToString(data[:n])
}
