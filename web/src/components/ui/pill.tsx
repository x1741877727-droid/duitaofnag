import { cn } from '@/lib/utils'
import type { ReactNode, CSSProperties } from 'react'

export type PillTone = 'live' | 'warn' | 'idle' | 'error' | 'accent'

const TONE_STYLES: Record<PillTone, { bg: string; fg: string }> = {
  live: { bg: 'var(--color-live-soft)', fg: '#15803d' },
  warn: { bg: 'var(--color-warn-soft)', fg: '#a16207' },
  idle: { bg: 'var(--color-idle-soft)', fg: 'var(--color-muted-foreground)' },
  error: { bg: 'var(--color-error-soft)', fg: '#b91c1c' },
  accent: { bg: '#ece9ff', fg: '#4338ca' },
}

/**
 * Pill — 小色块状态徽章.
 * 来源: parts.jsx GB.Pill.
 */
export function Pill({
  tone = 'idle',
  children,
  className,
  style,
}: {
  tone?: PillTone
  children: ReactNode
  className?: string
  style?: CSSProperties
}) {
  const t = TONE_STYLES[tone]
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-[4px] px-[7px] py-0.5',
        'text-[11px] font-medium leading-tight',
        className,
      )}
      style={{ background: t.bg, color: t.fg, ...style }}
    >
      {children}
    </span>
  )
}
