import { cn } from '@/lib/utils'
import type { Tone } from '@/lib/design-tokens'
import type { ReactNode } from 'react'

/**
 * Stat — 顶部状态条的小标签 + 大数字.
 * 来源: shell-parts.jsx Stat.
 */
export function Stat({
  label,
  value,
  sub,
  tone,
  mono = true,
  large = false,
  className,
}: {
  label: string
  value: ReactNode
  sub?: string
  tone?: Tone
  mono?: boolean
  large?: boolean
  className?: string
}) {
  const colorCls =
    tone === 'live'
      ? 'text-[var(--color-live)]'
      : tone === 'error'
        ? 'text-[var(--color-error)]'
        : tone === 'warn'
          ? 'text-[var(--color-warn)]'
          : 'text-foreground'

  return (
    <div className={cn('flex flex-col gap-0.5', className)}>
      <div
        className="text-[10px] font-medium uppercase text-subtle"
        style={{ letterSpacing: '.04em' }}
      >
        {label}
      </div>
      <div
        className={cn(
          'flex items-baseline gap-1.5 leading-none font-semibold',
          mono && 'gb-mono',
          large ? 'text-[22px]' : 'text-[16px]',
          colorCls,
        )}
      >
        {value}
        {sub && (
          <span className="text-[11px] font-medium text-fainter">{sub}</span>
        )}
      </div>
    </div>
  )
}
