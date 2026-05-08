import { cn } from '@/lib/utils'

/**
 * PrimaryAction — 启动 / 停止 主操作按钮.
 * 来源: shell-parts.jsx PrimaryAction.
 *
 * pageState:
 *   running / running-error  → "全部停止" (黑底)
 *   standby / zero           → "开始今天的工作 · N 台"
 */
export function PrimaryAction({
  pageState,
  total,
  onAction,
  loading,
  className,
}: {
  pageState: 'zero' | 'standby' | 'running' | 'running-error'
  total: number
  onAction: () => void
  loading?: boolean
  className?: string
}) {
  const isRunning = pageState === 'running' || pageState === 'running-error'
  const label = isRunning ? '全部停止' : `开始今天的工作 · ${total} 台`

  return (
    <button
      type="button"
      onClick={onAction}
      disabled={loading}
      className={cn(
        'inline-flex items-center gap-2 px-4 py-2 rounded-md',
        'text-[13px] font-semibold leading-none',
        'border shadow-[0_1px_2px_rgba(0,0,0,0.08)] transition-colors',
        'cursor-pointer disabled:cursor-wait disabled:opacity-70',
        isRunning
          ? 'bg-foreground text-card border-foreground hover:bg-[var(--color-error)] hover:border-[var(--color-error)]'
          : 'bg-accent text-accent-foreground border-accent hover:bg-foreground hover:border-foreground',
        className,
      )}
    >
      {isRunning ? (
        <span
          aria-hidden
          className="inline-block w-2 h-2 rounded-[2px] bg-card"
        />
      ) : (
        <svg
          width="11"
          height="11"
          viewBox="0 0 11 11"
          fill="currentColor"
          aria-hidden
        >
          <path d="M2 1l8 4.5L2 10z" />
        </svg>
      )}
      <span>{label}</span>
    </button>
  )
}
