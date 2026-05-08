import { cn } from '@/lib/utils'

/**
 * SegRadio — 段落式单选按钮组 (filter / sort 用).
 * 来源: detail.jsx SegRadio.
 */
export function SegRadio<V extends string>({
  value,
  onChange,
  options,
  className,
}: {
  value: V
  onChange: (v: V) => void
  options: Array<[V, string]>
  className?: string
}) {
  return (
    <div
      className={cn(
        'inline-flex bg-secondary rounded-md p-0.5 gap-px',
        className,
      )}
    >
      {options.map(([v, label]) => {
        const active = value === v
        return (
          <button
            key={v}
            type="button"
            onClick={() => onChange(v)}
            className={cn(
              'px-2.5 py-[3px] rounded text-[11px] font-medium transition-colors',
              'cursor-pointer border-0 bg-transparent',
              active
                ? 'bg-card text-foreground shadow-[0_1px_2px_rgba(0,0,0,0.06)]'
                : 'text-subtle hover:text-foreground',
            )}
          >
            {label}
          </button>
        )
      })}
    </div>
  )
}
