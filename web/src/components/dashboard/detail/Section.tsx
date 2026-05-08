import { cn } from '@/lib/utils'
import type { ReactNode } from 'react'

/**
 * 共享: detail 抽屉里的小标题节区.
 * 来源: detail.jsx Section.
 */
export function Section({
  title,
  subtitle,
  children,
  pad = true,
  className,
}: {
  title: string
  subtitle?: string
  children: ReactNode
  pad?: boolean
  className?: string
}) {
  return (
    <div className={cn(pad && 'px-4 pb-3.5', className)}>
      <div className="flex items-baseline gap-2 my-3.5 mb-2">
        <div
          className="text-[11px] font-semibold uppercase text-muted-foreground"
          style={{ letterSpacing: '.04em' }}
        >
          {title}
        </div>
        {subtitle && (
          <div className="text-[11px] text-fainter">{subtitle}</div>
        )}
      </div>
      {children}
    </div>
  )
}
