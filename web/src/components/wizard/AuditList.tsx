// 增量补全列表 — 4 区: applied / drift / missing / below_recommended.
// 用于 StepRecommend 显示当前 LDPlayer 配置 vs 推荐.

import { Pill } from '@/components/ui/pill'
import type { AuditItem, AuditReport } from './types'
import { auditStatusLabel, auditStatusTone } from './helpers'

interface Props {
  report: AuditReport
}

export function AuditList({ report }: Props) {
  const sections: Array<{ title: string; items: AuditItem[]; emptyHint?: string }> = [
    { title: '已配置', items: report.applied, emptyHint: '尚无配置项' },
    { title: '配置偏离', items: report.drift },
    { title: '缺失 / 待补', items: report.missing },
    {
      title: '低于推荐 (硬件限制)',
      items: report.below_recommended,
    },
  ]
  const visible = sections.filter((s) => s.items.length > 0)
  if (visible.length === 0) {
    return <div className="text-[13px] text-subtle">{report.notes.join(' / ') || '无内容'}</div>
  }

  return (
    <div className="flex flex-col gap-4">
      {visible.map((sec) => (
        <div key={sec.title}>
          <div className="text-[11px] uppercase tracking-wide text-subtle mb-2">
            {sec.title} ({sec.items.length})
          </div>
          <div className="flex flex-col gap-1">
            {sec.items.map((it) => (
              <div
                key={it.key}
                className="flex items-center justify-between gap-3 px-3 py-2 rounded border border-border-soft bg-card"
              >
                <div className="flex flex-col min-w-0 flex-1">
                  <div className="text-[13px] text-foreground truncate">
                    {it.label}
                  </div>
                  <div className="text-[11px] text-subtle truncate">
                    期望: {it.expected}
                    {it.actual && <span className="ml-2">| 当前: {it.actual}</span>}
                  </div>
                </div>
                <Pill tone={auditStatusTone(it.status)}>
                  {auditStatusLabel(it.status)}
                </Pill>
              </div>
            ))}
          </div>
        </div>
      ))}
      {report.notes.length > 0 && (
        <div className="text-[11px] text-subtle">
          {report.notes.map((n, i) => (
            <div key={i}>· {n}</div>
          ))}
        </div>
      )}
    </div>
  )
}
