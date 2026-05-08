import * as React from 'react'

import { cn } from '@/lib/utils'

function Input({ className, type, ...props }: React.ComponentProps<'input'>) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(
        // base — 浅色描边 + 白底 + 焦点边深
        'h-8 w-full min-w-0 rounded-md',
        'border border-border bg-card px-3',
        'text-[13px] leading-none text-foreground',
        'placeholder:text-fainter',
        'transition-colors duration-150 outline-none',
        'focus-visible:border-foreground focus-visible:ring-2 focus-visible:ring-ring/30',
        'disabled:cursor-not-allowed disabled:opacity-50',
        'file:inline-flex file:h-7 file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground',
        'aria-invalid:border-destructive aria-invalid:ring-2 aria-invalid:ring-destructive/30',
        className,
      )}
      {...props}
    />
  )
}

export { Input }
