import { cn } from '@/lib/utils'
import { toneColor, type Tone } from '@/lib/design-tokens'

/**
 * StatusDot — 圆点状态指示, live tone 自带 pulse 动画.
 * 来源: parts.jsx GB.StatusDot.
 */
export function StatusDot({
  tone = 'idle',
  pulse = true,
  size = 6,
  className,
}: {
  tone?: Tone
  pulse?: boolean
  size?: number
  className?: string
}) {
  return (
    <span
      className={cn(
        'inline-block rounded-full shrink-0',
        pulse && tone === 'live' && 'gb-pulse',
        className,
      )}
      style={{
        width: size,
        height: size,
        flex: `0 0 ${size}px`,
        background: toneColor(tone),
      }}
    />
  )
}
