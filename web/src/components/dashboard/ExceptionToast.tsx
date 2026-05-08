import { cn } from '@/lib/utils'
import type { Instance } from '@/lib/store'

/**
 * ExceptionToast — 右上角浮动异常通知, 最多显示 2 条.
 * 来源: shell-parts.jsx ExceptionToast.
 */
export function ExceptionToast({
  errors,
  onView,
  onRestart,
  className,
}: {
  errors: Instance[]
  onView: (idx: number) => void
  onRestart?: (idx: number) => void
  className?: string
}) {
  if (!errors.length) return null

  return (
    <div
      className={cn(
        'absolute top-3.5 right-3.5 z-[8]',
        'flex flex-col gap-2 max-w-[320px]',
        className,
      )}
    >
      {errors.slice(0, 2).map((e) => (
        <div
          key={e.index}
          className="animate-fade flex flex-col gap-1 p-2.5 rounded-md bg-card"
          style={{
            borderLeft: '3px solid var(--color-error)',
            boxShadow: '0 6px 20px rgba(0,0,0,.12)',
          }}
        >
          <div className="text-[12px] font-semibold text-foreground">
            {e.error || `${e.index} 号已掉线`}
          </div>
          <div className="text-[11px] leading-snug text-subtle">
            {e.nickname || `玩家${e.index}`}
            <br />
            当前局结束后停止接新局
          </div>
          <div className="flex gap-1 mt-1">
            <button
              type="button"
              onClick={() => onView(e.index)}
              className="px-2 py-0.5 rounded-sm text-[11px] font-medium cursor-pointer border-0 bg-transparent text-accent"
            >
              查看
            </button>
            <button
              type="button"
              onClick={() => onRestart?.(e.index)}
              className="px-2 py-0.5 rounded-sm text-[11px] font-medium cursor-pointer border-0 bg-transparent text-muted-foreground"
            >
              尝试重启
            </button>
          </div>
        </div>
      ))}
    </div>
  )
}
