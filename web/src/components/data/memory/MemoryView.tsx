/**
 * MemoryView — 记忆库 (真实后端).
 *   stats bar (总条目 / pending / by-target 柱图 / outcome 环图)
 *   子子 tab: 已收录 (collected) / 待审核 (pending)
 *   filter row: search + target tabs + 排序
 *   grid: MemoryCard
 *
 * 数据源:
 *   /api/memory/list      → MemoryRecord[]  (collected, 含 hits/success/fail)
 *   /api/memory/pending/list → PendingEntry[] (蓄水池, 等待入库)
 */

import { useEffect, useMemo, useState } from 'react'
import { C } from '@/lib/design-tokens'
import {
  discardPending,
  fetchMemoryList,
  fetchPendingList,
  memorySnapshotSrc,
  pendingSampleSrc,
  type MemoryRecord,
  type PendingEntry,
} from '@/lib/memoryApi'

type CollectedItem = MemoryRecord & {
  /** UI 用 — pretty 时间. */
  lastSeenLabel: string
}

type PendingItem = PendingEntry & {
  lastSeenLabel: string
  primarySampleIdx: number /* 第一个有 snapshot 的 sample idx, 没有就 -1 */
}

function fmtAge(ts: number): string {
  if (!ts) return '—'
  const ms = Date.now() - ts * 1000
  if (ms < 60_000) return `${Math.max(0, Math.floor(ms / 1000))}s 前`
  if (ms < 3600_000) return `${Math.floor(ms / 60_000)}m 前`
  if (ms < 86400_000) return `${Math.floor(ms / 3600_000)}h 前`
  return `${Math.floor(ms / 86400_000)}d 前`
}

function fmtAgeSec(s: number): string {
  if (s < 60) return `${Math.max(0, Math.floor(s))}s`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  return `${Math.floor(s / 3600)}h`
}

// ─── stats sub-components ────────────────────────────────────────────────
function StatTile({
  label,
  value,
  sub,
  color,
}: {
  label: string
  value: string | number
  sub?: string
  color?: string
}) {
  return (
    <div
      style={{
        flex: 1,
        padding: '12px 16px',
        borderRight: `1px solid ${C.borderSoft}`,
        display: 'flex',
        flexDirection: 'column',
        gap: 4,
      }}
    >
      <div
        style={{
          fontSize: 9.5,
          color: C.ink3,
          fontWeight: 500,
          letterSpacing: '.06em',
          textTransform: 'uppercase',
        }}
      >
        {label}
      </div>
      <div
        className="gb-mono"
        style={{
          fontSize: 22,
          fontWeight: 600,
          color: color || C.ink,
          lineHeight: 1.1,
          display: 'flex',
          alignItems: 'baseline',
          gap: 5,
        }}
      >
        {value}
        {sub && (
          <span
            style={{
              fontSize: 11,
              color: C.ink4,
              fontWeight: 500,
              fontFamily: C.fontUi,
            }}
          >
            {sub}
          </span>
        )}
      </div>
    </div>
  )
}

function TargetBars({ data }: { data: { name: string; n: number }[] }) {
  if (data.length === 0) {
    return (
      <div
        style={{
          flex: 1.6,
          padding: '12px 16px',
          borderRight: `1px solid ${C.borderSoft}`,
          display: 'flex',
          flexDirection: 'column',
          gap: 6,
          justifyContent: 'center',
        }}
      >
        <div
          style={{
            fontSize: 9.5,
            color: C.ink3,
            fontWeight: 500,
            letterSpacing: '.06em',
            textTransform: 'uppercase',
          }}
        >
          按 target 分布
        </div>
        <div style={{ fontSize: 11, color: C.ink4 }}>暂无数据</div>
      </div>
    )
  }
  const top = data.slice(0, 5)
  const max = Math.max(...top.map((d) => d.n))
  return (
    <div
      style={{
        flex: 1.6,
        padding: '10px 14px 8px',
        borderRight: `1px solid ${C.borderSoft}`,
        display: 'flex',
        flexDirection: 'column',
        gap: 5,
        minWidth: 0,
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          fontSize: 9.5,
          color: C.ink3,
          fontWeight: 500,
          letterSpacing: '.06em',
          textTransform: 'uppercase',
        }}
      >
        按 target 分布
      </div>
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-end',
          gap: 6,
          height: 32,
          minWidth: 0,
        }}
      >
        {top.map((d, i) => {
          const h = Math.max(4, (d.n / max) * 28)
          return (
            <div
              key={d.name}
              title={`${d.name} · ${d.n}`}
              style={{
                flex: 1,
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'stretch',
                gap: 2,
                minWidth: 0,
                justifyContent: 'flex-end',
              }}
            >
              <span
                className="gb-mono"
                style={{
                  fontSize: 10,
                  color: C.ink2,
                  fontWeight: 600,
                  textAlign: 'center',
                  lineHeight: 1.1,
                }}
              >
                {d.n}
              </span>
              <div
                style={{
                  width: '100%',
                  height: h,
                  background: C.ink2,
                  opacity: 0.9 - i * 0.1,
                  borderRadius: '2px 2px 0 0',
                }}
              />
            </div>
          )
        })}
      </div>
      <div
        style={{
          display: 'flex',
          gap: 6,
          minWidth: 0,
        }}
      >
        {top.map((d) => (
          <div
            key={d.name}
            title={d.name}
            style={{
              flex: 1,
              fontSize: 9.5,
              color: C.ink4,
              textAlign: 'center',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              lineHeight: 1.1,
            }}
          >
            {d.name}
          </div>
        ))}
      </div>
    </div>
  )
}

function OutcomeRing({ success, fail }: { success: number; fail: number }) {
  const total = success + fail
  const pct = total > 0 ? success / total : 0
  const r = 18
  const cx = 22
  const cy = 22
  const len = 2 * Math.PI * r
  return (
    <div
      style={{
        flex: 1,
        padding: '10px 16px',
        display: 'flex',
        alignItems: 'center',
        gap: 12,
      }}
    >
      <svg width="44" height="44" style={{ transform: 'rotate(-90deg)' }}>
        <circle cx={cx} cy={cy} r={r} stroke={C.surface2} strokeWidth="5" fill="none" />
        <circle
          cx={cx}
          cy={cy}
          r={r}
          stroke={C.live}
          strokeWidth="5"
          fill="none"
          strokeDasharray={`${len * pct} ${len}`}
          strokeLinecap="butt"
        />
      </svg>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        <div
          style={{
            fontSize: 9.5,
            color: C.ink3,
            fontWeight: 500,
            letterSpacing: '.06em',
            textTransform: 'uppercase',
          }}
        >
          成功率
        </div>
        <div
          className="gb-mono"
          style={{ fontSize: 14, fontWeight: 600, color: C.ink }}
        >
          {(pct * 100).toFixed(1)}
          <span style={{ fontSize: 11, color: C.ink4 }}>%</span>
        </div>
        <div style={{ fontSize: 10.5, color: C.ink4, fontFamily: C.fontMono }}>
          {success} ✓ · {fail} ✗
        </div>
      </div>
    </div>
  )
}

// ─── memory cards ────────────────────────────────────────────────────────
function CollectedCard({
  item,
  selected,
  onClick,
}: {
  item: CollectedItem
  selected: boolean
  onClick: () => void
}) {
  const successRate = item.success_rate ?? 0
  const denom = item.success_count + item.fail_count
  const tone =
    denom === 0 ? 'idle' : successRate >= 0.85 ? 'live' : successRate >= 0.6 ? 'warn' : 'error'
  const toneColor =
    tone === 'live'
      ? C.live
      : tone === 'warn'
        ? C.warn
        : tone === 'error'
          ? C.error
          : C.ink3

  return (
    <div
      onClick={onClick}
      style={{
        background: C.surface,
        border: `1px solid ${selected ? C.ink2 : C.border}`,
        boxShadow: selected ? `0 0 0 1px ${C.ink2}` : 'none',
        borderRadius: 8,
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
        cursor: 'pointer',
        transition: 'border-color .12s, box-shadow .12s',
      }}
    >
      <div
        style={{
          aspectRatio: '16 / 9',
          position: 'relative',
          background: '#0a0a08',
        }}
      >
        <img
          src={memorySnapshotSrc(item.id)}
          alt=""
          style={{
            width: '100%',
            height: '100%',
            objectFit: 'cover',
            display: 'block',
          }}
          onError={(e) => {
            e.currentTarget.style.opacity = '0'
          }}
        />
        {/* tap point overlay */}
        <div
          style={{
            position: 'absolute',
            left: `${(item.action_x / Math.max(1, item.action_x + item.action_w + 200)) * 100}%`,
            top: `${(item.action_y / Math.max(1, item.action_y + item.action_h + 200)) * 100}%`,
            transform: 'translate(-50%, -50%)',
            width: 14,
            height: 14,
            borderRadius: '50%',
            border: '2px solid #fff',
            boxShadow: `0 0 0 2px ${toneColor}, 0 1px 4px rgba(0,0,0,.4)`,
            background: 'transparent',
          }}
        />
        <div
          style={{
            position: 'absolute',
            bottom: 6,
            right: 6,
            background: 'rgba(20,20,18,.75)',
            color: '#fff',
            fontFamily: C.fontMono,
            fontSize: 10,
            padding: '1px 6px',
            borderRadius: 3,
            backdropFilter: 'blur(3px)',
          }}
        >
          ({item.action_x}, {item.action_y})
        </div>
      </div>

      <div
        style={{
          padding: '9px 11px 11px',
          display: 'flex',
          flexDirection: 'column',
          gap: 6,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span
            style={{
              fontSize: 12,
              fontWeight: 600,
              color: C.ink,
              flex: 1,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {item.target_name}
          </span>
          <span
            className="gb-mono"
            style={{ fontSize: 10.5, color: toneColor, fontWeight: 600 }}
          >
            {denom > 0 ? `${(successRate * 100).toFixed(0)}%` : '新'}
          </span>
        </div>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            fontSize: 11,
            color: C.ink3,
          }}
        >
          <span
            style={{
              padding: '1px 6px',
              borderRadius: 3,
              fontSize: 10,
              background: C.surface2,
              color: C.ink2,
              fontWeight: 500,
              fontFamily: C.fontMono,
            }}
          >
            #{item.id}
          </span>
          <span style={{ flex: 1 }} />
          <span>
            <span className="gb-mono">{item.hit_count}</span> 命中
          </span>
          <span style={{ color: C.ink4 }}>· {item.lastSeenLabel}</span>
        </div>
      </div>
    </div>
  )
}

function PendingCard({
  item,
  selected,
  onClick,
}: {
  item: PendingItem
  selected: boolean
  onClick: () => void
}) {
  const stdSum = item.std_x + item.std_y
  const stdOk = stdSum <= item.max_std_allowed * 2
  return (
    <div
      onClick={onClick}
      style={{
        background: C.surface,
        border: `1px solid ${selected ? C.ink2 : C.border}`,
        boxShadow: selected ? `0 0 0 1px ${C.ink2}` : 'none',
        borderRadius: 8,
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
        cursor: 'pointer',
        transition: 'border-color .12s, box-shadow .12s',
      }}
    >
      <div
        style={{
          aspectRatio: '16 / 9',
          position: 'relative',
          background: '#0a0a08',
        }}
      >
        {item.primarySampleIdx >= 0 ? (
          <img
            src={pendingSampleSrc(item.key, item.primarySampleIdx)}
            alt=""
            style={{
              width: '100%',
              height: '100%',
              objectFit: 'cover',
              display: 'block',
            }}
            onError={(e) => {
              e.currentTarget.style.opacity = '0'
            }}
          />
        ) : (
          <div
            style={{
              position: 'absolute',
              inset: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'rgba(255,255,255,.4)',
              fontSize: 11,
              fontFamily: C.fontMono,
            }}
          >
            no snapshot
          </div>
        )}
        <div
          style={{
            position: 'absolute',
            top: 6,
            left: 6,
            background: C.warnSoft,
            color: '#a16207',
            fontSize: 10,
            fontWeight: 600,
            padding: '2px 7px',
            borderRadius: 3,
            border: `1px solid ${C.warn}40`,
          }}
        >
          待审核 {item.samples}/{item.needed}
        </div>
        <div
          style={{
            position: 'absolute',
            bottom: 6,
            right: 6,
            background: 'rgba(20,20,18,.75)',
            color: '#fff',
            fontFamily: C.fontMono,
            fontSize: 10,
            padding: '1px 6px',
            borderRadius: 3,
            backdropFilter: 'blur(3px)',
          }}
        >
          ({item.median_xy[0]}, {item.median_xy[1]})
        </div>
      </div>

      <div
        style={{
          padding: '9px 11px 11px',
          display: 'flex',
          flexDirection: 'column',
          gap: 6,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span
            style={{
              fontSize: 12,
              fontWeight: 600,
              color: C.ink,
              flex: 1,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {item.target_name}
          </span>
          <span
            className="gb-mono"
            style={{
              fontSize: 10,
              color: stdOk ? C.live : C.warn,
              fontWeight: 600,
            }}
            title={`std σ = ${stdSum.toFixed(1)} px (允许 ${item.max_std_allowed * 2})`}
          >
            σ {stdSum.toFixed(0)}
          </span>
        </div>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            fontSize: 11,
            color: C.ink3,
          }}
        >
          <span
            style={{
              padding: '1px 6px',
              borderRadius: 3,
              fontSize: 10,
              background: C.surface2,
              color: C.ink2,
              fontWeight: 500,
              fontFamily: C.fontMono,
            }}
          >
            ttl {fmtAgeSec(item.ttl_s)}
          </span>
          <span style={{ flex: 1 }} />
          <span style={{ color: C.ink4 }}>{item.lastSeenLabel}</span>
        </div>
      </div>
    </div>
  )
}

// ─── filter row ──────────────────────────────────────────────────────────
function FilterRow({
  targetFilter,
  onTargetChange,
  sortBy,
  onSortChange,
  query,
  onQueryChange,
  count,
  targets,
}: {
  targetFilter: string
  onTargetChange: (k: string) => void
  sortBy: 'hits' | 'recent' | 'success'
  onSortChange: (k: 'hits' | 'recent' | 'success') => void
  query: string
  onQueryChange: (q: string) => void
  count: number
  targets: { name: string; count: number }[]
}) {
  const visibleTargets = targets.slice(0, 6)
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        flexWrap: 'wrap',
      }}
    >
      <input
        value={query}
        onChange={(e) => onQueryChange(e.target.value)}
        placeholder="按 target / id 搜索…"
        style={{
          padding: '6px 10px',
          border: `1px solid ${C.border}`,
          borderRadius: 6,
          fontSize: 12,
          background: C.surface,
          color: C.ink,
          fontFamily: 'inherit',
          outline: 'none',
          width: 220,
          boxSizing: 'border-box',
        }}
      />
      {visibleTargets.length > 0 && (
        <div
          style={{
            display: 'flex',
            background: C.surface2,
            padding: 2,
            borderRadius: 6,
            flexWrap: 'wrap',
          }}
        >
          {[
            ['all', `全部 ${targets.reduce((a, t) => a + t.count, 0)}`] as [string, string],
            ...visibleTargets.map(
              (t) => [t.name, `${t.name} ${t.count}`] as [string, string],
            ),
          ].map(([k, l]) => (
            <button
              key={k}
              type="button"
              onClick={() => onTargetChange(k)}
              style={{
                padding: '4px 10px',
                borderRadius: 4,
                fontSize: 11.5,
                fontWeight: 500,
                color: targetFilter === k ? C.ink : C.ink3,
                background: targetFilter === k ? C.surface : 'transparent',
                boxShadow: targetFilter === k ? '0 1px 2px rgba(0,0,0,.06)' : 'none',
                border: 'none',
                cursor: 'pointer',
                fontFamily: 'inherit',
              }}
            >
              {l}
            </button>
          ))}
        </div>
      )}
      <span style={{ flex: 1 }} />
      <span style={{ fontSize: 11.5, color: C.ink4 }}>
        共 <span className="gb-mono">{count}</span> 条
      </span>
      <select
        value={sortBy}
        onChange={(e) => onSortChange(e.target.value as 'hits' | 'recent' | 'success')}
        style={{
          padding: '5px 8px',
          border: `1px solid ${C.border}`,
          borderRadius: 6,
          fontSize: 12,
          background: C.surface,
          color: C.ink2,
          fontFamily: 'inherit',
          outline: 'none',
        }}
      >
        <option value="hits">按命中数</option>
        <option value="recent">按最近使用</option>
        <option value="success">按成功率</option>
      </select>
    </div>
  )
}

// ─── pending review action bar ───────────────────────────────────────────
function PendingActions({
  item,
  onDiscard,
  onDismissSelection,
}: {
  item: PendingItem
  onDiscard: () => Promise<void>
  onDismissSelection: () => void
}) {
  return (
    <div
      style={{
        background: C.bg,
        border: `1px solid ${C.border}`,
        borderRadius: 8,
        padding: 12,
        display: 'flex',
        gap: 8,
        alignItems: 'center',
      }}
    >
      <span style={{ fontSize: 12, color: C.ink2, flex: 1 }}>
        选中{' '}
        <span className="gb-mono" style={{ color: C.ink }}>
          {item.target_name}
        </span>{' '}
        · 当前样本{' '}
        <span className="gb-mono">
          {item.samples}/{item.needed}
        </span>{' '}
        · 满阈值后自动入库, 否则按 ttl 过期
      </span>
      <button
        type="button"
        onClick={async () => {
          await onDiscard()
          onDismissSelection()
        }}
        style={{
          padding: '6px 12px',
          fontSize: 12,
          fontWeight: 500,
          color: C.error,
          background: C.errorSoft,
          border: `1px solid ${C.error}30`,
          borderRadius: 6,
          cursor: 'pointer',
          fontFamily: 'inherit',
        }}
      >
        丢弃 (清空蓄水)
      </button>
    </div>
  )
}

// ─── main view ───────────────────────────────────────────────────────────
export function MemoryView() {
  const [tab, setTab] = useState<'collected' | 'pending'>('collected')
  const [collected, setCollected] = useState<CollectedItem[] | null>(null)
  const [collectedTargets, setCollectedTargets] = useState<
    { name: string; count: number }[]
  >([])
  const [pending, setPending] = useState<PendingItem[] | null>(null)
  const [available, setAvailable] = useState(true)
  const [err, setErr] = useState<string | null>(null)

  const [query, setQuery] = useState('')
  const [targetFilter, setTargetFilter] = useState('all')
  const [sortBy, setSortBy] = useState<'hits' | 'recent' | 'success'>('hits')
  const [selectedKey, setSelectedKey] = useState<string | null>(null)

  const reload = async () => {
    setErr(null)
    try {
      const [c, p] = await Promise.all([fetchMemoryList(), fetchPendingList()])
      const collectedItems: CollectedItem[] = (c.items || []).map((r) => ({
        ...r,
        lastSeenLabel: fmtAge(r.last_seen_ts),
      }))
      const pendingItems: PendingItem[] = (p.items || []).map((e) => ({
        ...e,
        lastSeenLabel: e.age_s ? `${fmtAgeSec(e.age_s)} 前` : '刚刚',
        primarySampleIdx:
          e.samples_detail?.findIndex((s) => s.has_snapshot) ?? -1,
      }))
      setCollected(collectedItems)
      setCollectedTargets(c.targets || [])
      setPending(pendingItems)
      setAvailable(!!c.available || !!p.available)
    } catch (e) {
      setErr(String(e))
    }
  }

  useEffect(() => {
    reload()
    const t = setInterval(reload, 12_000)
    return () => clearInterval(t)
  }, [])

  // unique targets across both lists for the filter row
  const allTargets = useMemo(() => {
    const m = new Map<string, number>()
    if (collected) for (const c of collected) m.set(c.target_name, (m.get(c.target_name) || 0) + 1)
    if (pending) for (const p of pending) m.set(p.target_name, (m.get(p.target_name) || 0) + 1)
    return [...m.entries()]
      .map(([name, count]) => ({ name, count }))
      .sort((a, b) => b.count - a.count)
  }, [collected, pending])

  const collectedFiltered = useMemo(() => {
    if (!collected) return []
    let list = collected.filter(
      (it) =>
        (targetFilter === 'all' || it.target_name === targetFilter) &&
        (!query ||
          it.target_name.toLowerCase().includes(query.toLowerCase()) ||
          String(it.id).includes(query)),
    )
    if (sortBy === 'hits') list = [...list].sort((a, b) => b.hit_count - a.hit_count)
    if (sortBy === 'recent')
      list = [...list].sort((a, b) => (b.last_seen_ts || 0) - (a.last_seen_ts || 0))
    if (sortBy === 'success')
      list = [...list].sort((a, b) => (b.success_rate || 0) - (a.success_rate || 0))
    return list
  }, [collected, targetFilter, sortBy, query])

  const pendingFiltered = useMemo(() => {
    if (!pending) return []
    let list = pending.filter(
      (it) =>
        (targetFilter === 'all' || it.target_name === targetFilter) &&
        (!query ||
          it.target_name.toLowerCase().includes(query.toLowerCase()) ||
          it.key.includes(query)),
    )
    if (sortBy === 'hits') list = [...list].sort((a, b) => b.samples - a.samples)
    if (sortBy === 'recent') list = [...list].sort((a, b) => a.age_s - b.age_s)
    if (sortBy === 'success')
      list = [...list].sort((a, b) => a.std_x + a.std_y - (b.std_x + b.std_y))
    return list
  }, [pending, targetFilter, sortBy, query])

  const total = collected?.length ?? 0
  const pendingTotal = pending?.length ?? 0
  const totalSuccess = collected?.reduce((a, c) => a + c.success_count, 0) ?? 0
  const totalFail = collected?.reduce((a, c) => a + c.fail_count, 0) ?? 0
  const targetBars = allTargets.slice(0, 8)

  if (err) {
    return (
      <div
        style={{
          flex: 1,
          padding: 30,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <div
          style={{
            background: C.surface,
            border: `1px solid ${C.error}30`,
            borderRadius: 8,
            padding: '16px 20px',
            maxWidth: 480,
            display: 'flex',
            flexDirection: 'column',
            gap: 6,
          }}
        >
          <div style={{ fontSize: 13, fontWeight: 600, color: C.error }}>
            无法加载记忆库
          </div>
          <div
            className="gb-mono"
            style={{ fontSize: 11.5, color: C.ink3, wordBreak: 'break-word' }}
          >
            {err}
          </div>
          <div style={{ fontSize: 11, color: C.ink4 }}>
            确认后端 /api/memory/list + /api/memory/pending/list 在线
          </div>
        </div>
      </div>
    )
  }

  if (!collected || !pending) {
    return (
      <div
        style={{
          flex: 1,
          padding: 30,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: C.ink4,
          fontSize: 13,
        }}
      >
        加载记忆库…
      </div>
    )
  }

  if (!available) {
    return (
      <div
        style={{
          flex: 1,
          padding: 30,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <div
          style={{
            padding: 40,
            textAlign: 'center',
            fontSize: 13,
            color: C.ink4,
            background: C.surface,
            border: `1px dashed ${C.border}`,
            borderRadius: 8,
            maxWidth: 460,
          }}
        >
          记忆库尚未启用 · runner 第一次成功决策后会写入
        </div>
      </div>
    )
  }

  const selectedPending =
    tab === 'pending' && selectedKey
      ? pendingFiltered.find((p) => p.key === selectedKey) || null
      : null

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 14,
        padding: '14px 22px 22px',
        flex: 1,
        minHeight: 0,
      }}
    >
      {/* stats bar */}
      <div
        style={{
          display: 'flex',
          background: C.surface,
          border: `1px solid ${C.border}`,
          borderRadius: 8,
          overflow: 'hidden',
        }}
      >
        <StatTile label="总条目" value={total.toLocaleString()} />
        <StatTile
          label="待审核"
          value={pendingTotal}
          color={pendingTotal > 0 ? C.warn : C.ink}
        />
        <StatTile
          label="总命中"
          value={(collected?.reduce((a, c) => a + c.hit_count, 0) || 0).toLocaleString()}
        />
        <TargetBars data={targetBars.map((t) => ({ name: t.name, n: t.count }))} />
        <OutcomeRing success={totalSuccess} fail={totalFail} />
      </div>

      {/* sub-sub tabs */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          borderBottom: `1px solid ${C.border}`,
        }}
      >
        {(
          [
            ['collected', '已收录', total, null] as const,
            [
              'pending',
              '待审核',
              pendingTotal,
              pendingTotal > 0 ? C.warn : null,
            ] as const,
          ]
        ).map(([k, l, n, dot]) => (
          <button
            key={k}
            type="button"
            onClick={() => {
              setTab(k)
              setSelectedKey(null)
            }}
            style={{
              padding: '8px 4px',
              marginBottom: -1,
              fontSize: 13,
              fontWeight: 500,
              color: tab === k ? C.ink : C.ink3,
              borderBottom: `2px solid ${tab === k ? C.ink : 'transparent'}`,
              borderTop: 'none',
              borderLeft: 'none',
              borderRight: 'none',
              background: 'transparent',
              display: 'inline-flex',
              alignItems: 'center',
              gap: 7,
              cursor: 'pointer',
              fontFamily: 'inherit',
            }}
          >
            {l}
            <span
              className="gb-mono"
              style={{
                fontSize: 11,
                color: tab === k ? C.ink3 : C.ink4,
                padding: '1px 6px',
                background: C.surface2,
                borderRadius: 3,
              }}
            >
              {n}
            </span>
            {dot && (
              <span
                className="gb-blink"
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: '50%',
                  background: dot,
                }}
              />
            )}
          </button>
        ))}
      </div>

      <FilterRow
        targetFilter={targetFilter}
        onTargetChange={setTargetFilter}
        sortBy={sortBy}
        onSortChange={setSortBy}
        query={query}
        onQueryChange={setQuery}
        count={tab === 'collected' ? collectedFiltered.length : pendingFiltered.length}
        targets={tab === 'collected' ? (collectedTargets.length ? collectedTargets : allTargets) : allTargets}
      />

      {selectedPending && (
        <PendingActions
          item={selectedPending}
          onDiscard={async () => {
            try {
              await discardPending(selectedPending.key)
              await reload()
            } catch {
              /* ignore */
            }
          }}
          onDismissSelection={() => setSelectedKey(null)}
        />
      )}

      {/* grid */}
      <div
        className="gb-scroll"
        style={{
          flex: 1,
          minHeight: 0,
          overflow: 'hidden auto',
          margin: '0 -2px',
          padding: '0 2px',
        }}
      >
        {tab === 'collected' ? (
          collectedFiltered.length === 0 ? (
            <div
              style={{
                padding: 60,
                textAlign: 'center',
                fontSize: 13,
                color: C.ink4,
                background: C.surface,
                border: `1px dashed ${C.border}`,
                borderRadius: 8,
              }}
            >
              {collected.length === 0
                ? '记忆库为空 · 还没有任何记录'
                : '没有匹配的记忆条目'}
            </div>
          ) : (
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fill, minmax(232px, 1fr))',
                gap: 12,
              }}
            >
              {collectedFiltered.map((it) => (
                <CollectedCard
                  key={it.id}
                  item={it}
                  selected={selectedKey === String(it.id)}
                  onClick={() =>
                    setSelectedKey((s) => (s === String(it.id) ? null : String(it.id)))
                  }
                />
              ))}
            </div>
          )
        ) : pendingFiltered.length === 0 ? (
          <div
            style={{
              padding: 60,
              textAlign: 'center',
              fontSize: 13,
              color: C.ink4,
              background: C.surface,
              border: `1px dashed ${C.border}`,
              borderRadius: 8,
            }}
          >
            {pending.length === 0
              ? '没有待审核条目 · 蓄水池为空'
              : '没有匹配的待审核条目'}
          </div>
        ) : (
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(232px, 1fr))',
              gap: 12,
            }}
          >
            {pendingFiltered.map((it) => (
              <PendingCard
                key={it.key}
                item={it}
                selected={selectedKey === it.key}
                onClick={() =>
                  setSelectedKey((s) => (s === it.key ? null : it.key))
                }
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
