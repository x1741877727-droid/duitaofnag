import { useMemo, useState } from 'react'
import { cn } from '@/lib/utils'
import type { LiveDecisionEvent } from '@/lib/store'
import { Section } from './Section'
import { SegRadio } from '@/components/ui/seg-radio'

type ResultFilter = 'all' | 'ok' | 'fail'

const TYPE_FILTERS: Array<['all' | string, string]> = [
  ['all', '所有类型'],
  ['tap', 'tap'],
  ['fire', 'fire'],
  ['follow', 'follow'],
  ['ocr', 'ocr'],
  ['yolo', 'yolo'],
]

function fmtTime(ts: number): string {
  const d = new Date(ts)
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  const ss = String(d.getSeconds()).padStart(2, '0')
  return `${hh}:${mm}:${ss}`
}

/**
 * DetailDecisions — 决策日志倒序列表 + result/type filter.
 * 来源: detail.jsx DetailDecisions.
 */
export function DetailDecisions({
  decisions,
}: {
  decisions: LiveDecisionEvent[]
}) {
  const [resultFilter, setResultFilter] = useState<ResultFilter>('all')
  const [typeFilter, setTypeFilter] = useState<string>('all')

  const filtered = useMemo(
    () =>
      decisions
        .filter((d) => {
          const isOk = d.verify_success === true || d.outcome === 'success'
          const isFail = d.verify_success === false
          if (resultFilter === 'ok' && !isOk) return false
          if (resultFilter === 'fail' && !isFail) return false
          if (typeFilter !== 'all' && d.tap_method !== typeFilter) return false
          return true
        })
        .slice()
        .reverse(),
    [decisions, resultFilter, typeFilter],
  )

  return (
    <Section title="决策日志" subtitle={`最近 ${filtered.length} 条`}>
      <div className="flex flex-wrap gap-1.5 mb-2">
        <SegRadio<ResultFilter>
          value={resultFilter}
          onChange={setResultFilter}
          options={[
            ['all', '全部'],
            ['ok', '成功'],
            ['fail', '失败'],
          ]}
        />
        <SegRadio<string>
          value={typeFilter}
          onChange={setTypeFilter}
          options={TYPE_FILTERS}
        />
      </div>
      <div
        className="rounded-lg overflow-y-auto border border-border bg-card"
        style={{ maxHeight: 240 }}
      >
        {filtered.map((d, i) => {
          // 三态: ok / fail / pending (未验证 / no_target / 等待中, 不该统一标失败)
          let status: 'ok' | 'fail' | 'pending' = 'pending'
          let statusText = '⋯ 等待'
          let statusColor = 'var(--color-muted-foreground)'
          if (d.verify_success === true || d.outcome === 'success') {
            status = 'ok'; statusText = '✓ 成功'; statusColor = 'var(--color-live)'
          } else if (d.verify_success === false) {
            status = 'fail'; statusText = '✗ 失败'; statusColor = 'var(--color-error)'
          }
          void status
          const target = d.tap_target ?? d.outcome ?? '—'
          return (
            <div
              key={d.id}
              className={cn(
                'grid items-center gap-2 px-2.5 py-1.5 text-[11.5px]',
                'grid-cols-[74px_56px_1fr_60px_50px]',
                i > 0 && 'border-t border-border-soft',
              )}
            >
              <span className="gb-mono text-subtle">{fmtTime(d.ts)}</span>
              <span
                className="gb-mono text-[10.5px] uppercase font-medium"
                style={{ letterSpacing: '.04em', color: 'var(--color-muted-foreground)' }}
              >
                {d.tap_method ?? '—'}
              </span>
              <span className="gb-mono text-foreground truncate">{target}</span>
              <span
                className="text-[10.5px] font-semibold"
                style={{ color: statusColor }}
              >
                {statusText}
              </span>
              <span className="gb-mono text-subtle text-right">{d.round}r</span>
            </div>
          )
        })}
        {filtered.length === 0 && (
          <div className="p-4 text-center text-[11px] text-fainter">
            无匹配记录
          </div>
        )}
      </div>
    </Section>
  )
}
