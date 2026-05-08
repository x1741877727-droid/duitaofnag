import { cn } from '@/lib/utils'
import type { TeamGroup } from '@/lib/store'

export type ViewFilter = 'all' | 'g1' | 'g2' | 'g3'

export interface FilterCounts {
  all: number
  g1: number
  g2: number
  g3: number
}

const SQUAD_TEAMS: Record<'g1' | 'g2' | 'g3', TeamGroup[]> = {
  g1: ['A', 'B'],
  g2: ['C', 'D'],
  g3: ['E', 'F'],
}

/** 派生筛选数 (按当前 instances 派生 chip badge 上的 count). */
export function makeFilterCounts<I extends { group: TeamGroup }>(
  instances: I[],
): FilterCounts {
  const inGroups = (groups: TeamGroup[]) =>
    instances.filter((i) => groups.includes(i.group)).length
  return {
    all: instances.length,
    g1: inGroups(SQUAD_TEAMS.g1),
    g2: inGroups(SQUAD_TEAMS.g2),
    g3: inGroups(SQUAD_TEAMS.g3),
  }
}

/** 筛 instances 用 (filter + errorOnly toggle). */
export function filterInstances<I extends { group: TeamGroup; state: string }>(
  instances: I[],
  view: ViewFilter,
  errorOnly: boolean,
): I[] {
  let list = instances
  if (view !== 'all') list = list.filter((i) => SQUAD_TEAMS[view].includes(i.group))
  if (errorOnly) list = list.filter((i) => i.state === 'error')
  return list
}

/**
 * FilterTabs — 大组 chip + 异常 toggle.
 * 来源: shell-parts.jsx FilterTabs.
 */
export function FilterTabs({
  value,
  onChange,
  errorOnly,
  onErrorOnly,
  errorCount,
  counts,
  vertical = false,
  className,
}: {
  value: ViewFilter
  onChange: (v: ViewFilter) => void
  errorOnly: boolean
  onErrorOnly: (b: boolean) => void
  errorCount: number
  counts: FilterCounts
  vertical?: boolean
  className?: string
}) {
  const items: Array<[ViewFilter, string, number, string]> = [
    ['all', '全部', counts.all, 'Cmd 0'],
    ['g1', '大组 1', counts.g1, 'Cmd 1'],
    ['g2', '大组 2', counts.g2, 'Cmd 2'],
    ['g3', '大组 3', counts.g3, 'Cmd 3'],
  ]

  return (
    <div
      className={cn(
        'flex items-center',
        vertical ? 'flex-col gap-1 items-stretch' : 'flex-row gap-1.5',
        className,
      )}
    >
      <div
        className={cn(
          'flex bg-secondary p-[3px] rounded-lg gap-px',
          vertical && 'flex-col flex-none',
        )}
      >
        {items.map(([k, l, n, key]) => {
          const active = value === k && !errorOnly
          const empty = n === 0
          return (
            <button
              key={k}
              type="button"
              onClick={() => {
                onErrorOnly(false)
                onChange(k)
              }}
              disabled={empty}
              title={key}
              className={cn(
                'flex items-center gap-1.5 px-2.5 py-1 rounded-md',
                'text-[12px] font-medium border-0 cursor-pointer transition-colors',
                vertical && 'justify-start',
                empty && 'text-fainter cursor-not-allowed',
                active
                  ? 'bg-card text-foreground shadow-[0_1px_2px_rgba(0,0,0,0.06)]'
                  : 'bg-transparent text-subtle hover:text-foreground',
              )}
            >
              <span>{l}</span>
              <span className="gb-mono text-[10.5px] text-fainter">{n}</span>
            </button>
          )
        })}
      </div>

      <button
        type="button"
        onClick={() => onErrorOnly(!errorOnly)}
        className={cn(
          'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg',
          'text-[12px] font-medium border cursor-pointer transition-colors',
          errorOnly
            ? 'bg-[var(--color-error)] text-card border-[var(--color-error)]'
            : errorCount > 0
              ? 'bg-[var(--color-error-soft)] text-[var(--color-error)] border-transparent'
              : 'bg-secondary text-fainter border-transparent',
        )}
      >
        <span
          className={cn(
            'inline-block w-1.5 h-1.5 rounded-full',
            errorOnly
              ? 'bg-card'
              : errorCount > 0
                ? 'bg-[var(--color-error)] gb-blink'
                : 'bg-fainter',
          )}
        />
        异常 <span className="gb-mono text-[10.5px] opacity-80">{errorCount}</span>
      </button>
    </div>
  )
}
