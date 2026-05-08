/**
 * ArchiveView — 决策档案, 单决策详情聚焦.
 *
 * Overview 态: 全部 session 卡片 (来自 /api/sessions, 客户端做轻量聚合)
 * Detail   态: 3 列布局 (决策列表 / 大图 + frame switcher / KPI + 5-tier accordion)
 *
 * 数据全部来自后端 (archive-adapter); mock 已经移除.
 */

import { useEffect, useMemo, useState } from 'react'
import { C } from '@/lib/design-tokens'
import {
  TIER_META,
  fetchSessionList,
  fetchDecisionList,
  fetchDecisionDetail,
  aggregateSession,
  decisionImgSrc,
  type SessionSummary,
  type DecisionRow,
  type DecisionDetail,
  type UIEvidence,
  type TierKey,
} from '@/lib/archive-adapter'

// ─── popover dropdown wrapper ────────────────────────────────────────────
function CrumbDropdown({
  trigger,
  children,
  width = 360,
  align = 'left',
}: {
  trigger: React.ReactNode
  children: (close: () => void) => React.ReactNode
  width?: number
  align?: 'left' | 'right'
}) {
  const [open, setOpen] = useState(false)
  return (
    <div style={{ position: 'relative', display: 'inline-block' }}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        style={{
          padding: '4px 9px 4px 11px',
          borderRadius: 6,
          fontSize: 13,
          fontWeight: 600,
          color: C.ink,
          background: open ? C.surface2 : 'transparent',
          border: `1px solid ${open ? C.border : 'transparent'}`,
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
          lineHeight: 1.3,
          cursor: 'pointer',
          fontFamily: 'inherit',
        }}
      >
        {trigger}
        <svg width="9" height="9" viewBox="0 0 9 9" style={{ marginTop: 1 }}>
          <path
            d="M1.5 3l3 3 3-3"
            stroke={C.ink3}
            strokeWidth="1.4"
            fill="none"
            strokeLinecap="round"
          />
        </svg>
      </button>
      {open && (
        <>
          <div
            onClick={() => setOpen(false)}
            style={{ position: 'fixed', inset: 0, zIndex: 9 }}
          />
          <div
            style={{
              position: 'absolute',
              top: '100%',
              [align]: 0,
              marginTop: 6,
              width,
              background: C.surface,
              border: `1px solid ${C.border}`,
              borderRadius: 8,
              boxShadow: '0 12px 32px rgba(0,0,0,.10), 0 2px 6px rgba(0,0,0,.06)',
              zIndex: 10,
              overflow: 'hidden',
              animation: 'gbFadeIn .14s ease-out',
            }}
          >
            {children(() => setOpen(false))}
          </div>
        </>
      )}
    </div>
  )
}

// ─── session list popover ────────────────────────────────────────────────
function SessionPopover({
  active,
  sessions,
  onPick,
  close,
}: {
  active: string
  sessions: SessionSummary[]
  onPick: (id: string) => void
  close: () => void
}) {
  const [q, setQ] = useState('')
  const filtered = sessions.filter(
    (s) => !q || s.id.toLowerCase().includes(q.toLowerCase()) || s.day.includes(q),
  )
  return (
    <div>
      <div style={{ padding: 8, borderBottom: `1px solid ${C.borderSoft}` }}>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="搜索 session id 或日期…"
          style={{
            width: '100%',
            padding: '6px 9px',
            border: `1px solid ${C.border}`,
            borderRadius: 5,
            fontSize: 12,
            background: C.bg,
            color: C.ink,
            fontFamily: 'inherit',
            outline: 'none',
            boxSizing: 'border-box',
          }}
        />
      </div>
      <div className="gb-scroll" style={{ maxHeight: 340, padding: 4, overflowY: 'auto' }}>
        {filtered.map((s) => (
          <button
            key={s.id}
            type="button"
            onClick={() => {
              onPick(s.id)
              close()
            }}
            style={{
              display: 'flex',
              width: '100%',
              textAlign: 'left',
              padding: '8px 10px',
              borderRadius: 5,
              gap: 10,
              background: s.id === active ? C.surface2 : 'transparent',
              alignItems: 'center',
              border: 'none',
              cursor: 'pointer',
              fontFamily: 'inherit',
            }}
          >
            <div
              style={{
                width: 4,
                alignSelf: 'stretch',
                borderRadius: 2,
                background: s.id === active ? C.ink : 'transparent',
              }}
            />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                <span style={{ fontSize: 12.5, fontWeight: 600, color: C.ink }}>
                  {s.day}
                </span>
                <span className="gb-mono" style={{ fontSize: 10.5, color: C.ink4 }}>
                  #{s.id}
                </span>
                {s.isCurrent && (
                  <span
                    style={{
                      fontSize: 9.5,
                      color: C.live,
                      background: C.liveSoft,
                      padding: '0 5px',
                      borderRadius: 3,
                      fontWeight: 600,
                    }}
                  >
                    LIVE
                  </span>
                )}
              </div>
              <div
                style={{
                  fontSize: 11,
                  color: C.ink3,
                  marginTop: 2,
                  display: 'flex',
                  gap: 10,
                }}
              >
                <span>
                  <span className="gb-mono">{s.decisions}</span> 决策
                </span>
              </div>
            </div>
          </button>
        ))}
        {filtered.length === 0 && (
          <div style={{ padding: 16, textAlign: 'center', fontSize: 12, color: C.ink4 }}>
            没有匹配的 session
          </div>
        )}
      </div>
    </div>
  )
}

// ─── tier legend overlay ────────────────────────────────────────────────
function TierLegend({
  visible,
  onToggle,
}: {
  visible: Set<string>
  onToggle: (k: TierKey) => void
}) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        padding: 6,
        background: 'rgba(20,20,18,.78)',
        borderRadius: 6,
        backdropFilter: 'blur(6px)',
      }}
    >
      <span
        style={{
          fontSize: 10,
          color: 'rgba(255,255,255,.55)',
          fontFamily: C.fontMono,
          marginRight: 2,
        }}
      >
        BBOX
      </span>
      {(Object.entries(TIER_META) as [TierKey, typeof TIER_META.T1][]).map(([k, m]) => {
        const on = visible.has(k)
        return (
          <button
            key={k}
            type="button"
            onClick={() => onToggle(k)}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 5,
              padding: '3px 7px',
              borderRadius: 4,
              background: on ? 'rgba(255,255,255,.08)' : 'transparent',
              border: `1px solid ${on ? 'rgba(255,255,255,.18)' : 'transparent'}`,
              fontFamily: C.fontMono,
              fontSize: 10.5,
              fontWeight: 600,
              color: on ? '#fff' : 'rgba(255,255,255,.4)',
              cursor: 'pointer',
            }}
          >
            <span
              style={{
                width: 9,
                height: 9,
                borderRadius: 2,
                background: m.color,
                opacity: on ? 1 : 0.3,
                boxShadow: on ? `0 0 0 1.5px ${m.color}40` : 'none',
              }}
            />
            {k}
          </button>
        )
      })}
    </div>
  )
}

// ─── decision list ──────────────────────────────────────────────────────
function DecisionList({
  decisions,
  activeId,
  onPick,
}: {
  decisions: DecisionRow[]
  activeId: string
  onPick: (id: string) => void
}) {
  const [resultFilter, setResultFilter] = useState<'all' | 'ok' | 'fail' | 'pending'>(
    'all',
  )
  const [tierFilter, setTierFilter] = useState<'all' | TierKey>('all')
  const [instFilter, setInstFilter] = useState<number | 'all'>('all')

  const allInstances = useMemo(() => {
    const s = new Set<number>()
    for (const d of decisions) s.add(d.instance)
    return [...s].sort((a, b) => a - b)
  }, [decisions])

  const filtered = decisions.filter((d) => {
    if (resultFilter === 'ok' && d.ok !== true) return false
    if (resultFilter === 'fail' && d.ok !== false) return false
    if (resultFilter === 'pending' && d.ok !== null) return false
    if (tierFilter !== 'all' && d.decidedBy !== tierFilter) return false
    if (instFilter !== 'all' && d.instance !== instFilter) return false
    return true
  })

  return (
    <div
      style={{
        background: C.surface,
        border: `1px solid ${C.border}`,
        borderRadius: 8,
        display: 'flex',
        flexDirection: 'column',
        minHeight: 0,
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          padding: '10px 12px',
          borderBottom: `1px solid ${C.borderSoft}`,
          display: 'flex',
          alignItems: 'center',
          gap: 6,
        }}
      >
        <span style={{ fontSize: 12, fontWeight: 600, color: C.ink2 }}>决策日志</span>
        <span className="gb-mono" style={{ fontSize: 10.5, color: C.ink4 }}>
          {filtered.length}/{decisions.length}
        </span>
      </div>
      <div
        style={{
          padding: '8px 10px',
          borderBottom: `1px solid ${C.borderSoft}`,
          display: 'flex',
          flexDirection: 'column',
          gap: 6,
          background: C.surface2,
        }}
      >
        {/* 结果 segmented */}
        <div
          style={{
            display: 'flex',
            background: C.surface,
            padding: 2,
            borderRadius: 5,
            gap: 1,
          }}
        >
          {(
            [
              ['all', '全部'],
              ['ok', '成功'],
              ['fail', '失败'],
              ['pending', '未验'],
            ] as const
          ).map(([k, l]) => (
            <button
              key={k}
              type="button"
              onClick={() => setResultFilter(k)}
              style={{
                flex: 1,
                padding: '3px 0',
                borderRadius: 3,
                fontSize: 10.5,
                fontWeight: 500,
                color: resultFilter === k ? C.ink : C.ink3,
                background: resultFilter === k ? C.surface2 : 'transparent',
                border: 'none',
                cursor: 'pointer',
                fontFamily: 'inherit',
              }}
            >
              {l}
            </button>
          ))}
        </div>

        {/* 实例 切换 — 当前 session 涉及的实例都列出 */}
        {allInstances.length > 0 && (
          <div
            style={{
              display: 'flex',
              gap: 3,
              alignItems: 'center',
              flexWrap: 'wrap',
            }}
          >
            <span
              style={{
                fontSize: 10,
                color: C.ink4,
                fontWeight: 500,
                marginRight: 2,
                textTransform: 'uppercase',
                letterSpacing: '.06em',
              }}
            >
              实例
            </span>
            <button
              type="button"
              onClick={() => setInstFilter('all')}
              style={{
                padding: '2px 7px',
                borderRadius: 4,
                fontSize: 10.5,
                fontWeight: 600,
                color: instFilter === 'all' ? C.ink : C.ink3,
                background: instFilter === 'all' ? C.surface : 'transparent',
                border: `1px solid ${instFilter === 'all' ? C.border : 'transparent'}`,
                cursor: 'pointer',
                fontFamily: 'inherit',
              }}
            >
              全部
            </button>
            {allInstances.map((idx) => (
              <button
                key={idx}
                type="button"
                onClick={() => setInstFilter(idx)}
                style={{
                  padding: '2px 7px',
                  borderRadius: 4,
                  fontFamily: C.fontMono,
                  fontSize: 10.5,
                  fontWeight: 600,
                  color: instFilter === idx ? C.ink : C.ink3,
                  background: instFilter === idx ? C.surface : 'transparent',
                  border: `1px solid ${instFilter === idx ? C.border : 'transparent'}`,
                  cursor: 'pointer',
                }}
              >
                #{idx}
              </button>
            ))}
          </div>
        )}

        {/* 决策来源 tier — 圆形色块 */}
        <div
          style={{
            display: 'flex',
            gap: 4,
            alignItems: 'center',
          }}
        >
          <span
            style={{
              fontSize: 10,
              color: C.ink4,
              fontWeight: 500,
              marginRight: 2,
              textTransform: 'uppercase',
              letterSpacing: '.06em',
            }}
          >
            来源
          </span>
          <button
            type="button"
            onClick={() => setTierFilter('all')}
            style={{
              padding: '2px 7px',
              borderRadius: 4,
              fontSize: 10.5,
              fontWeight: 600,
              color: tierFilter === 'all' ? C.ink : C.ink3,
              background: tierFilter === 'all' ? C.surface : 'transparent',
              border: `1px solid ${tierFilter === 'all' ? C.border : 'transparent'}`,
              cursor: 'pointer',
              fontFamily: 'inherit',
            }}
          >
            全部
          </button>
          {(['T1', 'T2', 'T3', 'T4', 'T5'] as const).map((k) => {
            const m = TIER_META[k]
            const on = tierFilter === k
            return (
              <button
                key={k}
                type="button"
                onClick={() => setTierFilter(k)}
                title={`${k} ${m.name}`}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 4,
                  padding: '2px 7px',
                  borderRadius: 4,
                  fontFamily: C.fontMono,
                  fontSize: 10.5,
                  fontWeight: 700,
                  color: on ? m.color : C.ink3,
                  background: on ? m.soft : 'transparent',
                  border: `1px solid ${on ? m.color + '60' : 'transparent'}`,
                  cursor: 'pointer',
                }}
              >
                <span
                  style={{
                    width: 6,
                    height: 6,
                    borderRadius: '50%',
                    background: m.color,
                    opacity: on ? 1 : 0.6,
                  }}
                />
                {k}
              </button>
            )
          })}
        </div>
      </div>
      <div className="gb-scroll" style={{ flex: 1, minHeight: 0, overflow: 'hidden auto' }}>
        {filtered.map((d, i) => {
          const tm = d.decidedBy ? TIER_META[d.decidedBy] : null
          const active = d.id === activeId
          return (
            <button
              key={d.id}
              type="button"
              onClick={() => onPick(d.id)}
              style={{
                display: 'block',
                width: '100%',
                textAlign: 'left',
                padding: '8px 12px',
                borderTop: i === 0 ? 'none' : `1px solid ${C.borderSoft}`,
                background: active ? C.surface2 : 'transparent',
                position: 'relative',
                border: 'none',
                cursor: 'pointer',
                fontFamily: 'inherit',
              }}
            >
              {active && (
                <span
                  style={{
                    position: 'absolute',
                    left: 0,
                    top: 0,
                    bottom: 0,
                    width: 3,
                    background: C.ink,
                  }}
                />
              )}
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 7,
                  fontSize: 11.5,
                  marginBottom: 3,
                }}
              >
                <span
                  style={{
                    width: 5,
                    height: 5,
                    borderRadius: '50%',
                    background:
                      d.ok === true ? C.live : d.ok === false ? C.error : C.ink4,
                  }}
                />
                <span className="gb-mono" style={{ color: C.ink3 }}>
                  {d.ts.slice(0, 8)}
                </span>
                {tm && (
                  <span
                    className="gb-mono"
                    style={{
                      fontSize: 10,
                      fontWeight: 700,
                      color: tm.color,
                      padding: '0 5px',
                      borderRadius: 3,
                      background: tm.soft,
                      lineHeight: '15px',
                    }}
                  >
                    {d.decidedBy}
                  </span>
                )}
                <span
                  className="gb-mono"
                  style={{
                    color: active ? C.ink : C.ink2,
                    fontWeight: 600,
                    fontSize: 10.5,
                  }}
                >
                  #{d.round}
                </span>
                <span style={{ flex: 1 }} />
                <span
                  className="gb-mono"
                  style={{
                    fontSize: 10,
                    color: C.ink4,
                    background: C.surface2,
                    padding: '0 5px',
                    borderRadius: 3,
                    fontWeight: 600,
                  }}
                >
                  #{d.instance}
                </span>
              </div>
              <div
                style={{
                  fontSize: 12,
                  color: active ? C.ink : C.ink2,
                  fontWeight: 500,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {d.target}
              </div>
              <div
                style={{
                  fontSize: 10.5,
                  color: C.ink4,
                  marginTop: 2,
                  display: 'flex',
                  gap: 8,
                  alignItems: 'center',
                }}
              >
                <span>{d.phaseLabel}</span>
                <span>·</span>
                <span className="gb-mono">{d.outcome || '—'}</span>
                {d.ok === false && (
                  <>
                    <span>·</span>
                    <span style={{ color: C.error }}>失败</span>
                  </>
                )}
              </div>
            </button>
          )
        })}
        {filtered.length === 0 && (
          <div
            style={{ padding: 30, textAlign: 'center', fontSize: 12, color: C.ink4 }}
          >
            当前筛选无决策
          </div>
        )}
      </div>
    </div>
  )
}

// ─── KPI strip vertical ─────────────────────────────────────────────────
function KpiStripVertical({ decision }: { decision: DecisionDetail }) {
  const tm = decision.decidedBy ? TIER_META[decision.decidedBy] : null
  const okLabel = decision.ok === true ? '成功' : decision.ok === false ? '失败' : '未验'
  const okTone =
    decision.ok === true ? 'live' : decision.ok === false ? 'error' : undefined
  const items: Array<{
    label: string
    value: string | number
    sub?: string
    tone?: 'live' | 'error'
    color?: string
    mono?: boolean
  }> = [
    { label: '结果', value: okLabel, tone: okTone },
    {
      label: '决策',
      value: decision.decidedBy || '—',
      sub: tm?.name,
      color: tm?.color,
    },
    { label: '置信度', value: decision.confidence, mono: true },
    { label: '总耗时', value: decision.latency_ms, sub: 'ms', mono: true },
  ]
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(4, 1fr)',
        background: C.surface,
        border: `1px solid ${C.border}`,
        borderRadius: 8,
        overflow: 'hidden',
      }}
    >
      {items.map((it, i) => (
        <div
          key={i}
          style={{
            padding: '12px 14px',
            borderRight: i < items.length - 1 ? `1px solid ${C.borderSoft}` : 'none',
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
            {it.label}
          </div>
          <div
            className={it.mono ? 'gb-mono' : ''}
            style={{
              marginTop: 4,
              fontSize: 18,
              fontWeight: 600,
              color:
                it.tone === 'live'
                  ? C.live
                  : it.tone === 'error'
                    ? C.error
                    : it.color || C.ink,
              display: 'flex',
              alignItems: 'baseline',
              gap: 4,
              lineHeight: 1.1,
            }}
          >
            {it.value}
            {it.sub && (
              <span style={{ fontSize: 11, color: C.ink4, fontWeight: 500 }}>
                {it.sub}
              </span>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

// ─── tier accordion row ─────────────────────────────────────────────────
function TierRow({
  ev,
  openMap,
  onToggle,
  visibleTiers,
  onTierVisToggle,
}: {
  ev: UIEvidence
  openMap: Record<string, boolean>
  onToggle: (k: TierKey) => void
  visibleTiers: Set<string>
  onTierVisToggle: (k: TierKey) => void
}) {
  const m = TIER_META[ev.tier]
  const isOpen = !!openMap[ev.tier]
  const isDecided = ev.isDecided

  const status: { label: string; tone: 'idle' | 'decided' | 'live' } = !ev.tried
    ? { label: '未尝试', tone: 'idle' }
    : ev.passed && isDecided
      ? { label: '决策', tone: 'decided' }
      : ev.passed
        ? { label: '命中', tone: 'live' }
        : { label: '未通过', tone: 'idle' }

  const statusColor =
    status.tone === 'decided' ? m.color : status.tone === 'live' ? C.live : C.ink4

  return (
    <div
      style={{
        borderBottom: `1px solid ${C.borderSoft}`,
        background: isDecided
          ? `linear-gradient(90deg, ${m.soft}30 0%, transparent 60%)`
          : C.surface,
      }}
    >
      <button
        type="button"
        onClick={() => onToggle(ev.tier)}
        style={{
          display: 'flex',
          width: '100%',
          alignItems: 'center',
          gap: 11,
          padding: '11px 14px',
          textAlign: 'left',
          background: 'transparent',
          border: 'none',
          cursor: 'pointer',
          fontFamily: 'inherit',
        }}
      >
        <span
          style={{
            width: 3,
            height: 26,
            borderRadius: 2,
            background: m.color,
            opacity: ev.tried ? 1 : 0.25,
          }}
        />
        <span
          style={{
            fontFamily: C.fontMono,
            fontSize: 12,
            fontWeight: 700,
            color: m.color,
            width: 24,
          }}
        >
          {ev.tier}
        </span>
        <span style={{ fontSize: 13, fontWeight: 600, color: C.ink, width: 64 }}>
          {ev.kindName || m.name}
        </span>

        <span
          className="gb-mono"
          style={{
            fontSize: 11.5,
            color: ev.tried ? C.ink2 : C.ink4,
            flex: 1,
            minWidth: 0,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {ev.detail}
        </span>

        <span
          className="gb-mono"
          style={{
            fontSize: 11.5,
            color: ev.tried ? C.ink2 : C.ink4,
            width: 50,
            textAlign: 'right',
          }}
        >
          {ev.conf}
        </span>

        <span
          style={{
            fontSize: 10.5,
            fontWeight: 600,
            padding: '2px 7px',
            borderRadius: 3,
            color: status.tone === 'decided' ? '#fff' : statusColor,
            background:
              status.tone === 'decided'
                ? m.color
                : status.tone === 'live'
                  ? C.liveSoft
                  : 'transparent',
            border:
              status.tone === 'decided' || status.tone === 'live'
                ? 'none'
                : `1px solid ${C.border}`,
            width: 56,
            textAlign: 'center',
            lineHeight: 1.3,
          }}
        >
          {status.label}
        </span>

        <span
          style={{
            width: 22,
            display: 'inline-flex',
            justifyContent: 'center',
            transform: isOpen ? 'rotate(180deg)' : 'none',
            transition: 'transform .18s',
            color: C.ink3,
          }}
        >
          <svg width="10" height="10" viewBox="0 0 10 10">
            <path
              d="M2 3.5l3 3 3-3"
              stroke="currentColor"
              strokeWidth="1.5"
              fill="none"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </span>
      </button>

      {isOpen && (
        <div
          style={{
            padding: '6px 14px 14px 44px',
            fontSize: 12,
            color: C.ink2,
            lineHeight: 1.65,
            animation: 'gbFadeIn .14s ease-out',
          }}
        >
          <div style={{ color: C.ink3, fontSize: 11, marginBottom: 6 }}>{m.desc}</div>

          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '90px 1fr',
              rowGap: 5,
              columnGap: 10,
            }}
          >
            <span style={{ color: C.ink3 }}>命中状态</span>
            <span style={{ color: ev.passed ? C.live : C.ink4, fontWeight: 500 }}>
              {ev.tried
                ? ev.passed
                  ? '命中 · 通过阈值'
                  : '尝试 · 未通过阈值'
                : '未触发 (上层已决策)'}
            </span>

            <span style={{ color: C.ink3 }}>置信度</span>
            <span className="gb-mono">{ev.conf}</span>

            {ev.tried && (
              <>
                <span style={{ color: C.ink3 }}>耗时</span>
                <span className="gb-mono">{ev.latency_ms} ms</span>
              </>
            )}

            {ev.bbox && (
              <>
                <span style={{ color: C.ink3 }}>bbox</span>
                <span
                  className="gb-mono"
                  style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}
                >
                  <span>
                    ({ev.bbox[0]}, {ev.bbox[1]}) {ev.bbox[2]}×{ev.bbox[3]}
                  </span>
                  {ev.bboxes.length > 1 && (
                    <span
                      style={{
                        fontSize: 10,
                        color: m.color,
                        background: m.soft,
                        padding: '0 5px',
                        borderRadius: 3,
                        fontWeight: 600,
                      }}
                    >
                      +{ev.bboxes.length - 1}
                    </span>
                  )}
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation()
                      onTierVisToggle(ev.tier)
                    }}
                    style={{
                      padding: '1px 7px',
                      borderRadius: 3,
                      fontSize: 10.5,
                      fontWeight: 500,
                      color: visibleTiers.has(ev.tier) ? m.color : C.ink4,
                      border: `1px solid ${visibleTiers.has(ev.tier) ? m.color + '60' : C.border}`,
                      background: visibleTiers.has(ev.tier) ? m.soft : 'transparent',
                      cursor: 'pointer',
                      fontFamily: 'inherit',
                    }}
                  >
                    {visibleTiers.has(ev.tier) ? '在图中显示' : '在图中隐藏'}
                  </button>
                </span>
              </>
            )}

            <span style={{ color: C.ink3 }}>详情</span>
            <span className="gb-mono">{ev.detail}</span>

            {ev.memoryHash && (
              <>
                <span style={{ color: C.ink3 }}>记忆 hash</span>
                <span className="gb-mono" style={{ wordBreak: 'break-all' }}>
                  {ev.memoryHash}
                </span>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// ─── overview state ─────────────────────────────────────────────────────
type AggMap = Record<string, ReturnType<typeof aggregateSession> | 'loading' | 'error'>

function ArchiveOverview({
  sessions,
  onPick,
}: {
  sessions: SessionSummary[]
  onPick: (id: string) => void
}) {
  const [hoverId, setHoverId] = useState<string | null>(null)
  const [aggMap, setAggMap] = useState<AggMap>({})

  // 懒加载: 每个 session 后台拉决策列表 + 算聚合, 4 并发
  useEffect(() => {
    let alive = true
    const queue = [...sessions]
    const concurrency = 4
    let active = 0

    const next = () => {
      if (!alive) return
      while (active < concurrency && queue.length > 0) {
        const s = queue.shift()!
        if (aggMap[s.id]) continue
        active++
        setAggMap((m) => ({ ...m, [s.id]: 'loading' }))
        fetchDecisionList(s.id)
          .then((list) => {
            if (!alive) return
            setAggMap((m) => ({ ...m, [s.id]: aggregateSession(list) }))
          })
          .catch(() => {
            if (!alive) return
            setAggMap((m) => ({ ...m, [s.id]: 'error' }))
          })
          .finally(() => {
            active--
            if (alive) next()
          })
      }
    }
    next()
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessions])

  const totalDecisions = sessions.reduce((a, s) => a + s.decisions, 0)
  const totalErrors = sessions.reduce((a, s) => {
    const v = aggMap[s.id]
    return a + (v && typeof v !== 'string' ? v.errors : 0)
  }, 0)

  return (
    <div
      style={{
        flex: 1,
        padding: '14px 22px 22px',
        display: 'flex',
        flexDirection: 'column',
        gap: 14,
        minHeight: 0,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          color: C.ink3,
          fontSize: 13,
        }}
      >
        <span style={{ color: C.ink, fontWeight: 600 }}>档案 · 全部会话</span>
        <span style={{ flex: 1 }} />
        <span
          style={{
            fontSize: 11.5,
            color: C.ink3,
            display: 'inline-flex',
            gap: 12,
          }}
        >
          <span>
            共 <span className="gb-mono">{sessions.length}</span> 个 session
          </span>
          <span>
            · 总 <span className="gb-mono">{totalDecisions.toLocaleString()}</span> 决策
          </span>
          {totalErrors > 0 && (
            <span style={{ color: C.error }}>
              · <span className="gb-mono">{totalErrors}</span> 异常
            </span>
          )}
        </span>
      </div>

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
        {sessions.length === 0 ? (
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
            还没有任何 session 记录 · 启动 runner 后会自动生成
          </div>
        ) : (
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))',
              gap: 12,
            }}
          >
            {sessions.map((s) => {
              const hover = hoverId === s.id
              const v = aggMap[s.id]
              const agg = v && typeof v !== 'string' ? v : null
              const loading = v === 'loading'
              const denom = agg ? agg.successCount + agg.failCount : 0
              const successTone =
                !agg || denom === 0
                  ? C.ink
                  : agg.successRate >= 0.95
                    ? C.live
                    : agg.successRate >= 0.85
                      ? C.warn
                      : C.error
              return (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => onPick(s.id)}
                  onMouseEnter={() => setHoverId(s.id)}
                  onMouseLeave={() => setHoverId(null)}
                  style={{
                    textAlign: 'left',
                    padding: '14px 16px 12px',
                    borderRadius: 10,
                    background: C.surface,
                    border: `1px solid ${hover ? C.ink3 : C.border}`,
                    boxShadow: hover
                      ? '0 6px 20px -10px rgba(40,30,20,.15), 0 1px 0 rgba(0,0,0,.02)'
                      : '0 1px 0 rgba(0,0,0,.02)',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 10,
                    cursor: 'pointer',
                    fontFamily: 'inherit',
                    transition: 'border-color .14s, box-shadow .14s, transform .14s',
                    transform: hover ? 'translateY(-1px)' : 'none',
                  }}
                >
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'baseline',
                      justifyContent: 'space-between',
                      gap: 8,
                    }}
                  >
                    <div
                      style={{
                        fontSize: 14.5,
                        fontWeight: 600,
                        color: C.ink,
                        display: 'flex',
                        alignItems: 'center',
                        gap: 7,
                      }}
                    >
                      {s.day}
                      {s.isCurrent && (
                        <span
                          className="gb-pulse"
                          style={{
                            fontSize: 9.5,
                            color: C.live,
                            background: C.liveSoft,
                            padding: '0 5px',
                            borderRadius: 3,
                            fontWeight: 700,
                            letterSpacing: '.04em',
                          }}
                        >
                          LIVE
                        </span>
                      )}
                    </div>
                    <div style={{ fontSize: 10.5, color: C.ink4, fontFamily: C.fontMono }}>
                      {s.id}
                    </div>
                  </div>

                  {/* 3 metric cells: 决策 / 成功率 / 时长 */}
                  <div
                    style={{
                      display: 'grid',
                      gridTemplateColumns: 'repeat(3, 1fr)',
                      gap: 8,
                      background: '#faf8f3',
                      border: `1px solid ${C.borderSoft}`,
                      borderRadius: 6,
                      padding: '8px 10px',
                    }}
                  >
                    <CardMetric
                      label="决策"
                      value={s.decisions.toLocaleString()}
                      mono
                    />
                    <CardMetric
                      label="成功率"
                      value={
                        agg && denom > 0 ? `${(agg.successRate * 100).toFixed(1)}%` : '—'
                      }
                      color={successTone}
                      loading={loading && !agg}
                    />
                    <CardMetric
                      label="时长"
                      value={agg ? agg.duration : '—'}
                      mono
                      loading={loading && !agg}
                    />
                  </div>

                  {/* footer: 实例 · 异常 */}
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 10,
                      fontSize: 11,
                      color: C.ink3,
                    }}
                  >
                    <span>
                      <span className="gb-mono">
                        {agg ? agg.instances.length : '—'}
                      </span>{' '}
                      台
                    </span>
                    <span style={{ color: C.ink4 }}>·</span>
                    <span>
                      <span className="gb-mono">{agg ? agg.rounds : '—'}</span> rounds
                    </span>
                    <span style={{ flex: 1 }} />
                    {agg && agg.errors > 0 ? (
                      <span
                        style={{
                          fontSize: 10.5,
                          fontWeight: 500,
                          padding: '1px 6px',
                          borderRadius: 3,
                          color: C.error,
                          background: C.errorSoft,
                          border: `1px solid ${C.error}30`,
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: 4,
                        }}
                      >
                        <span
                          style={{
                            width: 5,
                            height: 5,
                            borderRadius: '50%',
                            background: C.error,
                          }}
                        />
                        <span className="gb-mono">{agg.errors}</span> 异常
                      </span>
                    ) : agg ? (
                      <span
                        style={{
                          fontSize: 10.5,
                          color: C.live,
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: 4,
                        }}
                      >
                        <span
                          style={{
                            width: 5,
                            height: 5,
                            borderRadius: '50%',
                            background: C.live,
                          }}
                        />
                        无异常
                      </span>
                    ) : (
                      <span style={{ fontSize: 10.5, color: C.ink4 }}>
                        {loading ? '加载…' : ''}
                      </span>
                    )}
                  </div>
                </button>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

function CardMetric({
  label,
  value,
  color,
  mono,
  loading,
}: {
  label: string
  value: string
  color?: string
  mono?: boolean
  loading?: boolean
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 1, minWidth: 0 }}>
      <span
        style={{
          fontSize: 9,
          color: C.ink4,
          fontWeight: 500,
          letterSpacing: '.06em',
          textTransform: 'uppercase',
        }}
      >
        {label}
      </span>
      <span
        className={mono ? 'gb-mono' : ''}
        style={{
          fontSize: 13,
          fontWeight: 600,
          color: loading ? C.ink4 : color || C.ink,
          lineHeight: 1.1,
          opacity: loading ? 0.4 : 1,
          transition: 'opacity .2s',
        }}
      >
        {loading ? '…' : value}
      </span>
    </div>
  )
}

// ─── back-to-overview button ────────────────────────────────────────────
function BackToOverview({ onClick }: { onClick: () => void }) {
  const [hover, setHover] = useState(false)
  return (
    <button
      type="button"
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      title="返回全部档案"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 5,
        padding: '4px 9px 4px 7px',
        borderRadius: 6,
        fontSize: 12.5,
        fontWeight: 500,
        color: hover ? C.ink : C.ink3,
        background: hover ? C.surface2 : 'transparent',
        border: `1px solid ${hover ? C.border : 'transparent'}`,
        cursor: 'pointer',
        fontFamily: 'inherit',
        transition: 'background .12s, color .12s, border-color .12s',
        lineHeight: 1.3,
      }}
    >
      <svg width="11" height="11" viewBox="0 0 11 11" fill="none">
        <path
          d="M7 2.5L3.5 5.5L7 8.5"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      全部档案
    </button>
  )
}

// ─── main ───────────────────────────────────────────────────────────────
export function ArchiveView() {
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null)
  const [sessionsErr, setSessionsErr] = useState<string | null>(null)

  const [view, setView] = useState<'overview' | 'detail'>('overview')
  const [sessionId, setSessionId] = useState<string | null>(null)

  const [decisions, setDecisions] = useState<DecisionRow[] | null>(null)
  const [decisionsErr, setDecisionsErr] = useState<string | null>(null)

  const [decisionId, setDecisionId] = useState<string | null>(null)
  const [detail, setDetail] = useState<DecisionDetail | null>(null)
  const [detailErr, setDetailErr] = useState<string | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  // initial load: sessions
  useEffect(() => {
    let alive = true
    setSessionsErr(null)
    fetchSessionList()
      .then((list) => {
        if (!alive) return
        setSessions(list)
      })
      .catch((e) => {
        if (!alive) return
        setSessionsErr(String(e))
      })
    return () => {
      alive = false
    }
  }, [])

  // load decisions when entering detail
  useEffect(() => {
    if (view !== 'detail' || !sessionId) return
    let alive = true
    setDecisions(null)
    setDecisionsErr(null)
    fetchDecisionList(sessionId)
      .then((list) => {
        if (!alive) return
        setDecisions(list)
        if (list.length > 0) setDecisionId(list[0].id)
        else setDecisionId(null)
      })
      .catch((e) => {
        if (!alive) return
        setDecisionsErr(String(e))
      })
    return () => {
      alive = false
    }
  }, [view, sessionId])

  // load detail when selecting decision
  useEffect(() => {
    if (!decisionId || !sessionId) {
      setDetail(null)
      return
    }
    let alive = true
    setDetailLoading(true)
    setDetailErr(null)
    fetchDecisionDetail(decisionId, sessionId)
      .then((d) => {
        if (!alive) return
        setDetail(d)
      })
      .catch((e) => {
        if (!alive) return
        setDetailErr(String(e))
        setDetail(null)
      })
      .finally(() => {
        if (alive) setDetailLoading(false)
      })
    return () => {
      alive = false
    }
  }, [decisionId, sessionId])

  const session = sessions?.find((s) => s.id === sessionId) || null
  const sessionAgg = useMemo(
    () => (decisions && decisions.length > 0 ? aggregateSession(decisions) : null),
    [decisions],
  )

  const [visibleTiers, setVisibleTiers] = useState<Set<string>>(
    () => new Set(['T1', 'T2', 'T3', 'T4', 'T5']),
  )
  const toggleTierVis = (k: TierKey) =>
    setVisibleTiers((prev) => {
      const n = new Set(prev)
      if (n.has(k)) n.delete(k)
      else n.add(k)
      return n
    })

  const [openMap, setOpenMap] = useState<Record<string, boolean>>({})
  const toggleOpen = (k: TierKey) => setOpenMap((p) => ({ ...p, [k]: !p[k] }))

  const [frame, setFrame] = useState<'before' | 'during' | 'after'>('during')
  useEffect(() => {
    setFrame('during')
  }, [decisionId])

  // ───────── render ─────────

  if (sessionsErr) {
    return (
      <ErrorBox
        title="无法加载会话列表"
        detail={sessionsErr}
        hint="确认后端 /api/sessions 在线"
      />
    )
  }
  if (!sessions) return <LoadingBox label="加载会话列表…" />

  if (view === 'overview') {
    return (
      <ArchiveOverview
        sessions={sessions}
        onPick={(id) => {
          setSessionId(id)
          setView('detail')
        }}
      />
    )
  }

  // detail view
  if (decisionsErr) {
    return (
      <DetailShell back={() => setView('overview')}>
        <ErrorBox
          title="无法加载该 session 的决策列表"
          detail={decisionsErr}
          hint={`session = ${sessionId}`}
        />
      </DetailShell>
    )
  }
  if (!decisions) {
    return (
      <DetailShell back={() => setView('overview')}>
        <LoadingBox label="加载决策列表…" />
      </DetailShell>
    )
  }
  if (decisions.length === 0) {
    return (
      <DetailShell back={() => setView('overview')}>
        <EmptyBox label="该 session 没有决策记录" />
      </DetailShell>
    )
  }

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
      {/* breadcrumb */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          color: C.ink3,
          fontSize: 13,
        }}
      >
        <BackToOverview onClick={() => setView('overview')} />
        <span
          style={{
            width: 1,
            height: 14,
            background: C.border,
            margin: '0 4px',
          }}
        />
        <span style={{ color: C.ink4 }}>档案</span>
        <span style={{ color: C.ink4, opacity: 0.6, padding: '0 1px' }}>›</span>
        <span style={{ color: C.ink4 }}>Session</span>
        <CrumbDropdown
          width={400}
          trigger={
            <span>
              <span className="gb-mono" style={{ color: C.ink2 }}>
                {session?.id || sessionId}
              </span>
              {session && (
                <span style={{ color: C.ink4, fontWeight: 500, marginLeft: 6 }}>
                  · {session.day}
                </span>
              )}
            </span>
          }
        >
          {(close) => (
            <SessionPopover
              active={sessionId || ''}
              sessions={sessions}
              onPick={(id) => setSessionId(id)}
              close={close}
            />
          )}
        </CrumbDropdown>
        <span style={{ flex: 1 }} />
        <span
          style={{
            fontSize: 11.5,
            color: C.ink3,
            display: 'inline-flex',
            gap: 12,
          }}
        >
          <span>
            <span className="gb-mono">{decisions.length}</span> 条决策
          </span>
          {sessionAgg && (
            <>
              <span>
                · 成功率{' '}
                <span className="gb-mono">
                  {sessionAgg.successCount + sessionAgg.failCount > 0
                    ? `${(sessionAgg.successRate * 100).toFixed(1)}%`
                    : '—'}
                </span>
              </span>
              <span>
                · 时长 <span className="gb-mono">{sessionAgg.duration}</span>
              </span>
              <span>
                · <span className="gb-mono">{sessionAgg.instances.length}</span> 台
              </span>
            </>
          )}
        </span>
      </div>

      {/* main grid */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '300px 1fr 420px',
          gap: 14,
          flex: 1,
          minHeight: 0,
        }}
      >
        <DecisionList
          decisions={decisions}
          activeId={decisionId || ''}
          onPick={setDecisionId}
        />

        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
            minHeight: 0,
          }}
        >
          <div
            style={{
              position: 'relative',
              flex: 1,
              minHeight: 0,
              overflow: 'hidden',
              borderRadius: 8,
              border: `1px solid ${C.border}`,
              background: '#0a0a08',
            }}
          >
            {detailLoading && !detail && (
              <div
                style={{
                  position: 'absolute',
                  inset: 0,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: 'rgba(255,255,255,.5)',
                  fontSize: 12,
                }}
              >
                加载决策详情…
              </div>
            )}
            {detailErr && !detail && (
              <div
                style={{
                  position: 'absolute',
                  inset: 0,
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: 6,
                  color: 'rgba(255,255,255,.7)',
                  fontSize: 12,
                  padding: 20,
                  textAlign: 'center',
                }}
              >
                <div>无法加载决策详情</div>
                <div style={{ fontSize: 11, color: 'rgba(255,255,255,.4)' }}>
                  {detailErr}
                </div>
              </div>
            )}
            {detail && (
              <>
                <ArchiveSnapshotForDecision
                  detail={detail}
                  frame={frame}
                  visibleTiers={visibleTiers}
                />
                <div style={{ position: 'absolute', top: 10, right: 10 }}>
                  <TierLegend visible={visibleTiers} onToggle={toggleTierVis} />
                </div>
                <div
                  style={{
                    position: 'absolute',
                    left: '50%',
                    bottom: 12,
                    transform: 'translateX(-50%)',
                    display: 'flex',
                    background: 'rgba(20,20,18,.78)',
                    backdropFilter: 'blur(6px)',
                    borderRadius: 6,
                    padding: 3,
                    gap: 1,
                  }}
                >
                  {(
                    [
                      ['before', '决策前'],
                      ['during', '决策时刻'],
                      ['after', '决策后'],
                    ] as const
                  ).map(([k, l]) => {
                    const on = frame === k
                    const disabled =
                      (k === 'before' && !detail.imageBefore) ||
                      (k === 'after' && !detail.imageAfter)
                    return (
                      <button
                        key={k}
                        type="button"
                        onClick={() => !disabled && setFrame(k)}
                        disabled={disabled}
                        style={{
                          padding: '5px 12px',
                          borderRadius: 4,
                          fontFamily: C.fontMono,
                          fontSize: 11,
                          fontWeight: 600,
                          color: on
                            ? '#1a1814'
                            : disabled
                              ? 'rgba(255,255,255,.3)'
                              : 'rgba(255,255,255,.78)',
                          background: on ? '#f0eee9' : 'transparent',
                          letterSpacing: '.02em',
                          border: 'none',
                          cursor: disabled ? 'not-allowed' : 'pointer',
                        }}
                      >
                        {l}
                      </button>
                    )
                  })}
                </div>
                <div
                  className="gb-mono"
                  style={{
                    position: 'absolute',
                    left: 12,
                    bottom: 12,
                    padding: '4px 8px',
                    borderRadius: 4,
                    background: 'rgba(20,20,18,.78)',
                    backdropFilter: 'blur(6px)',
                    fontSize: 10.5,
                    color: 'rgba(255,255,255,.72)',
                    display: 'flex',
                    gap: 10,
                    alignItems: 'center',
                  }}
                >
                  <span style={{ color: '#fff', fontWeight: 600 }}>
                    {detail.inputW}×{detail.inputH}
                  </span>
                  <span>·</span>
                  <span>{detail.phaseLabel}</span>
                  <span>·</span>
                  <span>{detail.ts}</span>
                </div>
              </>
            )}
          </div>
        </div>

        {/* right: KPI + accordion */}
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 10,
            minHeight: 0,
          }}
        >
          {detail ? (
            <>
              <KpiStripVertical decision={detail} />
              <div
                className="gb-scroll"
                style={{
                  background: C.surface,
                  border: `1px solid ${C.border}`,
                  borderRadius: 8,
                  overflow: 'hidden auto',
                  flex: 1,
                }}
              >
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    padding: '10px 14px',
                    borderBottom: `1px solid ${C.borderSoft}`,
                    background: C.surface2,
                  }}
                >
                  <span style={{ fontSize: 12, fontWeight: 600, color: C.ink2 }}>
                    5 层证据链
                  </span>
                  <span
                    style={{ fontSize: 10.5, color: C.ink4, fontFamily: C.fontMono }}
                  >
                    T1 → T5 · 自上而下
                  </span>
                  <span style={{ flex: 1 }} />
                  <button
                    type="button"
                    onClick={() =>
                      setOpenMap((o) => {
                        const allOpen =
                          Object.values(o).filter(Boolean).length === 5
                        if (allOpen) return {}
                        const all: Record<string, boolean> = {}
                        ;(['T1', 'T2', 'T3', 'T4', 'T5'] as const).forEach((k) => {
                          all[k] = true
                        })
                        return all
                      })
                    }
                    style={{
                      fontSize: 11,
                      color: C.ink3,
                      padding: '2px 6px',
                      borderRadius: 4,
                      background: 'transparent',
                      border: 'none',
                      cursor: 'pointer',
                      fontFamily: 'inherit',
                    }}
                  >
                    {Object.values(openMap).filter(Boolean).length === 5
                      ? '全部收起'
                      : '全部展开'}
                  </button>
                </div>
                {detail.evidence.map((ev) => (
                  <TierRow
                    key={ev.tier}
                    ev={ev}
                    openMap={openMap}
                    onToggle={toggleOpen}
                    visibleTiers={visibleTiers}
                    onTierVisToggle={toggleTierVis}
                  />
                ))}
              </div>
            </>
          ) : (
            <div
              style={{
                background: C.surface,
                border: `1px solid ${C.border}`,
                borderRadius: 8,
                padding: 30,
                textAlign: 'center',
                fontSize: 12,
                color: C.ink4,
              }}
            >
              {detailLoading ? '加载证据链…' : '选择左侧决策查看详情'}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ─── helpers ────────────────────────────────────────────────────────────

function ArchiveSnapshotForDecision({
  detail,
  frame,
  visibleTiers,
}: {
  detail: DecisionDetail
  frame: 'before' | 'during' | 'after'
  visibleTiers: Set<string>
}) {
  const src =
    frame === 'before'
      ? detail.imageBefore || detail.inputImage
      : frame === 'after'
        ? detail.imageAfter || detail.inputImage
        : detail.inputImage
  const evidence = frame === 'during' ? detail.evidence : []
  const label =
    frame === 'during'
      ? `决策 #${detail.id} · ${detail.target}`
      : frame === 'before'
        ? `决策前`
        : `决策后`
  return (
    <ArchiveSnapshot
      src={src}
      seed={detail.created}
      phase={detail.phase}
      evidence={evidence}
      visibleTiers={visibleTiers}
      width={detail.inputW}
      height={detail.inputH}
      label={label}
    />
  )
}

import { ArchiveSnapshot } from './ArchiveSnapshot'

function DetailShell({
  back,
  children,
}: {
  back: () => void
  children: React.ReactNode
}) {
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
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          color: C.ink3,
          fontSize: 13,
        }}
      >
        <BackToOverview onClick={back} />
      </div>
      {children}
    </div>
  )
}

function LoadingBox({ label }: { label: string }) {
  return (
    <div
      style={{
        flex: 1,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        color: C.ink4,
        fontSize: 13,
      }}
    >
      {label}
    </div>
  )
}

function ErrorBox({
  title,
  detail,
  hint,
}: {
  title: string
  detail: string
  hint?: string
}) {
  return (
    <div
      style={{
        flex: 1,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 30,
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
        <div style={{ fontSize: 13, fontWeight: 600, color: C.error }}>{title}</div>
        <div
          className="gb-mono"
          style={{ fontSize: 11.5, color: C.ink3, wordBreak: 'break-word' }}
        >
          {detail}
        </div>
        {hint && <div style={{ fontSize: 11, color: C.ink4 }}>{hint}</div>}
      </div>
    </div>
  )
}

function EmptyBox({ label }: { label: string }) {
  return (
    <div
      style={{
        flex: 1,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 60,
        textAlign: 'center',
        fontSize: 13,
        color: C.ink4,
        background: C.surface,
        border: `1px dashed ${C.border}`,
        borderRadius: 8,
      }}
    >
      {label}
    </div>
  )
}

export { decisionImgSrc }
