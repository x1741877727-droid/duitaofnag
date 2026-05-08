/**
 * DataShell — 数据页 chrome.
 * 来源 /tmp/design_dl/gameautomation/project/data-shell.jsx
 *
 * 注: 设计原型 DataShell 包含一个完整的顶部 header (logo + 全局 tabs 中控台/数据/设置 +
 * range + 导出). 在我们的工程里, 全局 nav 已经在 [Header.tsx](../header/Header.tsx) 实现,
 * 所以这里只渲染 sub-header (range/export) + sub-tabs (档案/记忆库) + body, 避免双重导航.
 */

import { useEffect, useState } from 'react'
import { C } from '@/lib/design-tokens'
import { ArchiveView } from './archive/ArchiveView'
import { MemoryView } from './memory/MemoryView'
import { fetchPendingList } from '@/lib/memoryApi'

type SubTab = 'archive' | 'memory'

function SubTabs({
  active,
  onChange,
  pendingCount,
}: {
  active: SubTab
  onChange: (k: SubTab) => void
  pendingCount: number
}) {
  const items: Array<[SubTab, string, number | null]> = [
    ['archive', '档案', null],
    ['memory', '记忆库', pendingCount > 0 ? pendingCount : null],
  ]
  return (
    <div
      style={{
        display: 'flex',
        gap: 2,
        background: C.surface2,
        padding: 3,
        borderRadius: 7,
        alignSelf: 'flex-start',
      }}
    >
      {items.map(([k, l, badge]) => (
        <button
          key={k}
          type="button"
          onClick={() => onChange(k)}
          style={{
            padding: '5px 13px',
            borderRadius: 5,
            fontSize: 12.5,
            fontWeight: active === k ? 600 : 500,
            color: active === k ? C.ink : C.ink3,
            background: active === k ? C.surface : 'transparent',
            boxShadow: active === k ? '0 1px 2px rgba(0,0,0,.06)' : 'none',
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            border: 'none',
            cursor: 'pointer',
            fontFamily: 'inherit',
          }}
        >
          {l}
          {badge && (
            <span
              style={{
                fontFamily: C.fontMono,
                fontSize: 10,
                fontWeight: 600,
                color: '#a16207',
                background: C.warnSoft,
                padding: '0 5px',
                borderRadius: 3,
                lineHeight: '14px',
              }}
            >
              {badge}
            </span>
          )}
        </button>
      ))}
    </div>
  )
}

function GlobalRangePicker() {
  const [range, setRange] = useState<'24h' | '7d' | '30d' | 'all'>('7d')
  const items: Array<['24h' | '7d' | '30d' | 'all', string]> = [
    ['24h', '24小时'],
    ['7d', '7天'],
    ['30d', '30天'],
    ['all', '全部'],
  ]
  return (
    <div
      style={{
        display: 'flex',
        background: C.surface2,
        padding: 3,
        borderRadius: 7,
      }}
    >
      {items.map(([k, l]) => (
        <button
          key={k}
          type="button"
          onClick={() => setRange(k)}
          style={{
            padding: '4px 10px',
            borderRadius: 5,
            fontSize: 11.5,
            fontWeight: 500,
            color: range === k ? C.ink : C.ink3,
            background: range === k ? C.surface : 'transparent',
            boxShadow: range === k ? '0 1px 2px rgba(0,0,0,.06)' : 'none',
            border: 'none',
            cursor: 'pointer',
            fontFamily: 'inherit',
          }}
        >
          {l}
        </button>
      ))}
    </div>
  )
}

export function DataShell({
  defaultSubTab = 'archive',
}: {
  defaultSubTab?: SubTab
}) {
  const [subTab, setSubTab] = useState<SubTab>(defaultSubTab)
  const [pendingCount, setPendingCount] = useState(0)

  useEffect(() => {
    let alive = true
    const load = () =>
      fetchPendingList()
        .then((r) => alive && setPendingCount(r.count || 0))
        .catch(() => {})
    load()
    const t = setInterval(load, 12_000)
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [])

  return (
    <div
      style={{
        width: '100%',
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        background: C.bg,
        color: C.ink,
        fontFamily: C.fontUi,
        overflow: 'hidden',
      }}
    >
      {/* sub header: range picker + export */}
      <div
        style={{
          background: C.surface,
          borderBottom: `1px solid ${C.border}`,
          padding: '10px 22px',
          display: 'flex',
          alignItems: 'center',
          gap: 14,
        }}
      >
        <SubTabs active={subTab} onChange={setSubTab} pendingCount={pendingCount} />
        <span style={{ flex: 1 }} />
        {subTab === 'archive' && (
          <span
            style={{
              fontSize: 11.5,
              color: C.ink4,
              fontFamily: C.fontMono,
            }}
          >
            ↓ 用面包屑下拉切 session / decision · ← → 上下条
          </span>
        )}
        {subTab === 'memory' && (
          <span style={{ fontSize: 11.5, color: C.ink4 }}>
            点卡片选中 · 待审核 tab 选中后底部出审核操作
          </span>
        )}
        <span style={{ fontSize: 11.5, color: C.ink3 }}>范围</span>
        <GlobalRangePicker />
        <span
          style={{
            width: 1,
            height: 18,
            background: C.border,
            margin: '0 4px',
          }}
        />
        <button
          type="button"
          style={{
            padding: '5px 11px',
            borderRadius: 6,
            fontSize: 12,
            fontWeight: 500,
            color: C.ink2,
            background: C.surface,
            border: `1px solid ${C.border}`,
            cursor: 'pointer',
            fontFamily: 'inherit',
          }}
        >
          导出
        </button>
      </div>

      {/* body */}
      {subTab === 'archive' ? <ArchiveView /> : <MemoryView />}
    </div>
  )
}
