/**
 * StandbyState — 待启动态.
 *
 * 非 dev: 默认 HeroSplash → 用户点 "先看看" 进 reveal 模式 (ReadyCard + PreviewPanel)
 * dev:   跳过 hero, 直接 ReadyCard + PreviewPanel + PhaseTester (含 testerSel 联动)
 *
 * 来源: states.jsx StandbyState (71-163)
 */

import { useEffect, useMemo, useState } from 'react'
import { C } from '@/lib/design-tokens'
import { useAppStore, type AccountAssignment, type Instance } from '@/lib/store'
import { PhaseTester } from './PhaseTester'
import { HeroSplash } from './HeroSplash'
import { ReadyCard } from './ReadyCard'
import { PreviewPanel } from './PreviewPanel'

function greetByHour(): string {
  const h = new Date().getHours()
  if (h < 6) return '夜深了'
  if (h < 12) return '早上好'
  if (h < 18) return '下午好'
  return '晚上好'
}

export function StandbyState({
  accounts,
  onStart,
  onSettings,
}: {
  accounts: AccountAssignment[]
  onStart: () => void
  onSettings: () => void
}) {
  const devMode = useAppStore((s) => s.devMode)
  const emulators = useAppStore((s) => s.emulators)
  // TUN-only: /api/tun/state 拉本地 wintun + gameproxy 状态.
  // running && ok = TUN 模式; 其他都视为 OFF (失联/未启).
  const [tunMode, setTunMode] = useState<'TUN' | 'OFF'>('OFF')
  const refetchTun = async () => {
    try {
      const r = await fetch('/api/tun/state')
      if (!r.ok) {
        setTunMode('OFF')
        return
      }
      const j = await r.json()
      // gameproxy schema: { ok, mode: 'tun'|'offline', uptime_seconds, counters }
      const isTun =
        j && j.ok && (j.mode === 'tun' || (j.uptime_seconds || 0) > 0)
      setTunMode(isTun ? 'TUN' : 'OFF')
    } catch {
      setTunMode('OFF')
    }
  }
  useEffect(() => {
    let alive = true
    const tick = () => alive && refetchTun()
    tick()
    const t = setInterval(tick, 30_000)
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [])

  const handleAccelToggle = async () => {
    const path = tunMode === 'OFF' ? '/api/tun/start' : '/api/tun/stop'
    try {
      await fetch(path, { method: 'POST' })
    } catch {
      /* ignore — refetch will reflect actual state */
    }
    await refetchTun()
  }

  // 派生 instance shape (从 accounts)
  const instances = useMemo<Instance[]>(
    () =>
      accounts.map((a) => ({
        index: a.index,
        group: a.group,
        role: a.role,
        nickname: a.nickname || `玩家${a.index}`,
        state: 'init',
        error: '',
        stateDuration: 0,
        adbSerial: a.adbSerial,
      })),
    [accounts],
  )

  const greet = greetByHour()
  const teamCount = useMemo(
    () => new Set(instances.map((i) => i.group)).size,
    [instances],
  )
  // 真实 running 数; 没有 running 时返回 0 (不再 fallback 到 instances.length, 那是 demo bug).
  const emuReady = useMemo(
    () => emulators.filter((e) => e.running).length,
    [emulators],
  )

  // PhaseTester 选中 — 复用 store.selectedInstances (主页 running 状态也用同字段)
  const selectedInstances = useAppStore((s) => s.selectedInstances)
  const toggleInstanceSelection = useAppStore((s) => s.toggleInstanceSelection)
  // 默认选前 3 (设计原型): standby 进 dev 模式时, 若 store 为空则 init
  useState(() => {
    if (devMode && selectedInstances.length === 0 && instances.length >= 3) {
      useAppStore.setState({ selectedInstances: [0, 1, 2] })
    }
  })
  const testerSel = selectedInstances
  const toggleTester = (idx: number) => toggleInstanceSelection(idx)

  // hero 默认开 (非 dev); dev 跳过
  const [heroMode, setHeroMode] = useState(!devMode)

  if (heroMode && !devMode) {
    return (
      <HeroSplash
        greet={greet}
        count={instances.length}
        teamCount={teamCount}
        accel={tunMode}
        instances={instances}
        onStart={() => onStart()}
        onReveal={() => setHeroMode(false)}
      />
    )
  }

  // reveal / dev 共享: ReadyCard + PreviewPanel + (dev) PhaseTester
  return (
    <div
      style={{
        flex: 1,
        display: 'flex',
        flexDirection: 'column',
        background: C.bg,
        overflow: 'hidden',
      }}
    >
      <div
        className="gb-scroll"
        style={{ flex: 1, overflowY: 'auto' }}
      >
        {!devMode && (
          <div style={{ padding: '14px 28px 0' }}>
            <button
              type="button"
              onClick={() => setHeroMode(true)}
              style={{
                fontSize: 11.5,
                color: C.ink3,
                background: 'transparent',
                border: 'none',
                cursor: 'pointer',
                fontFamily: 'inherit',
                padding: '4px 0',
              }}
            >
              ← 回封面
            </button>
          </div>
        )}

        {/* HERO grid */}
        <div
          style={{
            padding: '20px 28px 24px',
            display: 'grid',
            gridTemplateColumns: 'minmax(360px, 460px) 1fr',
            gap: 20,
            alignItems: 'stretch',
          }}
        >
          <ReadyCard
            greet={greet}
            count={instances.length}
            teamCount={teamCount}
            emuReady={emuReady}
            accel={tunMode}
            onStart={onStart}
            onAccelToggle={handleAccelToggle}
          />

          <PreviewPanel
            instances={instances}
            emulators={emulators}
            testerSel={devMode ? testerSel : null}
            onToggleTester={devMode ? toggleTester : null}
          />
        </div>

        {/* below hero: dev → PhaseTester; non-dev → 设置入口 (简化掉 StandbySupport) */}
        {devMode ? (
          <div style={{ padding: '0 28px 28px' }}>
            <PhaseTester />
          </div>
        ) : (
          <div
            style={{
              padding: '0 28px 28px',
              fontSize: 11.5,
              color: C.ink4,
              textAlign: 'right',
            }}
          >
            <button
              type="button"
              onClick={onSettings}
              style={{
                background: 'transparent',
                border: 'none',
                cursor: 'pointer',
                color: C.ink3,
                fontSize: 12,
                fontFamily: 'inherit',
                padding: 0,
              }}
            >
              去设置 →
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
