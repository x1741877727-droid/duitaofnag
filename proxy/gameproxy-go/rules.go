package main

import (
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"sync"
)

// SearchModifyEntry 一个 pos:val 对
type SearchModifyEntry struct {
	Pos int `json:"pos"`
	Val int `json:"val"`
}

// PacketRule 一条封包改写规则
type PacketRule struct {
	Name      string             `json:"name"`
	Enabled   bool               `json:"enabled"`
	Search    []SearchModifyEntry `json:"search"`
	Modify    []SearchModifyEntry `json:"modify"`
	Header    string             `json:"header"`
	LengthMin int                `json:"length_min"`
	LengthMax int                `json:"length_max"`
	Action    string             `json:"action"`

	headerBytes []byte // 从 Header hex 解析
}

// Matches 检查封包是否匹配此规则
func (r *PacketRule) Matches(data []byte) bool {
	if !r.Enabled {
		return false
	}
	if r.LengthMin > 0 && len(data) < r.LengthMin {
		return false
	}
	if r.LengthMax > 0 && len(data) > r.LengthMax {
		return false
	}

	offset := 0
	if r.headerBytes != nil {
		if len(data) < len(r.headerBytes) {
			return false
		}
		for i, b := range r.headerBytes {
			if data[i] != b {
				return false
			}
		}
		offset = len(r.headerBytes)
	}

	for _, s := range r.Search {
		pos := s.Pos + offset
		if pos >= len(data) {
			return false
		}
		if data[pos] != byte(s.Val) {
			return false
		}
	}
	return true
}

// MatchDebug 返回 (matched, reason) 用于诊断
func (r *PacketRule) MatchDebug(data []byte) (bool, string) {
	if !r.Enabled {
		return false, "disabled"
	}
	if r.LengthMin > 0 && len(data) < r.LengthMin {
		return false, fmt.Sprintf("too_short(%d<%d)", len(data), r.LengthMin)
	}
	if r.LengthMax > 0 && len(data) > r.LengthMax {
		return false, fmt.Sprintf("too_long(%d>%d)", len(data), r.LengthMax)
	}

	offset := 0
	if r.headerBytes != nil {
		if len(data) < len(r.headerBytes) {
			return false, fmt.Sprintf("header_short(%d<%d)", len(data), len(r.headerBytes))
		}
		for i, b := range r.headerBytes {
			if data[i] != b {
				actual := hex.EncodeToString(data[:len(r.headerBytes)])
				expected := hex.EncodeToString(r.headerBytes)
				return false, fmt.Sprintf("header_mismatch(got=%s,want=%s)", actual, expected)
			}
		}
		offset = len(r.headerBytes)
	}

	for _, s := range r.Search {
		pos := s.Pos + offset
		if pos >= len(data) {
			return false, fmt.Sprintf("pos%d_oob(need>%d,have=%d)", s.Pos, pos, len(data))
		}
		if data[pos] != byte(s.Val) {
			return false, fmt.Sprintf("pos%d_mismatch(got=0x%02x,want=0x%02x,abs=%d)",
				s.Pos, data[pos], byte(s.Val), pos)
		}
	}
	return true, "matched"
}

// Apply 应用修改，返回修改后的数据
func (r *PacketRule) Apply(data []byte) []byte {
	offset := 0
	if r.headerBytes != nil {
		offset = len(r.headerBytes)
	}

	if r.Action == "change" {
		// 计算最大位置
		maxPos := 0
		for _, m := range r.Modify {
			p := m.Pos + offset
			if p > maxPos {
				maxPos = p
			}
		}
		size := len(data)
		if maxPos+1 > size {
			size = maxPos + 1
		}
		buf := make([]byte, size)
		copy(buf, data)
		for _, m := range r.Modify {
			p := m.Pos + offset
			if p < len(buf) {
				buf[p] = byte(m.Val)
			}
		}
		return buf[:maxPos+1]
	}

	// replace: 只修改指定位置
	buf := make([]byte, len(data))
	copy(buf, data)
	for _, m := range r.Modify {
		p := m.Pos + offset
		if p < len(buf) {
			buf[p] = byte(m.Val)
		}
	}
	return buf
}

// RuleStats 规则引擎统计快照
type RuleStats struct {
	TotalPackets    int64
	ModifiedPackets int64
	MatchedRules    map[string]int64
}

// RuleEngine 封包改写规则引擎
type RuleEngine struct {
	rules     []PacketRule
	dryRun    bool
	ruleDebug bool

	mu              sync.Mutex
	totalPackets    int64
	modifiedPackets int64
	matchedRules    map[string]int64
}

// NewRuleEngine 创建规则引擎
func NewRuleEngine(dryRun, ruleDebug bool) *RuleEngine {
	return &RuleEngine{
		dryRun:       dryRun,
		ruleDebug:    ruleDebug,
		matchedRules: make(map[string]int64),
	}
}

// LoadFromJSON 从 JSON 文件加载规则
func (e *RuleEngine) LoadFromJSON(path string) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	var rf struct {
		Rules []PacketRule `json:"rules"`
	}
	if err := json.Unmarshal(data, &rf); err != nil {
		return err
	}

	e.rules = rf.Rules
	for i := range e.rules {
		r := &e.rules[i]
		if r.Header != "" {
			b, err := hex.DecodeString(r.Header)
			if err != nil {
				return fmt.Errorf("规则 [%s] header hex 解析失败: %v", r.Name, err)
			}
			r.headerBytes = b
		}
		if r.Action == "" {
			r.Action = "replace"
		}
	}

	logInfo("加载 %d 条规则", len(e.rules))
	return nil
}

// Process 处理一个封包，返回（可能修改后的）数据
func (e *RuleEngine) Process(data []byte, direction string) []byte {
	e.mu.Lock()
	e.totalPackets++
	pktNum := e.totalPackets
	e.mu.Unlock()

	// 记录封包样本（前 64 字节 hex）
	if len(data) >= 4 && pktNum <= 500 {
		headLen := 64
		if len(data) < headLen {
			headLen = len(data)
		}
		headerHex := hex.EncodeToString(data[:headLen])
		logDebug("封包 (%s) #%d len=%d: %s", direction, pktNum, len(data), headerHex)
		if pktNum%20 == 1 {
			logInfo("封包样本 (%s) len=%d: %s", direction, len(data), headerHex)
		}
	}

	for i := range e.rules {
		rule := &e.rules[i]

		if e.ruleDebug {
			matched, reason := rule.MatchDebug(data)
			if matched {
				logInfo("[RULE-DEBUG] [%s] MATCHED (%s) len=%d head=%s",
					rule.Name, direction, len(data), hex.EncodeToString(data[:min(16, len(data))]))
			} else if pktNum <= 200 {
				logDebug("[RULE-DEBUG] [%s] skip: %s (%s) len=%d",
					rule.Name, reason, direction, len(data))
			}
			if !matched {
				continue
			}
		} else {
			if !rule.Matches(data) {
				continue
			}
		}

		e.mu.Lock()
		e.matchedRules[rule.Name]++
		e.mu.Unlock()

		if e.dryRun {
			logInfo("[DRY-RUN] 规则 [%s] 命中 (%s), len=%d, head=%s",
				rule.Name, direction, len(data), hex.EncodeToString(data[:min(16, len(data))]))
			return data
		}

		modified := rule.Apply(data)
		if !bytesEqual(data, modified) {
			e.mu.Lock()
			e.modifiedPackets++
			e.mu.Unlock()
			logInfo("规则 [%s] 命中并修改 (%s), len=%d→%d, head=%s",
				rule.Name, direction, len(data), len(modified),
				hex.EncodeToString(data[:min(16, len(data))]))
			return modified
		}
		return data
	}
	return data
}

// Stats 返回统计快照
func (e *RuleEngine) Stats() RuleStats {
	e.mu.Lock()
	defer e.mu.Unlock()
	m := make(map[string]int64)
	for k, v := range e.matchedRules {
		m[k] = v
	}
	return RuleStats{
		TotalPackets:    e.totalPackets,
		ModifiedPackets: e.modifiedPackets,
		MatchedRules:    m,
	}
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

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
