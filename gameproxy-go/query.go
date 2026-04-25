package main

// Packet viewer REST API (Phase A MVP)
//
// /api/conns                                返回 conn 列表 + 元数据
// /api/timeline?conn=X[&limit=N&offset=M]   返回单 conn 的轻量 frame 行（仅 G6 header 解析）
// /api/frame?conn=X&dir=c2s|s2c&seq=N       返回单 frame 完整 hex + 解析 + mod diff
//
// 数据源：capture.CaptureDir() 下的 conn_NNNNNN/ 目录结构：
//   meta.json
//   c2s_SSSSSS.bin + c2s_SSSSSS.json         原始封包
//   s2c_SSSSSS.bin + s2c_SSSSSS.json
//   c2s_mod_SSSSSS.bin + c2s_mod_SSSSSS.json 改写后（若规则命中）
//   s2c_mod_SSSSSS.bin + s2c_mod_SSSSSS.json
//
// 说明：Phase A 用 per-TCP-packet 粒度的行展示；单个 TCP 包内部的 G6 多帧在
// /api/frame 里展开（fragments 数组）。Timeline 一行 = 一个 TCP 包 = 一个 .bin 文件。

import (
	"encoding/hex"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
)

// 文件里已经有的 G6 magic 常量在 relay.go 叫不同名字，这里本地定义避免冲突
var g6Magic = []byte{0x33, 0x66, 0x00, 0x0a, 0x00, 0x0a}

// ---- JSON schemas ----

type connMetaFile struct {
	ConnID    string  `json:"conn_id"`
	DstAddr   string  `json:"dst_addr"`
	DstPort   int     `json:"dst_port"`
	ClientIP  string  `json:"client_ip,omitempty"`
	StartTime float64 `json:"start_time"`
	StartISO  string  `json:"start_iso"`
}

type pktMetaFile struct {
	PktNum    int     `json:"pkt_num"`
	Direction string  `json:"direction"`
	Sequence  int     `json:"sequence"`
	Length    int     `json:"length"`
	Timestamp float64 `json:"timestamp"`
	HexHead64 string  `json:"hex_head_64"`
}

// ConnRef 对外返回的 conn 元数据
type ConnRef struct {
	ConnID    string  `json:"conn_id"`
	DstAddr   string  `json:"dst_addr"`
	DstPort   int     `json:"dst_port"`
	ClientIP  string  `json:"client_ip,omitempty"`
	StartTime float64 `json:"start_time"`
	PktCount  int     `json:"pkt_count"`
	C2S       int     `json:"c2s"`
	S2C       int     `json:"s2c"`
	ModCount  int     `json:"mod_count"`
}

// FrameRow 对外返回的 timeline 行（一行 = 一个 TCP packet）
type FrameRow struct {
	Conn    string  `json:"conn"`
	Seq     int     `json:"seq"`
	Dir     string  `json:"dir"`
	T       float64 `json:"t"`    // 距 conn 起始时间（毫秒）
	TS      float64 `json:"ts"`   // 绝对 unix sec（方便 Claude 按真实时间查）
	Len     int     `json:"len"`
	TypeHex string  `json:"typ,omitempty"`  // G6 type LE u16 hex，如 "4013"
	Chan    string  `json:"ch,omitempty"`   // channel byte hex，如 "51"
	BodyLen int     `json:"body_len,omitempty"`
	DirByte int     `json:"dir_byte,omitempty"`
	Frames  int     `json:"frames,omitempty"` // TCP 包里 G6 frame 数
	Flags   int     `json:"flags"`            // bit 16 = has_mod
}

// FrameDetail 对外返回的单帧详情
type FrameDetail struct {
	Conn      string  `json:"conn"`
	Seq       int     `json:"seq"`
	Dir       string  `json:"dir"`
	Timestamp float64 `json:"timestamp"`
	Length    int     `json:"length"`
	HexFull   string  `json:"hex_full"`
	ModHex    string  `json:"mod_hex_full,omitempty"`
	Parsed    *FrameParsed       `json:"parsed,omitempty"`
	Fragments []FrameFragment    `json:"fragments,omitempty"`
	RuleHits  []FrameRuleHit     `json:"rule_hits"`
}

type FrameParsed struct {
	MagicOK    bool   `json:"magic_ok"`
	TypeHex    string `json:"type"`        // LE u16 hex
	DirByte    int    `json:"dir_byte"`
	Seq1B      int    `json:"srv_seq"`     // byte[12]，1-byte cyclic
	PayloadLen int    `json:"payload_len"`
	ChannelHex string `json:"channel_hex"` // byte[23]
	BodyHead32 string `json:"body_head_32"`
}

type FrameFragment struct {
	Offset     int    `json:"offset"`
	Size       int    `json:"size"`
	TypeHex    string `json:"type"`
	DirByte    int    `json:"dir_byte"`
	Seq1B      int    `json:"seq1b"`
	PayloadLen int    `json:"payload_len"`
	ChannelHex string `json:"channel_hex"`
}

type FrameRuleHit struct {
	Rule   string `json:"rule"`
	Offset int    `json:"offset"`
	Note   string `json:"note"`
}

// ---- helpers ----

// readPktMeta 读并解码一个 packet 的 .json sidecar
func readPktMeta(path string) (*pktMetaFile, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var m pktMetaFile
	if err := json.Unmarshal(data, &m); err != nil {
		return nil, err
	}
	return &m, nil
}

// readConnMeta 读并解码一个 conn 的 meta.json
func readConnMeta(path string) (*connMetaFile, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var m connMetaFile
	if err := json.Unmarshal(data, &m); err != nil {
		return nil, err
	}
	return &m, nil
}

// parseG6Frames 扫描一个 TCP data 里所有 G6 frame 的 header 字段
// 不抛异常：对不合法字节回退到"no-g6"行为。
func parseG6Frames(data []byte) []FrameFragment {
	var frags []FrameFragment
	pos := 0
	for pos+25 <= len(data) {
		if !bytesEqual(data[pos:pos+6], g6Magic) {
			break
		}
		typeHex := hex.EncodeToString(data[pos+6 : pos+8])
		dirByte := int(data[pos+8])
		seq1B := int(data[pos+12])
		payLen := int(data[pos+19])<<8 | int(data[pos+20])
		channel := hex.EncodeToString(data[pos+23 : pos+24])
		total := payLen + 25
		if pos+total > len(data) {
			// 尾部切片（应用层分片切到下一个 TCP 包），记录部分信息后跳出
			frags = append(frags, FrameFragment{
				Offset: pos, Size: len(data) - pos,
				TypeHex: typeHex, DirByte: dirByte, Seq1B: seq1B,
				PayloadLen: payLen, ChannelHex: channel,
			})
			break
		}
		frags = append(frags, FrameFragment{
			Offset: pos, Size: total,
			TypeHex: typeHex, DirByte: dirByte, Seq1B: seq1B,
			PayloadLen: payLen, ChannelHex: channel,
		})
		pos += total
	}
	return frags
}

func bytesEqual(a, b []byte) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

// listConns 扫 captureDir 列出所有 conn_xxxxxx 目录
func listConns(captureDir string) ([]ConnRef, error) {
	entries, err := os.ReadDir(captureDir)
	if err != nil {
		return nil, err
	}
	var out []ConnRef
	for _, e := range entries {
		if !e.IsDir() || !strings.HasPrefix(e.Name(), "conn_") {
			continue
		}
		connDir := filepath.Join(captureDir, e.Name())
		meta, err := readConnMeta(filepath.Join(connDir, "meta.json"))
		if err != nil || meta == nil {
			continue
		}

		var c2s, s2c, mods int
		files, _ := os.ReadDir(connDir)
		for _, f := range files {
			name := f.Name()
			if !strings.HasSuffix(name, ".bin") {
				continue
			}
			switch {
			case strings.HasPrefix(name, "c2s_mod_"):
				mods++
			case strings.HasPrefix(name, "s2c_mod_"):
				mods++
			case strings.HasPrefix(name, "c2s_"):
				c2s++
			case strings.HasPrefix(name, "s2c_"):
				s2c++
			}
		}
		out = append(out, ConnRef{
			ConnID:    meta.ConnID,
			DstAddr:   meta.DstAddr,
			DstPort:   meta.DstPort,
			ClientIP:  meta.ClientIP,
			StartTime: meta.StartTime,
			PktCount:  c2s + s2c,
			C2S:       c2s,
			S2C:       s2c,
			ModCount:  mods,
		})
	}
	// 按 start_time 降序（最新的 conn 在前）
	sort.Slice(out, func(i, j int) bool { return out[i].StartTime > out[j].StartTime })
	return out, nil
}

// loadTimeline 扫单个 conn 的所有 .json sidecar 构造 FrameRow 切片（按时间升序）
func loadTimeline(captureDir, connID string) ([]FrameRow, error) {
	connDir := filepath.Join(captureDir, connID)
	meta, err := readConnMeta(filepath.Join(connDir, "meta.json"))
	if err != nil {
		return nil, err
	}

	entries, err := os.ReadDir(connDir)
	if err != nil {
		return nil, err
	}

	// 先扫 _mod.json 集合，便于打 has_mod 标志
	modSet := map[string]bool{} // key = "c2s|42" / "s2c|42"
	for _, e := range entries {
		name := e.Name()
		if !strings.HasSuffix(name, ".json") {
			continue
		}
		if strings.HasPrefix(name, "c2s_mod_") || strings.HasPrefix(name, "s2c_mod_") {
			// c2s_mod_000042.json → c2s|42
			parts := strings.Split(strings.TrimSuffix(name, ".json"), "_")
			if len(parts) < 3 {
				continue
			}
			seq, err := strconv.Atoi(parts[2])
			if err != nil {
				continue
			}
			modSet[parts[0]+"|"+strconv.Itoa(seq)] = true
		}
	}

	var rows []FrameRow
	for _, e := range entries {
		name := e.Name()
		if !strings.HasSuffix(name, ".json") {
			continue
		}
		if !(strings.HasPrefix(name, "c2s_") || strings.HasPrefix(name, "s2c_")) {
			continue
		}
		// 跳过 _mod.json
		if strings.HasPrefix(name, "c2s_mod_") || strings.HasPrefix(name, "s2c_mod_") {
			continue
		}
		pkt, err := readPktMeta(filepath.Join(connDir, name))
		if err != nil || pkt == nil {
			continue
		}
		dir := "c2s"
		if strings.HasPrefix(name, "s2c_") {
			dir = "s2c"
		}

		// parse G6 from hex_head_64 (cheap, no full-file read)
		head, _ := hex.DecodeString(pkt.HexHead64)
		frags := parseG6Frames(head)

		row := FrameRow{
			Conn: connID, Seq: pkt.Sequence, Dir: dir,
			T:  (pkt.Timestamp - meta.StartTime) * 1000.0,
			TS: pkt.Timestamp,
			Len: pkt.Length,
		}
		if len(frags) > 0 {
			f0 := frags[0]
			row.TypeHex = f0.TypeHex
			row.Chan = f0.ChannelHex
			row.BodyLen = f0.PayloadLen
			row.DirByte = f0.DirByte
			// Frames 启发式：G6 应用层分片是 1081B/帧（实证），用 TCP 包总长推算；
			// hex_head_64 只有 64B 看不到后续帧，但 len 是准确的
			row.Frames = (pkt.Length + 1080) / 1081
			if row.Frames < 1 {
				row.Frames = 1
			}
		}
		if modSet[dir+"|"+strconv.Itoa(pkt.Sequence)] {
			row.Flags |= 16
		}
		rows = append(rows, row)
	}
	sort.Slice(rows, func(i, j int) bool {
		if rows[i].TS != rows[j].TS {
			return rows[i].TS < rows[j].TS
		}
		return rows[i].Seq < rows[j].Seq
	})
	return rows, nil
}

// loadFrame 读单个封包原始 bytes + mod（若有），构造 FrameDetail
func loadFrame(captureDir, connID, dir string, seq int) (*FrameDetail, error) {
	connDir := filepath.Join(captureDir, connID)
	base := dir + "_" + pad6(seq)
	jsonPath := filepath.Join(connDir, base+".json")
	binPath := filepath.Join(connDir, base+".bin")

	pkt, err := readPktMeta(jsonPath)
	if err != nil {
		return nil, err
	}
	raw, err := os.ReadFile(binPath)
	if err != nil {
		return nil, err
	}

	det := &FrameDetail{
		Conn: connID, Seq: seq, Dir: dir,
		Timestamp: pkt.Timestamp, Length: len(raw),
		HexFull:  hex.EncodeToString(raw),
		RuleHits: []FrameRuleHit{},
	}

	// G6 parse (整包)
	frags := parseG6Frames(raw)
	if len(frags) > 0 {
		f0 := frags[0]
		body32 := ""
		bodyStart := f0.Offset + 25
		if bodyStart+32 <= len(raw) {
			body32 = hex.EncodeToString(raw[bodyStart : bodyStart+32])
		} else if bodyStart <= len(raw) {
			body32 = hex.EncodeToString(raw[bodyStart:])
		}
		det.Parsed = &FrameParsed{
			MagicOK: true, TypeHex: f0.TypeHex, DirByte: f0.DirByte,
			Seq1B:   f0.Seq1B, PayloadLen: f0.PayloadLen,
			ChannelHex: f0.ChannelHex, BodyHead32: body32,
		}
		det.Fragments = frags
	}

	// mod diff
	modBase := strings.Replace(dir, "c2s", "c2s_mod", 1)
	modBase = strings.Replace(modBase, "s2c", "s2c_mod", 1)
	// 上两行会把 "c2s" 变成 "c2s_mod"；"s2c" 已被先一步替换到 "s2c_mod" — 先判方向再组合更稳：
	if dir == "c2s" {
		modBase = "c2s_mod_" + pad6(seq)
	} else {
		modBase = "s2c_mod_" + pad6(seq)
	}
	modBin, err := os.ReadFile(filepath.Join(connDir, modBase+".bin"))
	if err == nil {
		det.ModHex = hex.EncodeToString(modBin)
		diffs := diffHexPositions(raw, modBin)
		for _, off := range diffs {
			det.RuleHits = append(det.RuleHits, FrameRuleHit{
				Rule:   "mod-byte-diff",
				Offset: off,
				Note:   byteDiffNote(raw, modBin, off),
			})
		}
	}

	return det, nil
}

func pad6(n int) string {
	s := strconv.Itoa(n)
	for len(s) < 6 {
		s = "0" + s
	}
	return s
}

// diffHexPositions 列出 a vs b 逐字节差异的 offset（至多 32 个）
func diffHexPositions(a, b []byte) []int {
	var out []int
	n := len(a)
	if len(b) < n {
		n = len(b)
	}
	for i := 0; i < n; i++ {
		if a[i] != b[i] {
			out = append(out, i)
			if len(out) >= 32 {
				break
			}
		}
	}
	return out
}

func byteDiffNote(a, b []byte, i int) string {
	if i < 0 || i >= len(a) || i >= len(b) {
		return ""
	}
	av, bv := a[i], b[i]
	return "0x" + toHex2(av) + " -> 0x" + toHex2(bv)
}

func toHex2(b byte) string {
	const digits = "0123456789abcdef"
	return string([]byte{digits[b>>4], digits[b&0x0f]})
}

// ---- HTTP handlers ----

func writeJSON(w http.ResponseWriter, status int, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func writeErr(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]interface{}{"ok": false, "error": msg})
}

// handleConns GET /api/conns — 扫 captureDir 返回 conn 列表
func handleConns(getCaptureDir func() string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		dir := getCaptureDir()
		if dir == "" {
			writeErr(w, 503, "capture disabled")
			return
		}
		conns, err := listConns(dir)
		if err != nil {
			writeErr(w, 500, err.Error())
			return
		}
		// 可选筛选：port=443,17500
		if portParam := r.URL.Query().Get("port"); portParam != "" {
			wanted := map[int]bool{}
			for _, p := range strings.Split(portParam, ",") {
				if n, err := strconv.Atoi(strings.TrimSpace(p)); err == nil {
					wanted[n] = true
				}
			}
			filtered := conns[:0]
			for _, c := range conns {
				if wanted[c.DstPort] {
					filtered = append(filtered, c)
				}
			}
			conns = filtered
		}
		// 可选筛选：client_ip=1.2.3.4
		if cip := r.URL.Query().Get("client_ip"); cip != "" {
			filtered := conns[:0]
			for _, c := range conns {
				if c.ClientIP == cip {
					filtered = append(filtered, c)
				}
			}
			conns = filtered
		}
		writeJSON(w, 200, map[string]interface{}{"ok": true, "conns": conns})
	}
}

// handleTimeline GET /api/timeline?conn=X
func handleTimeline(getCaptureDir func() string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		dir := getCaptureDir()
		if dir == "" {
			writeErr(w, 503, "capture disabled")
			return
		}
		conn := r.URL.Query().Get("conn")
		if conn == "" {
			writeErr(w, 400, "missing conn param")
			return
		}
		rows, err := loadTimeline(dir, conn)
		if err != nil {
			writeErr(w, 404, err.Error())
			return
		}
		// 可选分页
		offset, _ := strconv.Atoi(r.URL.Query().Get("offset"))
		limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
		total := len(rows)
		if offset > total {
			offset = total
		}
		if offset < 0 {
			offset = 0
		}
		end := total
		if limit > 0 && offset+limit < total {
			end = offset + limit
		}
		writeJSON(w, 200, map[string]interface{}{
			"ok": true, "conn": conn, "total": total,
			"offset": offset, "limit": end - offset,
			"rows": rows[offset:end],
		})
	}
}

// handleFrame GET /api/frame?conn=X&dir=Y&seq=N
func handleFrame(getCaptureDir func() string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		dir := getCaptureDir()
		if dir == "" {
			writeErr(w, 503, "capture disabled")
			return
		}
		conn := r.URL.Query().Get("conn")
		direction := r.URL.Query().Get("dir")
		seqStr := r.URL.Query().Get("seq")
		if conn == "" || seqStr == "" {
			writeErr(w, 400, "missing conn or seq param")
			return
		}
		if direction != "c2s" && direction != "s2c" {
			writeErr(w, 400, "dir must be c2s or s2c")
			return
		}
		seq, err := strconv.Atoi(seqStr)
		if err != nil {
			writeErr(w, 400, "seq not int")
			return
		}
		det, err := loadFrame(dir, conn, direction, seq)
		if err != nil {
			writeErr(w, 404, err.Error())
			return
		}
		writeJSON(w, 200, map[string]interface{}{"ok": true, "frame": det})
	}
}
