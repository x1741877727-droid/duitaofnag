import { cn } from '@/lib/utils'
import { STATE_LABEL, STATE_TONE, fmtPhase } from '@/lib/design-tokens'
import type { Instance } from '@/lib/store'
import { SnapshotThumb } from '../SnapshotThumb'
import { StatusDot } from '@/components/ui/status-dot'

/**
 * DetailLive — 抽屉内大画面 5 fps + state pill + uptime overlay.
 * 来源: detail.jsx DetailLive
 */
export function DetailLive({
  inst,
  className,
}: {
  inst: Instance
  className?: string
}) {
  const tone = STATE_TONE[inst.state] ?? 'idle'
  const stateLabel = STATE_LABEL[inst.state] ?? inst.state
  const phaseSec = Math.round(inst.stateDuration ?? 0)

  return (
    <div className={cn('p-3', className)}>
      <div
        className="relative rounded-lg overflow-hidden"
        style={{ aspectRatio: '16 / 9', background: '#000' }}
      >
        <SnapshotThumb
          instanceIdx={inst.index}
          tone={tone}
          errorMsg={inst.error}
          width={0}
          fps={5}
          state={inst.state}
        />
        <div
          className="absolute left-2.5 bottom-2.5 px-2.5 py-1 rounded-md flex items-center gap-2 text-card"
          style={{
            background: 'rgba(0,0,0,.55)',
            backdropFilter: 'blur(6px)',
          }}
        >
          <StatusDot tone={tone} />
          <span className="text-[12px] font-semibold">{stateLabel}</span>
          <span
            className="gb-mono text-[11px]"
            style={{ color: 'rgba(255,255,255,.7)' }}
          >
            停留 {fmtPhase(phaseSec)}
          </span>
        </div>
        <div
          className="absolute right-2.5 top-2.5 px-1.5 py-0.5 rounded-sm gb-mono text-[10px] text-card"
          style={{ background: 'rgba(0,0,0,.55)', letterSpacing: '.04em' }}
        >
          5 fps
        </div>
      </div>
    </div>
  )
}
