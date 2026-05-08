import * as React from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'

import { cn } from '@/lib/utils'

const buttonVariants = cva(
  // base — Linear/Vercel 风, 平面无渐变, hover 仅边框/背景过渡
  [
    'inline-flex items-center justify-center gap-2 whitespace-nowrap',
    'rounded-md text-[13px] font-medium leading-none',
    'transition-colors duration-150',
    "[&_svg]:pointer-events-none [&_svg:not([class*='size-'])]:size-[14px] [&_svg]:shrink-0",
    'shrink-0 outline-none cursor-pointer',
    'focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:ring-offset-1 focus-visible:ring-offset-background',
    'disabled:pointer-events-none disabled:opacity-50',
  ].join(' '),
  {
    variants: {
      variant: {
        // 主操作 — accent 黑底
        default:
          'bg-accent text-accent-foreground border border-accent shadow-[0_1px_2px_rgba(0,0,0,0.08)] hover:bg-foreground hover:border-foreground',
        // 危险 — 错误红
        destructive:
          'bg-destructive text-destructive-foreground border border-destructive hover:bg-destructive/90',
        // 描边 — 浅边 + 白底 + hover 边深
        outline:
          'bg-card text-foreground border border-border hover:border-foreground',
        // 次级 — 米色块
        secondary:
          'bg-secondary text-secondary-foreground border border-transparent hover:bg-muted',
        // 幽灵 — 透明 hover 浅
        ghost:
          'bg-transparent text-muted-foreground border border-transparent hover:bg-secondary hover:text-foreground',
        link: 'text-foreground underline-offset-4 hover:underline px-0',
      },
      size: {
        default: 'h-8 px-3.5',
        sm: 'h-7 px-2.5 text-[12px] gap-1.5',
        lg: 'h-9 px-4',
        icon: 'size-8',
        'icon-sm': 'size-7',
        'icon-lg': 'size-9',
      },
    },
    defaultVariants: {
      variant: 'outline',
      size: 'default',
    },
  },
)

function Button({
  className,
  variant,
  size,
  asChild = false,
  ...props
}: React.ComponentProps<'button'> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean
  }) {
  const Comp = asChild ? Slot : 'button'

  return (
    <Comp
      data-slot="button"
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  )
}

export { Button, buttonVariants }
