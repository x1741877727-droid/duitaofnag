import { cn } from '@/lib/utils'
import type { Instance } from '@/lib/store'

/**
 * ExceptionBanner — 顶部红色异常 banner (running-error 时显示).
 * 来源: shell-parts.jsx ExceptionBanner.
 */
export function ExceptionBanner({
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
  const e = errors[0]
  const errorTitle = e.error || `${e.index} 号已掉线`

  return (
    <div
      className={cn(
        'flex items-center gap-2.5 px-3.5 py-2 text-[12px] text-card',
        className,
      )}
      style={{ background: 'var(--color-error)' }}
    >
      <span
        aria-hidden
        className="inline-block w-2 h-2 rounded-full bg-card gb-blink"
      />
      <span className="font-semibold">{errorTitle}</span>
      <span className="opacity-85">
        {e.nickname || `玩家${e.index}`} · 已断开 · 当前局结束后停止接新局
      </span>
      {errors.length > 1 && (
        <span
          className="gb-mono px-1.5 py-px rounded-[10px] text-[11px]"
          style={{ background: 'rgba(255,255,255,.2)' }}
        >
          +{errors.length - 1}
        </span>
      )}
      <span className="flex-1" />
      <button
        type="button"
        onClick={() => onView(e.index)}
        className="px-2.5 py-1 rounded-sm text-[11.5px] text-card font-medium cursor-pointer border-0"
        style={{ background: 'rgba(255,255,255,.18)' }}
      >
        查看
      </button>
      <button
        type="button"
        onClick={() => onRestart?.(e.index)}
        className="px-2.5 py-1 rounded-sm text-[11.5px] font-semibold cursor-pointer border-0 bg-card"
        style={{ color: 'var(--color-error)' }}
      >
        尝试重启
      </button>
    </div>
  )
}
