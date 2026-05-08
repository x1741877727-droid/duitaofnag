import { useState } from 'react'
import { cn } from '@/lib/utils'

/**
 * EmergencyMenu — 应急处置下拉 (全部重启 / 强制停止).
 * 来源: shell-parts.jsx EmergencyMenu.
 */
export function EmergencyMenu({
  onAll,
  onForce,
  className,
}: {
  onAll?: () => void
  onForce?: () => void
  className?: string
}) {
  const [open, setOpen] = useState(false)

  return (
    <div className={cn('relative', className)}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={cn(
          'inline-flex items-center gap-1.5 px-3 py-2 rounded-md',
          'text-[12px] font-medium leading-none',
          'bg-card text-muted-foreground border border-border',
          'hover:border-foreground transition-colors cursor-pointer',
        )}
        title="应急处置"
      >
        应急
        <svg width="9" height="9" viewBox="0 0 9 9" aria-hidden>
          <path
            d="M1.5 3l3 3 3-3"
            stroke="currentColor"
            strokeWidth="1.4"
            fill="none"
            strokeLinecap="round"
          />
        </svg>
      </button>

      {open && (
        <>
          <button
            type="button"
            onClick={() => setOpen(false)}
            aria-label="close menu"
            className="fixed inset-0 z-[9] bg-transparent border-0 cursor-default"
          />
          <div
            className="absolute top-full right-0 mt-1 z-10 min-w-[160px] p-1 animate-fade rounded-md border border-border bg-card"
            style={{ boxShadow: '0 8px 24px rgba(0,0,0,.12)' }}
          >
            <button
              type="button"
              onClick={() => {
                setOpen(false)
                onAll?.()
              }}
              className="block w-full text-left px-3 py-2 rounded-sm text-[12px] text-muted-foreground hover:bg-secondary cursor-pointer border-0 bg-transparent"
            >
              全部重启
            </button>
            <button
              type="button"
              onClick={() => {
                setOpen(false)
                onForce?.()
              }}
              className="block w-full text-left px-3 py-2 rounded-sm text-[12px] text-[var(--color-error)] hover:bg-secondary cursor-pointer border-0 bg-transparent"
            >
              强制停止
            </button>
          </div>
        </>
      )}
    </div>
  )
}
