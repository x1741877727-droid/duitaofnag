/**
 * 实例总览 — 紧凑卡片网格.
 * 每卡:
 *   ① 实例号 + 状态色点 + 当前阶段
 *   ② 最近 50 帧动作色块条 (绿✓/红✗/橙⚠/灰○/蓝●)
 *   ③ 决策计数
 * 点卡 → 切聚焦.
 *
 * 风格: 用 token; 状态色 success/warning/destructive (米白底 + 森林绿)
 */
import { useMemo } from 'react'
import { useAppStore, stateConfig, type InstanceState, type LiveDecisionEvent } from '@/lib/store'
import { cn } from '@/lib/utils'

const PHASE_LABEL: Record<string, string> = {
  P0: '加速器', P1: '启动游戏', P2: '清弹窗',
  P3a: '队长建队', P3b: '队员加入', P4: '选地图',
}

// 状态 → 文字色 (token-based)
const STATE_TEXT: Record<string, string> = {
  idle_running: 'text-foreground',
  idle_stopped: 'text-muted-foreground',
  init: 'text-muted-foreground',
  accelerator: 'text-info',
  launch_game: 'text-info',
  wait_login: 'text-warning',
  dismiss_popups: 'text-warning',
  lobby: 'text-success',
  team_create: 'text-info',
  team_join: 'text-info',
  map_setup: 'text-info',
  ready: 'text-success',
  in_game: 'text-success',
  done: 'text-success',
  error: 'text-destructive',
}

const STATE_DOT: Record<string, string> = {
  idle_running: 'bg-muted-foreground/40',
  idle_stopped: 'bg-muted-foreground/20',
  init: 'bg-muted-foreground/40',
  accelerator: 'bg-info',
  launch_game: 'bg-info',
  wait_login: 'bg-warning',
  dismiss_popups: 'bg-warning',
  lobby: 'bg-success',
  team_create: 'bg-info',
  team_join: 'bg-info',
  map_setup: 'bg-info',
  ready: 'bg-success',
  in_game: 'bg-success',
  done: 'bg-success',
  error: 'bg-destructive',
}

function blockColor(d: LiveDecisionEvent): string {
  const o = d.outcome || ''
  if (o.startsWith('lobby_confirmed') || o === 'phase_next' || o === 'phase_done') return 'bg-success'
  if (d.verify_success === true) return 'bg-success'
  if (d.verify_success === false || o === 'loop_blocked' || o === 'phase_fail') return 'bg-destructive'
  if (o.startsWith('lobby_pending')) return 'bg-warning'
  if (o === 'no_target') return 'bg-muted-foreground/30'
  if (o === 'tapped') return 'bg-info'
  return 'bg-muted-foreground/30'
}

export function InstancesOverview() {
  const instances = useAppStore((s) => s.instances)
  const emulators = useAppStore((s) => s.emulators)
  const liveDecisions = useAppStore((s) => s.liveDecisions)
  const focusedInstance = useAppStore((s) => s.focusedInstance)
  const setFocusedInstance = useAppStore((s) => s.setFocusedInstance)

  const cards = useMemo(() => {
    const ids = new Set<number>()
    for (const e of emulators) ids.add(e.index)
    for (const i of Object.values(instances)) ids.add(i.index)
    return Array.from(ids).sort((a, b) => a - b).map((idx) => {
      const inst = instances[String(idx)] || (instances as any)[idx]
      const emu = emulators.find((e) => e.index === idx)
      const dec = liveDecisions[idx] || []
      const lastDec = dec.length > 0 ? dec[dec.length - 1] : null
      const stateRaw = (inst?.state as string) || (emu?.running ? 'idle_running' : 'idle_stopped')
      const stateLabel = inst
        ? (stateConfig[inst.state as InstanceState]?.label ?? inst.state)
        : (emu?.running ? '模拟器运行' : '模拟器未启动')
      return {
        index: idx,
        state: stateRaw,
        stateLabel,
        decPhase: lastDec?.phase ?? '',
        decRound: lastDec?.round ?? 0,
        decCount: dec.length,
        recent: dec.slice(-50),
        emuRunning: emu?.running ?? false,
      }
    })
  }, [instances, emulators, liveDecisions])

  if (cards.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border bg-card p-6 text-center text-sm text-muted-foreground">
        没检测到模拟器 — 看「运行控制」检测一下
      </div>
    )
  }

  return (
    <div className="grid gap-2"
         style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))' }}>
      {cards.map((c) => {
        const focused = focusedInstance === c.index
        const dotCls = STATE_DOT[c.state] || 'bg-muted-foreground/30'
        const txtCls = STATE_TEXT[c.state] || 'text-foreground'
        return (
          <button
            key={c.index}
            onClick={() => setFocusedInstance(focused ? null : c.index)}
            className={cn(
              'rounded-lg border bg-card text-left transition-all overflow-hidden',
              focused
                ? 'border-primary ring-2 ring-primary/30'
                : 'border-border hover:border-primary/40',
              !c.emuRunning && 'opacity-60',
            )}
          >
            <div className="px-2.5 py-2 flex items-center gap-2">
              <span className={cn('inline-block w-2 h-2 rounded-full shrink-0', dotCls, c.state !== 'idle_stopped' && c.state !== 'init' && 'animate-pulse')} />
              <span className="font-mono font-semibold text-sm">#{c.index}</span>
              <span className={cn('text-xs truncate', txtCls)}>{c.stateLabel}</span>
              {c.decPhase && (
                <span className="ml-auto text-xs text-muted-foreground font-mono shrink-0">
                  {c.decPhase} R{c.decRound}
                </span>
              )}
            </div>
            {/* 动作色块条 */}
            <div className="px-2.5 pb-2 flex items-center gap-1.5">
              <div className="flex gap-px flex-1 h-3 items-center">
                {c.recent.length === 0 ? (
                  <div className="text-[10px] text-muted-foreground italic">未开始决策</div>
                ) : (
                  c.recent.map((d, i) => (
                    <div
                      key={d.id}
                      className={cn(
                        'flex-1 h-3 rounded-sm',
                        blockColor(d),
                        i === c.recent.length - 1 && 'ring-1 ring-primary',
                      )}
                      title={`${d.phase} R${d.round} · ${d.outcome}`}
                    />
                  ))
                )}
              </div>
              {c.decCount > 0 && (
                <span className="text-[10px] text-muted-foreground font-mono shrink-0">
                  {c.decCount}
                </span>
              )}
            </div>
          </button>
        )
      })}
    </div>
  )
}
