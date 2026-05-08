import { cn } from '@/lib/utils'
import type { ReactNode } from 'react'

/**
 * Kbd — 键盘快捷键文字徽章.
 * 来源: parts.jsx GB.Kbd.
 */
export function Kbd({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <kbd
      className={cn(
        'inline-block rounded-[4px] px-[5px] py-px',
        'text-[10.5px] leading-tight',
        'font-mono',
        'border border-border bg-secondary text-muted-foreground',
        className,
      )}
    >
      {children}
    </kbd>
  )
}
