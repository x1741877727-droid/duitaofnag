import { cn } from '@/lib/utils'
import { STATE_LABEL, STATE_TONE, fmtPhase, toneColor, type Tone } from '@/lib/design-tokens'
import { StatusDot } from '@/components/ui/status-dot'
import { Section } from './Section'
import { useAppStore, type Instance, type InstanceState } from '@/lib/store'

// PhaseTester 选的 phase key → InstanceState (跟 PhaseTester.tsx PHASE_TO_STATE 对齐)
const PHASE_TO_STATE: Record<string, InstanceState> = {
  P0: 'accelerator',
  P1: 'launch_game',
  P2: 'dismiss_popups',
  P3a: 'team_create',
  P3b: 'team_join',
  P4: 'map_setup',
  P5: 'ready',
}

/** store 里的 phaseHistory[idx] 项形态 (来自 store.ts:199). */
export interface PhaseTransition {
  from: string
  to: string
  ts: number
}

interface PhaseSegment {
  state: string
  label: string
  sec: number
  tone: Tone
}

/**
 * 派生本局 phase 时间线段:
 * 输入 phaseHistory[idx] 的 transitions, 输出连续段 [{state, sec}].
 * 最末段的 sec 用 (now - last.ts) + inst.stateDuration 兜底.
 */
export function buildPhaseSegments(
  history: PhaseTransition[],
  inst: Instance,
): PhaseSegment[] {
  if (history.length === 0) {
    const sec = Math.max(0, Math.round(inst.stateDuration ?? 0))
    return [
      {
        state: inst.state,
        label: STATE_LABEL[inst.state] ?? inst.state,
        sec,
        tone: STATE_TONE[inst.state] ?? 'idle',
      },
    ]
  }
  const sorted = [...history].sort((a, b) => a.ts - b.ts)
  const segments: PhaseSegment[] = []
  for (let i = 0; i < sorted.length; i++) {
    const ev = sorted[i]
    const next = sorted[i + 1]
    const endTs = next ? next.ts : Date.now()
    const sec = Math.max(0, Math.round((endTs - ev.ts) / 1000))
    segments.push({
      state: ev.to,
      label: STATE_LABEL[ev.to] ?? ev.to,
      sec,
      tone: STATE_TONE[ev.to] ?? 'idle',
    })
  }
  return segments
}

/**
 * DetailPhases — 本局 phase 时间线 (横向 bar + 列表).
 * 来源: detail.jsx DetailPhases.
 */
export function DetailPhases({
  inst,
  history,
}: {
  inst: Instance
  history: PhaseTransition[]
}) {
  const phaseTester = useAppStore((s) => s.phaseTester)
  const testerActive = phaseTester.busy || phaseTester.results.length > 0

  // PhaseTester 模式: 时间线只显示用户选的那几个 phase (selKeys), 已跑的拿 duration_ms,
  // 当前 phase 用 inst.stateDuration, 还没跑到的占 1s 占位.
  // 非 PhaseTester 模式: 走旧 phaseHistory 路径.
  let phases: PhaseSegment[]
  if (testerActive && phaseTester.selKeys.length > 0) {
    const myResults = phaseTester.results.filter(
      (r) => r.phase_name?.startsWith(`#${inst.index} `) || r.phase_name?.startsWith(`#${inst.index}/`),
    )
    phases = phaseTester.selKeys.map((phaseKey) => {
      const state = PHASE_TO_STATE[phaseKey] || 'init'
      const result = myResults.find((r) => r.phase === phaseKey || r.phase === `#${inst.index}/${phaseKey}`)
      const stateLabel = STATE_LABEL[state] ?? phaseKey
      let sec = 1
      let tone: Tone = 'idle'
      if (result) {
        sec = Math.max(1, Math.round((result.duration_ms || 0) / 1000))
        tone = result.ok ? 'success' : 'error'
      } else if (PHASE_TO_STATE[phaseKey] === inst.state) {
        // 当前正在跑的 phase
        sec = Math.max(1, Math.round(inst.stateDuration ?? 0))
        tone = 'running'
      }
      return { state, label: stateLabel, sec, tone }
    })
  } else {
    phases = buildPhaseSegments(history, inst)
  }
  const total = phases.reduce((s, p) => s + p.sec, 0)

  return (
    <Section title="本局时间线" subtitle={`已进行 ${fmtPhase(total)}`}>
      <div className="flex h-[26px] rounded-md overflow-hidden border border-border">
        {phases.map((p, i) => (
          <div
            key={i}
            title={`${p.label} · ${fmtPhase(p.sec)}`}
            className="relative"
            style={{
              flex: Math.max(0.0001, p.sec),
              minWidth: 4,
              background:
                toneColor(p.tone) +
                (i === phases.length - 1 ? '' : '99'),
              borderRight:
                i < phases.length - 1
                  ? '1px solid var(--color-card)'
                  : 'none',
            }}
          >
            {i === phases.length - 1 && (
              <span
                className="absolute right-1 top-1 w-[5px] h-[5px] rounded-full bg-card gb-pulse"
                aria-hidden
              />
            )}
          </div>
        ))}
      </div>

      <div className="flex flex-col gap-1 mt-2">
        {phases.map((p, i) => (
          <div
            key={i}
            className={cn(
              'grid items-center gap-2 text-[11.5px]',
              'grid-cols-[14px_1fr_70px_50px]',
            )}
          >
            <StatusDot tone={p.tone} pulse={false} />
            <span className="text-muted-foreground">{p.label}</span>
            <span className="gb-mono text-subtle text-right">
              {fmtPhase(p.sec)}
            </span>
            <span className="gb-mono text-fainter text-right">
              {total > 0 ? ((p.sec / total) * 100).toFixed(0) : 0}%
            </span>
          </div>
        ))}
      </div>
    </Section>
  )
}
