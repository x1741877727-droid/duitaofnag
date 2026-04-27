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
  const liveDecisions = useAppStore((s) => s.liveDecisions)
  const focusedInstance = useAppStore((s) => s.focusedInstance)
  const setFocusedInstance = useAppStore((s) => s.setFocusedInstance)

  const cards = useMemo(() => {
    const list = Object.values(instances)
    list.sort((a, b) => a.index - b.index)
    return list.map((inst) => {
      const dec = liveDecisions[inst.index] || []
      const lastDec = dec.length > 0 ? dec[dec.length - 1] : null
      const decRound = lastDec?.round ?? 0
      const decPhase = lastDec?.phase ?? ''
      const stateLabel = stateConfig[inst.state as InstanceState]?.label ?? inst.state
      return {
        index: inst.index,
        state: inst.state as string,
        stateLabel,
        decPhase,
        decRound,
        decCount: dec.length,
      }
    })
  }, [instances, liveDecisions])

  if (cards.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
        暂无实例 — 在「运行控制」页选好账号后点开始
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
