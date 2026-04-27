/**
 * 阶段时间线 — 左侧纵向条, 显示聚焦实例的阶段流转 + 每阶段决策计数.
 */
import { useMemo } from 'react'
import { useAppStore } from '@/lib/store'

const PHASE_LABEL: Record<string, string> = {
  P0: '加速器校验',
  P1: '启动游戏',
  P2: '清弹窗',
  P3a: '队长创建',
  P3b: '队员加入',
  P4: '选地图开打',
}

export function PhaseTimeline() {
  const focused = useAppStore((s) => s.focusedInstance)
  const liveDecisions = useAppStore((s) => s.liveDecisions)
  const phaseHistory = useAppStore((s) => s.phaseHistory)

  const items = useMemo(() => {
    if (focused === null) return []
    const dec = liveDecisions[focused] || []
    // 按阶段聚合: 每阶段最近一次出现 + 总轮数
    const byPhase = new Map<string, { phase: string; lastTs: number; rounds: number }>()
    for (const d of dec) {
      const cur = byPhase.get(d.phase)
      if (cur) {
        cur.rounds = Math.max(cur.rounds, d.round)
        cur.lastTs = Math.max(cur.lastTs, d.ts)
      } else {
        byPhase.set(d.phase, { phase: d.phase, lastTs: d.ts, rounds: d.round })
      }
    }
    // 用 phaseHistory 补足跳过的 phase
    const ph = phaseHistory[focused] || []
    for (const p of ph) {
      if (!byPhase.has(p.to)) {
        byPhase.set(p.to, { phase: p.to, lastTs: p.ts, rounds: 0 })
      }
    }
    return Array.from(byPhase.values()).sort((a, b) => a.lastTs - b.lastTs)
  }, [focused, liveDecisions, phaseHistory])

  if (focused === null) {
    return (
      <div className="rounded-lg border border-border bg-card p-3 text-xs text-muted-foreground">
        阶段时间线 — 选个实例看
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-border bg-card p-3 overflow-y-auto">
      <div className="text-xs font-semibold text-foreground mb-2">阶段时间线</div>
      {items.length === 0 ? (
        <div className="text-xs text-muted-foreground">还没有数据</div>
      ) : (
        <ol className="space-y-1.5">
          {items.map((it, idx) => {
            const last = idx === items.length - 1
            return (
              <li
                key={it.phase}
                className={`flex items-center gap-2 text-xs ${
                  last ? 'font-semibold text-foreground' : 'text-muted-foreground'
                }`}
              >
                <span
                  className={`inline-block w-2 h-2 rounded-full ${
                    last ? 'bg-blue-500 animate-pulse' : 'bg-zinc-300'
                  }`}
                />
                <span className="font-mono">{it.phase}</span>
                <span className="truncate">{PHASE_LABEL[it.phase] || ''}</span>
                {it.rounds > 0 && (
                  <span className="ml-auto text-[10px] font-mono text-zinc-400">
                    R{it.rounds}
                  </span>
                )}
              </li>
            )
          })}
        </ol>
      )}
    </div>
  )
}
