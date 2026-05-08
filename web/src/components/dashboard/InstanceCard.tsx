import { cn } from '@/lib/utils'
import { STATE_LABEL, STATE_TONE, fmtPhase } from '@/lib/design-tokens'
import type { Instance } from '@/lib/store'
import { SnapshotThumb } from './SnapshotThumb'

export interface InstanceCardData extends Instance {
  /** 上次决策时间 (相对时间字符串, 派生自 liveDecisions[idx][0].ts). */
  decisionAgo?: string
  /** 当前 phase 已停留秒数 (派生自 stateDuration). */
  phaseSec?: number
  /** 是否队长 (role==='captain'). */
  captain?: boolean
}

/**
 * InstanceCard — 监控墙单卡, 两种密度:
 *   wall (默认) — 16:9 大画面 + 下方信息条
 *   row         — 横向紧凑卡 (108×60 缩略 + 文字)
 *
 * 来源: instance-card.jsx
 */
export function InstanceCard({
  inst,
  selected,
  onClick,
  density = 'wall',
  className,
}: {
  inst: InstanceCardData
  selected: boolean
  onClick: () => void
  density?: 'wall' | 'row'
  className?: string
}) {
  const tone = STATE_TONE[inst.state] ?? 'idle'
  const stateLabel = STATE_LABEL[inst.state] ?? inst.state
  const isErr = inst.state === 'error'
  const phaseSec = inst.phaseSec ?? Math.round(inst.stateDuration ?? 0)
  const captain = inst.captain ?? inst.role === 'captain'

  if (density === 'row') {
    return (
      <button
        type="button"
        onClick={onClick}
        className={cn(
          'grid grid-cols-[108px_1fr_auto] gap-2.5 p-1.5 rounded-lg text-left',
          'cursor-pointer transition-colors border',
          selected
            ? 'border-accent shadow-[0_0_0_2px_rgba(59,58,54,0.2)]'
            : isErr
              ? 'border-[var(--color-error)] bg-[var(--color-error-bg)]'
              : 'border-border bg-card hover:border-border-soft',
          className,
        )}
      >
        <div className="relative w-[108px] h-[60px] rounded-[5px] overflow-hidden">
          <SnapshotThumb
            instanceIdx={inst.index}
            tone={tone}
            errorMsg={inst.error}
            width={216}
            fps={1}
            state={inst.state}
          />
          <div
            className="absolute top-1 left-1 gb-mono text-[10px] font-semibold text-card"
            style={{ textShadow: '0 1px 2px rgba(0,0,0,0.6)' }}
          >
            #{String(inst.index).padStart(2, '0')}
          </div>
        </div>
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-[13px] font-medium text-foreground">
            <span className="truncate">{inst.nickname || `玩家${inst.index}`}</span>
            {captain && (
              <span
                className="text-[10px] font-semibold text-accent"
                style={{ letterSpacing: '.05em' }}
              >
                队长
              </span>
            )}
          </div>
          <div className="flex items-center gap-1.5 mt-0.5 text-[11px] text-subtle">
            <span
              className={cn(
                'inline-block w-1.5 h-1.5 rounded-full',
                tone === 'live' && 'gb-pulse',
              )}
              style={{
                background:
                  tone === 'live'
                    ? 'var(--color-live)'
                    : tone === 'warn'
                      ? 'var(--color-warn)'
                      : tone === 'error'
                        ? 'var(--color-error)'
                        : 'var(--color-fainter)',
              }}
            />
            <span
              className={cn(
                'font-medium',
                isErr ? 'text-[var(--color-error)]' : 'text-muted-foreground',
              )}
            >
              {stateLabel}
            </span>
            <span className="gb-mono text-fainter">· {fmtPhase(phaseSec)}</span>
          </div>
        </div>
        <div className="flex flex-col items-end gap-1 pr-1.5">
          <div
            className="text-[10px] gb-mono text-subtle"
            style={{ letterSpacing: '.04em' }}
          >
            {inst.group}
            {captain ? '·C' : ''}
          </div>
          {inst.decisionAgo && (
            <div className="text-[10px] text-fainter">{inst.decisionAgo}</div>
          )}
        </div>
      </button>
    )
  }

  // wall density
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'relative block w-full text-left p-0 rounded-[10px] overflow-hidden',
        'cursor-pointer border transition-shadow',
        selected
          ? 'border-accent shadow-[0_0_0_2px_var(--color-accent),0_6px_18px_rgba(0,0,0,0.10)]'
          : isErr
            ? 'border-[var(--color-error)] shadow-[0_0_0_1px_var(--color-error),0_4px_14px_rgba(220,38,38,0.10)]'
            : 'border-border shadow-[0_1px_0_rgba(0,0,0,0.02)] hover:shadow-[0_0_0_1.5px_var(--color-accent),0_4px_12px_rgba(0,0,0,0.08)]',
        className,
      )}
      style={{ background: 'var(--color-card)' }}
    >
      {/* snapshot */}
      <div className="relative w-full" style={{ aspectRatio: '16 / 9' }}>
        <SnapshotThumb
          instanceIdx={inst.index}
          tone={tone}
          errorMsg={inst.error}
          width={320}
          fps={2}
          state={inst.state}
        />

        {/* index 角标 (左上) */}
        <div className="absolute top-2 left-2.5">
          <span
            className="gb-mono text-[11px] font-semibold text-card px-1.5 py-0.5 rounded-sm"
            style={{
              background: 'rgba(0,0,0,.55)',
              backdropFilter: 'blur(6px)',
              letterSpacing: '.02em',
            }}
          >
            #{String(inst.index).padStart(2, '0')}
          </span>
        </div>

        {/* state pill (右上) */}
        <div className="absolute top-2 right-2.5">
          <span
            className="inline-flex items-center gap-1 px-2 py-[3px] rounded-sm text-[11px] font-semibold"
            style={{
              background: 'rgba(255,255,255,.96)',
              color: isErr
                ? 'var(--color-error)'
                : tone === 'live'
                  ? '#15803d'
                  : tone === 'warn'
                    ? '#a16207'
                    : 'var(--color-muted-foreground)',
              boxShadow: '0 1px 2px rgba(0,0,0,.15)',
            }}
          >
            <span
              className={cn(
                'inline-block w-[5px] h-[5px] rounded-full',
                tone === 'live' && 'gb-pulse',
              )}
              style={{
                background: isErr
                  ? 'var(--color-error)'
                  : tone === 'live'
                    ? 'var(--color-live)'
                    : tone === 'warn'
                      ? 'var(--color-warn)'
                      : 'var(--color-subtle)',
              }}
            />
            {stateLabel}
          </span>
        </div>
      </div>

      {/* info strip */}
      <div className="px-3 pt-2.5 pb-3 flex items-center gap-2.5 bg-card">
        <div className="min-w-0 flex-1">
          <div className="text-[13px] font-semibold leading-tight text-foreground truncate">
            {inst.nickname || `玩家${inst.index}`}
          </div>
          <div className="flex items-center gap-1.5 mt-1 text-[11px] text-subtle">
            <span
              className={cn(
                'font-semibold',
                captain ? 'text-foreground' : 'text-subtle',
              )}
              style={{ letterSpacing: '.04em' }}
            >
              {inst.group} 队{captain ? ' · 队长' : ''}
            </span>
            {inst.decisionAgo && (
              <>
                <span className="text-fainter">·</span>
                <span className="gb-mono">{inst.decisionAgo}</span>
              </>
            )}
          </div>
        </div>
        <div className="gb-mono text-[12px] font-semibold text-muted-foreground text-right">
          {fmtPhase(phaseSec)}
        </div>
      </div>
    </button>
  )
}
