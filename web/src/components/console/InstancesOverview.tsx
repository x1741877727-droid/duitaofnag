/**
 * 实例总览 — 顶栏色块条, 每实例一张小卡片:
 *   实例号 / 当前阶段 / 已运行轮次 / 健康色 / 帧率 (Day 5 加)
 *
 * 点击切换 focusedInstance.
 */
import { useMemo } from 'react'
import { useAppStore, stateConfig, type InstanceState } from '@/lib/store'
import { cn } from '@/lib/utils'

const STATE_COLOR: Record<string, string> = {
  idle_running: 'bg-zinc-50 text-zinc-700 border-zinc-300',
  idle_stopped: 'bg-zinc-100 text-zinc-400 border-zinc-200',
  init: 'bg-zinc-200 text-zinc-600 border-zinc-300',
  accelerator: 'bg-blue-50 text-blue-700 border-blue-200',
  launch_game: 'bg-blue-50 text-blue-700 border-blue-200',
  wait_login: 'bg-amber-50 text-amber-700 border-amber-200',
  dismiss_popups: 'bg-amber-50 text-amber-700 border-amber-200',
  lobby: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  team_create: 'bg-violet-50 text-violet-700 border-violet-200',
  team_join: 'bg-violet-50 text-violet-700 border-violet-200',
  map_setup: 'bg-violet-50 text-violet-700 border-violet-200',
  ready: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  in_game: 'bg-emerald-100 text-emerald-800 border-emerald-300',
  done: 'bg-emerald-100 text-emerald-800 border-emerald-300',
  error: 'bg-red-50 text-red-700 border-red-200',
}

export function InstancesOverview() {
  const instances = useAppStore((s) => s.instances)
  const emulators = useAppStore((s) => s.emulators)
  const liveDecisions = useAppStore((s) => s.liveDecisions)
  const focusedInstance = useAppStore((s) => s.focusedInstance)
  const setFocusedInstance = useAppStore((s) => s.setFocusedInstance)

  const cards = useMemo(() => {
    // 合并 emulators (始终有) + instances (runner 跑时填充)
    const ids = new Set<number>()
    for (const e of emulators) ids.add(e.index)
    for (const i of Object.values(instances)) ids.add(i.index)
    return Array.from(ids).sort((a, b) => a - b).map((idx) => {
      const inst = instances[String(idx)] || instances[idx as any]
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
        emuRunning: emu?.running ?? false,
      }
    })
  }, [instances, emulators, liveDecisions])

  if (cards.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
        没检测到模拟器 — 看「运行控制」检测一下
      </div>
    )
  }

  return (
    <div className="flex gap-2 overflow-x-auto pb-1">
      {cards.map((c) => {
        const focused = focusedInstance === c.index
        const colorCls = STATE_COLOR[c.state] || 'bg-zinc-50 text-zinc-700 border-zinc-200'
        return (
          <button
            key={c.index}
            onClick={() => setFocusedInstance(focused ? null : c.index)}
            className={cn(
              'shrink-0 min-w-[148px] rounded-md border px-3 py-2 text-left transition-all',
              colorCls,
              focused && 'ring-2 ring-offset-1 ring-blue-500',
            )}
          >
            <div className="flex items-center gap-2 text-xs font-medium">
              <span className="font-mono">#{c.index}</span>
              <span className="font-semibold">{c.stateLabel}</span>
            </div>
            <div className="text-[11px] mt-0.5 text-zinc-500 flex items-center gap-2 font-mono">
              <span>{c.decPhase || '—'}</span>
              {c.decRound > 0 && <span>R{c.decRound}</span>}
              {c.decCount > 0 && <span>· {c.decCount} 决策</span>}
            </div>
          </button>
        )
      })}
    </div>
  )
}
