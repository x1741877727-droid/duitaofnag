package main

// Packet viewer label API (Phase A MVP)
//
// POST /api/label       写一条 JSONL 标签
// GET  /api/labels      读全部（可按 conn / since 过滤），支持 ETag
//
// 存储：captureDir/labels.jsonl
//   {"id":"lbl_XXX","ts":1713990000.123,"author":"user","tag":"活动",
//    "anchor":{"conn":"conn_000042","dir":"s2c","seq":77},"note":"弹了"}

import (
	"bufio"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

const labelsFilename = "labels.jsonl"

var labelsMu sync.Mutex

// LabelAnchor 某个 frame 的引用
type LabelAnchor struct {
	Conn string `json:"conn"`
	Dir  string `json:"dir"`
	Seq  int    `json:"seq"`
}

// LabelRange 一段时间范围（跨 frame）
type LabelRange struct {
	TStart float64  `json:"t_start"`
	TEnd   float64  `json:"t_end"`
	Conns  []string `json:"conns,omitempty"`
}

// Label 一条标签
//   - ClientIP 存在 → 表示"这条标签属于某个游戏实例"（跨所有端口）
//   - Anchor 存在 → 同时精确钉到某 conn 的 (dir, seq)
// 两者可并存：客户端按按钮时用实例 IP 作广覆盖，如果当前选中了某 frame 则顺便 anchor
type Label struct {
	ID        string       `json:"id"`
	TS        float64      `json:"ts"`
	Author    string       `json:"author"` // "user" | "claude"
	Tag       string       `json:"tag"`
	Note      string       `json:"note,omitempty"`
	ClientIP  string       `json:"client_ip,omitempty"`
	Anchor    *LabelAnchor `json:"anchor,omitempty"`
	Range     *LabelRange  `json:"range,omitempty"`
}

// newLabelID 生成 "lbl_XXXXXXXXXXXXXXXX" 形式 ID
func newLabelID() string {
	var b [8]byte
	_, _ = rand.Read(b[:])
	return "lbl_" + hex.EncodeToString(b[:])
}

// labelsFilePath 算出 labels.jsonl 的完整路径
func labelsFilePath(captureDir string) string {
	return filepath.Join(captureDir, labelsFilename)
}

// appendLabel 以 O_APPEND 一次写入一行 JSON，末尾 '\n'
// POSIX 保证 <PIPE_BUF (一般 4KB) 的 write 是原子的；一条 label 远小于该值
func appendLabel(captureDir string, lbl *Label) error {
	path := labelsFilePath(captureDir)
	data, err := json.Marshal(lbl)
	if err != nil {
		return err
	}
	data = append(data, '\n')

	labelsMu.Lock()
	defer labelsMu.Unlock()
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = f.Write(data)
	return err
}

// readAllLabels 扫整个 labels.jsonl，逐行解码
func readAllLabels(captureDir string) ([]Label, string, error) {
	path := labelsFilePath(captureDir)
	info, err := os.Stat(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, "\"empty\"", nil
		}
		return nil, "", err
	}
	// ETag = size + mtime（mtime 换成纳秒防抖）
	etag := fmt.Sprintf("\"%d-%d\"", info.Size(), info.ModTime().UnixNano())

	f, err := os.Open(path)
	if err != nil {
		return nil, etag, err
	}
	defer f.Close()

	var out []Label
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<16), 1<<20)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		var lbl Label
		if err := json.Unmarshal([]byte(line), &lbl); err != nil {
			continue
		}
		out = append(out, lbl)
	}
	return out, etag, sc.Err()
}

// ---- HTTP handlers ----

// handleLabelPost POST /api/label
//
// Body: { tag, author?, note?, anchor?, range? }
// Returns: {ok:true, label: {...}}
func handleLabelPost(getCaptureDir func() string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			// CORS preflight
			if r.Method == http.MethodOptions {
				w.Header().Set("Access-Control-Allow-Origin", "*")
				w.Header().Set("Access-Control-Allow-Methods", "POST, OPTIONS")
				w.Header().Set("Access-Control-Allow-Headers", "Content-Type, If-None-Match")
				w.WriteHeader(204)
				return
			}
			writeErr(w, 405, "POST only")
			return
		}
		dir := getCaptureDir()
		if dir == "" {
			writeErr(w, 503, "capture disabled")
			return
		}

		var body Label
		dec := json.NewDecoder(r.Body)
		dec.DisallowUnknownFields()
		if err := dec.Decode(&body); err != nil {
			writeErr(w, 400, "invalid json: "+err.Error())
			return
		}
		if strings.TrimSpace(body.Tag) == "" {
			writeErr(w, 400, "tag required")
			return
		}
		// 补齐默认字段
		if body.ID == "" {
			body.ID = newLabelID()
		}
		if body.TS == 0 {
			body.TS = float64(time.Now().UnixNano()) / 1e9
		}
		if body.Author == "" {
			body.Author = "user"
		}
		if body.Author != "user" && body.Author != "claude" {
			writeErr(w, 400, "author must be 'user' or 'claude'")
			return
		}
		if err := appendLabel(dir, &body); err != nil {
			writeErr(w, 500, "append failed: "+err.Error())
			return
		}
		writeJSON(w, 200, map[string]interface{}{"ok": true, "label": body})
	}
}

// handleLabelsGet GET /api/labels[?conn=X][&since=T][&tag=Y]
// 支持 If-None-Match → 304
func handleLabelsGet(getCaptureDir func() string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		dir := getCaptureDir()
		if dir == "" {
			writeErr(w, 503, "capture disabled")
			return
		}
		all, etag, err := readAllLabels(dir)
		if err != nil {
			writeErr(w, 500, err.Error())
			return
		}
		w.Header().Set("ETag", etag)
		if inm := r.Header.Get("If-None-Match"); inm != "" && inm == etag {
			w.Header().Set("Access-Control-Allow-Origin", "*")
			w.WriteHeader(304)
			return
		}

		// 过滤
		connFilter := r.URL.Query().Get("conn")
		tagFilter := r.URL.Query().Get("tag")
		clientIPFilter := r.URL.Query().Get("client_ip")
		sinceStr := r.URL.Query().Get("since")
		var since float64
		if sinceStr != "" {
			if v, err := strconv.ParseFloat(sinceStr, 64); err == nil {
				since = v
			}
		}
		out := make([]Label, 0, len(all))
		for _, l := range all {
			if since > 0 && l.TS < since {
				continue
			}
			if connFilter != "" {
				ok := l.Anchor != nil && l.Anchor.Conn == connFilter
				if !ok && l.Range != nil {
					for _, c := range l.Range.Conns {
						if c == connFilter {
							ok = true
							break
						}
					}
				}
				if !ok {
					continue
				}
			}
			if clientIPFilter != "" && l.ClientIP != clientIPFilter {
				continue
			}
			if tagFilter != "" && l.Tag != tagFilter {
				continue
			}
			out = append(out, l)
		}
		writeJSON(w, 200, map[string]interface{}{
			"ok": true, "count": len(out), "labels": out, "etag": etag,
		})
	}
}
