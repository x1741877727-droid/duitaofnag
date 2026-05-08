import { cn } from '@/lib/utils'
import type { Instance } from '@/lib/store'
import { Pill } from '@/components/ui/pill'

export function DetailHeader({
  inst,
  ldName,
  onClose,
  compact,
  className,
}: {
  inst: Instance
  /** LDPlayer 实例名 (从 emulators[idx].name 取). */
  ldName?: string
  onClose?: () => void
  compact?: boolean
  className?: string
}) {
  const captain = inst.role === 'captain'
  return (
    <div
      className={cn(
        'flex items-center gap-2.5 border-b border-border',
        compact ? 'px-3 py-2' : 'px-4 py-3.5',
        className,
      )}
    >
      <span
        className="gb-mono text-[12px] font-semibold px-1.5 py-0.5 rounded-md text-muted-foreground bg-secondary"
      >
        #{String(inst.index).padStart(2, '0')}
      </span>
      <div className="min-w-0 flex-1">
        <div
          className={cn(
            'flex items-center gap-1.5 font-semibold text-foreground',
            compact ? 'text-[13px]' : 'text-[14px]',
          )}
        >
          <span>{inst.nickname || `玩家${inst.index}`}</span>
          {captain && <Pill tone="accent">队长</Pill>}
          <span className="text-[11px] text-subtle font-medium">
            · {inst.group} 队
          </span>
        </div>
        {ldName && (
          <div className="gb-mono text-[11px] text-subtle mt-0.5 truncate">
            {ldName}
          </div>
        )}
      </div>
      {onClose && (
        <button
          type="button"
          onClick={onClose}
          title="关闭 (Esc)"
          aria-label="关闭"
          className="w-[26px] h-[26px] rounded-md text-subtle flex items-center justify-center cursor-pointer border-0 bg-transparent hover:bg-secondary"
        >
          <svg width="13" height="13" viewBox="0 0 14 14" fill="none">
            <path
              d="M3 3l8 8M11 3l-8 8"
              stroke="currentColor"
              strokeWidth="1.4"
              strokeLinecap="round"
            />
          </svg>
        </button>
      )}
    </div>
  )
}
