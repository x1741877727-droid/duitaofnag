import { cn } from '@/lib/utils'
import type { ReactNode, CSSProperties } from 'react'

/**
 * MonoNum — 等宽数字, 默认 13/500.
 * 来源: parts.jsx GB.MonoNum.
 */
export function MonoNum({
  children,
  size = 13,
  weight = 500,
  color,
  className,
  style,
}: {
  children: ReactNode
  size?: number
  weight?: 400 | 500 | 600 | 700
  color?: string
  className?: string
  style?: CSSProperties
}) {
  return (
    <span
      className={cn('gb-mono', className)}
      style={{ fontSize: size, fontWeight: weight, color, ...style }}
    >
      {children}
    </span>
  )
}
