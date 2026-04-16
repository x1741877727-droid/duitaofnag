package main

import (
	"sync"
	"time"
)

type failEntry struct {
	count    int
	lastFail time.Time
}

// FailCache 连接失败缓存，用于抑制重复日志
type FailCache struct {
	mu      sync.Mutex
	entries map[string]*failEntry
}

// NewFailCache 创建失败缓存
func NewFailCache() *FailCache {
	return &FailCache{entries: make(map[string]*failEntry)}
}

// RecordFailure 记录失败，返回累计次数。60 秒后自动重置
func (fc *FailCache) RecordFailure(key string) int {
	fc.mu.Lock()
	defer fc.mu.Unlock()

	e, ok := fc.entries[key]
	if !ok {
		fc.entries[key] = &failEntry{count: 1, lastFail: time.Now()}
		return 1
	}
	if time.Since(e.lastFail) >= 60*time.Second {
		e.count = 0
	}
	e.count++
	e.lastFail = time.Now()
	return e.count
}

// Clear 连接成功时清除记录
func (fc *FailCache) Clear(key string) {
	fc.mu.Lock()
	defer fc.mu.Unlock()
	delete(fc.entries, key)
}
