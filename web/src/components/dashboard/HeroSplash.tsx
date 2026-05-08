/**
 * HeroSplash — 待启动态 (非 dev) 全屏 hero + scope picker.
 * 完全照搬 /tmp/design_dl/gameautomation/project/states.jsx HeroSplash (170-563)
 * 跟 design 像素一致, 用 inline style.
 */

import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { C } from '@/lib/design-tokens'
import type { Instance, TeamGroup } from '@/lib/store'

const HERO_TEAM_TINT: Record<string, string> = {
  A: '#7c8ff5',
  B: '#5db8a4',
  C: '#d28a5c',
  D: '#b87dc8',
  E: '#c4a850',
  F: '#7da7d9',
}

interface TeamGroupData {
  key: TeamGroup
  members: Instance[]
}

const GROUPS: Array<{ id: 'g1' | 'g2' | 'g3'; label: string; teams: TeamGroup[] }> = [
  { id: 'g1', label: '大组 1', teams: ['A', 'B'] },
  { id: 'g2', label: '大组 2', teams: ['C', 'D'] },
  { id: 'g3', label: '大组 3', teams: ['E', 'F'] },
]

export function HeroSplash({
  greet,
  count,
  teamCount,
  accel,
  instances,
  onStart,
  onReveal,
}: {
  greet: string
  count: number
  teamCount: number
  accel: 'TUN' | 'OFF'
  instances: Instance[]
  onStart: (picked: string[], effectiveTeams: TeamGroup[]) => void
  onReveal: () => void
}) {
  const [step, setStep] = useState<'splash' | 'scope'>('splash')
  const [picked, setPicked] = useState<string[]>(['all'])

  const teams: TeamGroupData[] = useMemo(() => {
    const m: Partial<Record<TeamGroup, Instance[]>> = {}
    instances.forEach((i) => {
      ;(m[i.group] = m[i.group] || []).push(i)
    })
    return Object.entries(m).map(([k, v]) => ({
      key: k as TeamGroup,
      members: (v ?? []).sort(
        (a, b) => Number(b.role === 'captain') - Number(a.role === 'captain'),
      ),
    }))
  }, [instances])

  const allTeamKeys = teams.map((t) => t.key)

  const effectiveTeams: TeamGroup[] = useMemo(() => {
    if (picked.includes('all')) return allTeamKeys
    const set = new Set<TeamGroup>()
    picked.forEach((id) => {
      const g = GROUPS.find((x) => x.id === id)
      if (g) g.teams.forEach((t) => allTeamKeys.includes(t) && set.add(t))
    })
    return [...set]
  }, [picked, allTeamKeys])

  const targetCount = instances.filter((i) => effectiveTeams.includes(i.group)).length

  const togglePick = (id: string) => {
    setPicked((prev) => {
      if (id === 'all') return ['all']
      const without = prev.filter((p) => p !== 'all' && p !== id)
      const next = prev.includes(id) ? without : [...without, id]
      const all3 = ['g1', 'g2', 'g3'].every((g) => next.includes(g))
      return all3 ? ['all'] : next.length === 0 ? ['all'] : next
    })
  }

  const fireStart = () => onStart(picked, effectiveTeams)

  // ⌘/Ctrl+Enter advance: splash → scope, scope → start
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        e.preventDefault()
        if (step === 'splash') setStep('scope')
        else if (targetCount > 0) fireStart()
      } else if (e.key === 'Escape' && step === 'scope') {
        setStep('splash')
      }
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, picked, targetCount])

  return (
    <div
      style={{
        flex: 1,
        position: 'relative',
        overflow: 'hidden',
        background: C.bg,
      }}
    >
      <style
        dangerouslySetInnerHTML={{
          __html: HERO_KEYFRAMES,
        }}
      />

      {/* dot grid background (slow drift) */}
      <div
        aria-hidden
        style={{
          position: 'absolute',
          inset: '-3%',
          backgroundImage: `radial-gradient(${C.border} 1px, transparent 1px)`,
          backgroundSize: '28px 28px',
          opacity: 0.35,
          animation: 'gbHDrift 30s ease-in-out infinite',
          pointerEvents: 'none',
        }}
      />

      {/* orbital pattern (right side) */}
      <HeroOrbitsRefined teams={teams} />

      {/* content frame */}
      <div
        style={{
          position: 'relative',
          zIndex: 2,
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
          padding: 'clamp(28px, 5vw, 64px) clamp(36px, 8vw, 96px)',
          gap: 24,
        }}
      >
        {step === 'splash' ? (
          <SplashStep
            greet={greet}
            count={count}
            teamCount={teamCount}
            accel={accel}
            teamKeys={teams.map((t) => t.key)}
            onNext={() => setStep('scope')}
            onReveal={onReveal}
          />
        ) : (
          <ScopeStep
            instances={instances}
            allTeamKeys={allTeamKeys}
            picked={picked}
            togglePick={togglePick}
            effectiveTeams={effectiveTeams}
            targetCount={targetCount}
            onBack={() => setStep('splash')}
            onStart={fireStart}
          />
        )}
      </div>
    </div>
  )
}

// ── splash step ──────────────────────────────────────────────────────────
function SplashStep({
  greet,
  count,
  teamCount,
  accel,
  teamKeys,
  onNext,
  onReveal,
}: {
  greet: string
  count: number
  teamCount: number
  accel: 'TUN' | 'OFF'
  teamKeys: string[]
  onNext: () => void
  onReveal: () => void
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 26, maxWidth: 980 }}>
      <div className="gbh-rise-1" style={{ fontSize: 14, color: C.ink2 }}>
        {greet}
        <span style={{ color: C.ink4 }}> · {count} 台模拟器静候指令</span>
      </div>

      <div className="gbh-rise-2">
        <h1
          className="gbh-kern"
          style={{
            fontSize: 'clamp(96px, 16vw, 220px)',
            fontWeight: 500,
            lineHeight: 0.92,
            letterSpacing: '-.04em',
            color: C.ink,
            margin: 0,
          }}
        >
          <span>开</span>
          <span style={{ display: 'inline-block', width: '0.06em' }} />
          <span>工</span>
        </h1>
      </div>

      <div
        className="gbh-rise-3"
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 10,
          alignItems: 'center',
        }}
      >
        <StatusPill>
          <span
            className="gb-pulse"
            style={{
              width: 6,
              height: 6,
              borderRadius: '50%',
              background: C.live,
            }}
          />
          <span style={{ color: C.ink, fontWeight: 600 }}>{count}</span>
          <span style={{ color: C.ink3 }}>实例待命</span>
        </StatusPill>
        <StatusPill>
          <span style={{ color: C.ink, fontWeight: 600 }}>{teamCount}</span>
          <span style={{ color: C.ink3 }}>队</span>
          <span
            style={{
              color: C.ink4,
              fontFamily: C.fontMono,
              fontSize: 11.5,
              letterSpacing: '.04em',
            }}
          >
            {teamKeys.join('·')}
          </span>
        </StatusPill>
        <StatusPill warn={accel === 'OFF'}>
          <span
            style={{
              width: 6,
              height: 6,
              borderRadius: '50%',
              background: accel === 'OFF' ? C.ink4 : '#d97757',
            }}
          />
          <span
            style={{
              color: accel === 'OFF' ? C.ink3 : C.ink,
              fontWeight: 600,
            }}
          >
            {accel === 'OFF' ? '加速器未启用' : `${accel} 加速`}
          </span>
        </StatusPill>
      </div>

      {/* CTA */}
      <div
        className="gbh-rise-4"
        style={{
          marginTop: 12,
          display: 'flex',
          alignItems: 'center',
          gap: 18,
          flexWrap: 'wrap',
        }}
      >
        <button
          type="button"
          onClick={onNext}
          className="gbh-cta"
          style={{
            padding: '16px 26px',
            borderRadius: 12,
            border: 'none',
            background: 'linear-gradient(180deg, #e8845f 0%, #c66845 100%)',
            color: '#fff',
            fontWeight: 700,
            fontSize: 15.5,
            fontFamily: 'inherit',
            letterSpacing: '.02em',
            cursor: 'pointer',
            display: 'inline-flex',
            alignItems: 'center',
            gap: 12,
            animation: 'gbHCtaPulse 3.4s ease-in-out infinite',
            minWidth: 240,
          }}
        >
          <span>开始今天的工作</span>
          <span aria-hidden style={{ flex: 1 }} />
          <span
            style={{
              fontFamily: C.fontMono,
              fontWeight: 700,
              fontSize: 13,
              padding: '3px 9px',
              borderRadius: 6,
              background: 'rgba(255,255,255,.18)',
            }}
          >
            {count} 台
          </span>
          <span aria-hidden style={{ fontSize: 14 }}>
            →
          </span>
        </button>
        <span style={{ fontSize: 12, color: C.ink4 }}>下一步选择启动范围</span>
        <span aria-hidden style={{ flex: 1 }} />
        <button
          type="button"
          onClick={onReveal}
          style={{
            background: 'transparent',
            border: 'none',
            cursor: 'pointer',
            color: C.ink3,
            fontFamily: 'inherit',
            fontSize: 12.5,
            padding: '8px 0',
            textDecoration: 'underline',
            textDecorationColor: C.borderSoft,
            textUnderlineOffset: 3,
          }}
        >
          先看看 {count} 台 →
        </button>
      </div>

      <div
        className="gbh-fade-late"
        style={{
          fontSize: 11.5,
          color: C.ink4,
          display: 'inline-flex',
          gap: 10,
          alignItems: 'center',
        }}
      >
        <HKbd>⌘</HKbd>
        <HKbd>↵</HKbd>
        <span>快捷启动</span>
      </div>
    </div>
  )
}

// ── scope step ──────────────────────────────────────────────────────────
function ScopeStep({
  instances,
  allTeamKeys,
  picked,
  togglePick,
  effectiveTeams,
  targetCount,
  onBack,
  onStart,
}: {
  instances: Instance[]
  allTeamKeys: TeamGroup[]
  picked: string[]
  togglePick: (id: string) => void
  effectiveTeams: TeamGroup[]
  targetCount: number
  onBack: () => void
  onStart: () => void
}) {
  const cards = [
    { id: 'all', label: '全部', teams: allTeamKeys, hint: '满载并发' },
    ...GROUPS.map((g) => ({ ...g, hint: g.teams.join(' · ') + ' 队' })),
  ]
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 26, maxWidth: 1020 }}>
      <div
        className="gbh-rise-1"
        style={{ display: 'flex', alignItems: 'center', gap: 14 }}
      >
        <button
          type="button"
          onClick={onBack}
          style={{
            background: 'transparent',
            border: 'none',
            cursor: 'pointer',
            color: C.ink3,
            fontSize: 13,
            fontFamily: 'inherit',
            padding: 0,
          }}
        >
          ← 返回
        </button>
        <span style={{ fontSize: 12.5, color: C.ink3 }}>选择启动范围</span>
        <span aria-hidden style={{ flex: 1 }} />
        <span style={{ fontSize: 12, color: C.ink4 }}>可多选, 点击切换</span>
      </div>

      <h2
        className="gbh-rise-2"
        style={{
          fontSize: 'clamp(40px, 6vw, 72px)',
          fontWeight: 500,
          lineHeight: 0.98,
          letterSpacing: '-.03em',
          color: C.ink,
          margin: 0,
        }}
      >
        要跑哪几组?
      </h2>

      <div
        className="gbh-rise-3"
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
          gap: 12,
        }}
      >
        {cards.map((g, gi) => {
          const matches = instances.filter((i) =>
            (g.teams as TeamGroup[]).includes(i.group),
          )
          const cps = matches.filter((i) => i.role === 'captain').length
          const empty = matches.length === 0
          const on = picked.includes(g.id)
          return (
            <button
              key={g.id}
              type="button"
              disabled={empty}
              onClick={() => togglePick(g.id)}
              className={empty ? '' : 'gbh-gcard'}
              style={{
                textAlign: 'left',
                fontFamily: 'inherit',
                cursor: empty ? 'not-allowed' : 'pointer',
                padding: '18px 18px 16px',
                borderRadius: 12,
                border: `1.5px solid ${on ? '#d97757' : C.border}`,
                background: on
                  ? 'rgba(217,119,87,.06)'
                  : empty
                    ? 'transparent'
                    : C.surface,
                opacity: empty ? 0.4 : 1,
                display: 'flex',
                flexDirection: 'column',
                gap: 12,
                animation: `gbHRise .5s ${0.15 + gi * 0.05}s cubic-bezier(.16,1,.3,1) both`,
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <span
                  aria-hidden
                  style={{
                    width: 18,
                    height: 18,
                    borderRadius: 5,
                    border: `1.5px solid ${on ? '#d97757' : C.border}`,
                    background: on ? '#d97757' : 'transparent',
                    display: 'inline-flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    flex: '0 0 auto',
                  }}
                >
                  {on && (
                    <svg width="11" height="11" viewBox="0 0 11 11">
                      <path
                        d="M2 5.5L4.5 8L9 3"
                        fill="none"
                        stroke="#fff"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  )}
                </span>
                <span
                  style={{
                    fontSize: 18,
                    fontWeight: 600,
                    color: C.ink,
                    letterSpacing: '-.01em',
                  }}
                >
                  {g.label}
                </span>
                <span aria-hidden style={{ flex: 1 }} />
                <span
                  className="gb-mono"
                  style={{
                    fontSize: 12,
                    color: C.ink4,
                    letterSpacing: '.02em',
                  }}
                >
                  {g.teams.length ? g.teams.join('·') : '—'}
                </span>
              </div>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                <span
                  className="gb-mono"
                  style={{
                    fontSize: 32,
                    fontWeight: 600,
                    color: empty ? C.ink4 : C.ink,
                    lineHeight: 1,
                    letterSpacing: '-.02em',
                  }}
                >
                  {matches.length}
                </span>
                <span style={{ fontSize: 12, color: C.ink3 }}>实例</span>
                <span aria-hidden style={{ flex: 1 }} />
                <span style={{ fontSize: 11.5, color: C.ink4 }}>
                  {empty ? '空' : `${cps} 队长 · ${matches.length - cps} 队员`}
                </span>
              </div>
              <div style={{ fontSize: 12, color: C.ink4 }}>
                {empty ? '该大组当前没有实例' : g.hint}
              </div>
            </button>
          )
        })}
      </div>

      {/* combined preview + start */}
      <div
        className="gbh-rise-4"
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 18,
          padding: '16px 18px',
          borderRadius: 12,
          border: `1px solid ${C.border}`,
          background: C.surface,
          flexWrap: 'wrap',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
          <span
            className="gb-mono"
            style={{
              fontSize: 28,
              fontWeight: 700,
              color: C.ink,
              lineHeight: 1,
              letterSpacing: '-.02em',
            }}
          >
            {targetCount}
          </span>
          <span style={{ fontSize: 12.5, color: C.ink3 }}>台将启动</span>
        </div>
        <span
          aria-hidden
          style={{ width: 1, height: 24, background: C.borderSoft }}
        />
        <div
          style={{
            fontSize: 12.5,
            color: C.ink3,
            display: 'inline-flex',
            gap: 6,
            flexWrap: 'wrap',
          }}
        >
          <span style={{ color: C.ink4 }}>覆盖</span>
          {effectiveTeams.length > 0 ? (
            effectiveTeams.map((t) => (
              <span
                key={t}
                style={{
                  fontFamily: C.fontMono,
                  fontWeight: 700,
                  padding: '1px 6px',
                  borderRadius: 4,
                  background: '#fff',
                  border: `1px solid ${C.border}`,
                  color: C.ink2,
                }}
              >
                {t}
              </span>
            ))
          ) : (
            <span style={{ color: C.ink4 }}>—</span>
          )}
        </div>
        <span aria-hidden style={{ flex: 1 }} />
        <button
          type="button"
          onClick={onStart}
          disabled={targetCount === 0}
          style={{
            padding: '12px 22px',
            borderRadius: 10,
            border: 'none',
            background:
              targetCount === 0
                ? C.borderSoft
                : 'linear-gradient(180deg, #e8845f 0%, #c66845 100%)',
            color: '#fff',
            fontWeight: 700,
            fontSize: 14,
            fontFamily: 'inherit',
            letterSpacing: '.02em',
            cursor: targetCount === 0 ? 'not-allowed' : 'pointer',
            display: 'inline-flex',
            alignItems: 'center',
            gap: 10,
            opacity: targetCount === 0 ? 0.5 : 1,
          }}
        >
          <span>启动选定 {targetCount} 台</span>
          <span aria-hidden>→</span>
        </button>
      </div>

      <div
        className="gbh-rise-5"
        style={{
          display: 'flex',
          gap: 16,
          fontSize: 11.5,
          color: C.ink4,
          alignItems: 'center',
        }}
      >
        <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
          <HKbd>⌘</HKbd>
          <HKbd>↵</HKbd>
          <span>启动</span>
        </span>
        <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
          <HKbd>esc</HKbd>
          <span>返回</span>
        </span>
      </div>
    </div>
  )
}

// ── helpers ──────────────────────────────────────────────────────────────
function StatusPill({ children, warn }: { children: ReactNode; warn?: boolean }) {
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 8,
        padding: '7px 12px',
        borderRadius: 999,
        border: `1px solid ${warn ? '#f0c89a' : C.border}`,
        background: warn ? '#fff7ed' : C.surface,
        fontSize: 12.5,
      }}
    >
      {children}
    </span>
  )
}

function HKbd({ children }: { children: ReactNode }) {
  return (
    <span
      className="gb-mono"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        minWidth: 18,
        height: 18,
        padding: '0 5px',
        borderRadius: 4,
        background: 'rgba(255,255,255,.6)',
        border: `1px solid ${C.borderSoft}`,
        fontSize: 10.5,
        color: C.ink2,
        fontWeight: 600,
      }}
    >
      {children}
    </span>
  )
}

function HeroOrbitsRefined({ teams }: { teams: TeamGroupData[] }) {
  return (
    <div
      aria-hidden
      className="gbh-fade-late"
      style={{
        position: 'absolute',
        inset: 0,
        pointerEvents: 'none',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'flex-end',
        paddingRight: 'clamp(24px, 6vw, 88px)',
        animation: 'gbHGlowSlow 8s ease-in-out infinite, gbHFade 1.2s 1.2s both',
      }}
    >
      <svg
        width="540"
        height="540"
        viewBox="-270 -270 540 540"
        style={{ overflow: 'visible', maxWidth: '50%', opacity: 0.65 }}
      >
        {[80, 130, 190, 240].map((r, i) => (
          <circle
            key={i}
            cx="0"
            cy="0"
            r={r}
            fill="none"
            stroke={C.borderSoft}
            strokeOpacity={i === 1 ? 0.9 : 0.55}
            strokeWidth={i === 1 ? 1 : 0.6}
            strokeDasharray={i % 2 ? '2 6' : 'none'}
          />
        ))}
        {teams.slice(0, 4).map((t, ti) => {
          const tint = HERO_TEAM_TINT[t.key] || '#888'
          const r = 130 + ti * 22
          const start = ti * 90 - 90
          return (
            <g
              key={t.key}
              style={{
                transformOrigin: '0 0',
                animation: `${ti % 2 ? 'gbHSpinR' : 'gbHSpin'} ${80 + ti * 18}s linear infinite`,
              }}
            >
              <g transform={`rotate(${start})`}>
                <g transform={`translate(${r} 0)`}>
                  <circle r="4" fill={tint} />
                  <circle r="9" fill="none" stroke={tint} strokeOpacity=".4" />
                  <text
                    x="14"
                    y="4"
                    fontSize="11"
                    fontFamily="ui-monospace, monospace"
                    fontWeight="700"
                    fill={tint}
                    opacity=".7"
                    letterSpacing=".05em"
                  >
                    {t.key}
                  </text>
                </g>
              </g>
            </g>
          )
        })}
        <circle cx="0" cy="0" r="3" fill={C.ink} opacity=".5" />
        <line
          x1="-12"
          y1="0"
          x2="12"
          y2="0"
          stroke={C.ink}
          strokeOpacity=".25"
          strokeWidth=".5"
        />
        <line
          x1="0"
          y1="-12"
          x2="0"
          y2="12"
          stroke={C.ink}
          strokeOpacity=".25"
          strokeWidth=".5"
        />
      </svg>
    </div>
  )
}

const HERO_KEYFRAMES = `
@keyframes gbHRise {
  0%   { opacity: 0; transform: translateY(12px); }
  100% { opacity: 1; transform: translateY(0); }
}
@keyframes gbHKern {
  0%   { letter-spacing: .22em; opacity: 0; }
  100% { letter-spacing: -.04em; opacity: 1; }
}
@keyframes gbHSpin { to { transform: rotate(360deg); } }
@keyframes gbHSpinR { to { transform: rotate(-360deg); } }
@keyframes gbHDrift {
  0%, 100% { transform: translate(-1%, -1%); }
  50%      { transform: translate( 1%,  1%); }
}
@keyframes gbHFade { 0% { opacity: 0; } 100% { opacity: 1; } }
@keyframes gbHGlowSlow {
  0%, 100% { opacity: .55; } 50% { opacity: .85; }
}
@keyframes gbHCtaPulse {
  0%, 100% { box-shadow: 0 6px 22px rgba(217,119,87,.22), 0 0 0 0 rgba(217,119,87,.35); }
  50%      { box-shadow: 0 8px 28px rgba(217,119,87,.28), 0 0 0 12px rgba(217,119,87,0); }
}
.gbh-rise-1 { animation: gbHRise .65s   .05s cubic-bezier(.16,1,.3,1) both; }
.gbh-rise-2 { animation: gbHRise .7s    .20s cubic-bezier(.16,1,.3,1) both; }
.gbh-rise-3 { animation: gbHRise .8s    .30s cubic-bezier(.16,1,.3,1) both; }
.gbh-kern   { animation: gbHKern 1.2s   .30s cubic-bezier(.16,1,.3,1) both; }
.gbh-rise-4 { animation: gbHRise .6s    .55s cubic-bezier(.16,1,.3,1) both; }
.gbh-rise-5 { animation: gbHRise .6s    .75s cubic-bezier(.16,1,.3,1) both; }
.gbh-fade-late { animation: gbHFade .8s 1.0s both; }
.gbh-cta:hover { animation-play-state: paused; transform: translateY(-1px); }
.gbh-cta { transition: transform .15s; }
.gbh-gcard { transition: border-color .2s, background .2s, transform .15s; }
.gbh-gcard:hover { transform: translateY(-1px); }
`
