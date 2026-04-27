/**
 * 动作记录 — 底部色块时间线.
 *
 * 每实例一行, 横向最近 N 个决策的色块:
 *   绿 = verify_success / lobby_confirmed
 *   红 = verify 失败 / loop_blocked
 *   橙 = lobby_pending
 *   灰 = no_target / retry
 *   蓝 = 当前最新一帧 (闪烁)
 *
 * Hover 显示该决策的 phase / round / outcome / tap.
 */
import { useMemo } from 'react'
import { useAppStore, type LiveDecisionEvent } from '@/lib/store'
import { cn } from '@/lib/utils'

function blockColor(d: LiveDecisionEvent): string {
  const o = d.outcome || ''
  if (o.startsWith('lobby_confirmed')) return 'bg-success'
  if (d.verify_success === true) return 'bg-success'
  if (d.verify_success === false) return 'bg-destructive'
  if (o === 'loop_blocked') return 'bg-destructive'
  if (o.startsWith('lobby_pending')) return 'bg-warning'
  if (o === 'no_target') return 'bg-muted-foreground/50'
  if (o === 'login_timeout_fail' || o === 'dead_screen' || o === 'phase_fail') return 'bg-destructive'
  if (o === 'phase_next' || o === 'phase_done') return 'bg-success'
  if (o === 'tapped') return 'bg-info'
  return 'bg-muted-foreground/30'
}

function fmtTime(ts: number): string {
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString('zh-CN', { hour12: false })
}

export function ActionLog() {
  const liveDecisions = useAppStore((s) => s.liveDecisions)
  const focused = useAppStore((s) => s.focusedInstance)
  const setFocused = useAppStore((s) => s.setFocusedInstance)

  const rows = useMemo(() => {
    const ids = Object.keys(liveDecisions).map(Number).sort((a, b) => a - b)
    return ids.map((idx) => ({
      idx,
      decisions: liveDecisions[idx] || [],
    }))
  }, [liveDecisions])

  if (rows.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border p-2 text-center text-[11px] text-muted-foreground">
        动作记录 — 等待实例开始决策
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-border bg-card p-2 overflow-x-auto">
      <div className="text-[11px] font-semibold text-foreground mb-1">动作记录</div>
      <div className="space-y-1">
        {rows.map(({ idx, decisions }) => (
          <div key={idx} className="flex items-center gap-2 text-xs">
            <button
              onClick={() => setFocused(focused === idx ? null : idx)}
              className={cn(
                'shrink-0 w-12 text-left font-mono text-[11px]',
                focused === idx ? 'text-info font-bold' : 'text-muted-foreground'
              )}
            >
              #{idx}
            </button>
            <div className="flex gap-0.5 overflow-hidden flex-1">
              {decisions.slice(-50).map((d, i) => {
                const isLast = i === decisions.slice(-50).length - 1
                return (
                  <div
                    key={d.id}
                    className={cn(
                      'shrink-0 w-2 h-4 rounded-[1px] cursor-help',
                      blockColor(d),
                      isLast && 'ring-1 ring-blue-500',
                    )}
                    title={`${d.phase} R${d.round} · ${d.outcome} · ${fmtTime(d.ts)}`}
                  />
                )
              })}
            </div>
            <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
              {decisions.length}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
