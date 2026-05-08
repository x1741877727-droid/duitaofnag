import { cn } from '@/lib/utils'
import type { Instance, LiveDecisionEvent } from '@/lib/store'
import { Section } from './Section'

export interface TierEvidence {
  tier: 'T1' | 'T2' | 'T3' | 'T4' | 'T5'
  name: string
  hit: boolean
  skip: boolean
  conf: string
  detail: string
}

/**
 * 从最近一条决策派生 tier 占位数据.
 * 阶段 6 backend 补 `tier_evidence` 字段后, 改为读真数据.
 */
export function deriveTiers(decision?: LiveDecisionEvent): TierEvidence[] {
  const method = decision?.tap_method
  const ok = decision?.verify_success !== false
  const isTemplate = method === 'template'
  const isOcr = method === 'ocr'
  const isYolo = method === 'yolo'
  const isMemory = method === 'memory'
  return [
    {
      tier: 'T1',
      name: '模板',
      hit: isTemplate && ok,
      skip: !decision || (!isTemplate && !isOcr && !isYolo && !isMemory),
      conf: isTemplate ? '0.92' : '—',
      detail: isTemplate ? decision?.tap_target ?? '命中模板' : '未命中',
    },
    {
      tier: 'T2',
      name: 'OCR',
      hit: isOcr && ok,
      skip: !decision || isTemplate,
      conf: isOcr ? '0.88' : '—',
      detail: isOcr ? decision?.tap_target ?? '识别文本' : '跳过',
    },
    {
      tier: 'T3',
      name: 'YOLO',
      hit: isYolo && ok,
      skip: !decision || isTemplate || isOcr,
      conf: isYolo ? '0.71' : '—',
      detail: isYolo ? decision?.tap_target ?? '检出目标' : '跳过',
    },
    {
      tier: 'T4',
      name: 'Memory',
      hit: isMemory && ok,
      skip: !decision || (!isMemory && (isTemplate || isOcr || isYolo)),
      conf: isMemory ? '记忆' : '—',
      detail: isMemory ? '历史 tap 复用' : '无记忆',
    },
    {
      tier: 'T5',
      name: '兜底',
      hit: !decision ? false : !ok,
      skip: !decision ? true : ok,
      conf: '—',
      detail: ok ? '未启用' : '走兜底规则',
    },
  ]
}

/**
 * DetailTiers — 5 层识别证据展示.
 * 来源: detail.jsx DetailTiers.
 */
export function DetailTiers({
  inst,
  decision,
}: {
  inst: Instance
  decision?: LiveDecisionEvent
}) {
  const tiers = deriveTiers(decision)
  const hitCount = tiers.filter((t) => t.hit).length
  void inst
  return (
    <Section title="识别证据" subtitle={`${hitCount}/5 命中`}>
      <div className="rounded-lg overflow-hidden border border-border bg-card">
        {tiers.map((t, i) => (
          <div
            key={t.tier}
            className={cn(
              'grid items-center gap-2.5 px-2.5 py-2',
              'grid-cols-[36px_70px_24px_1fr_56px]',
              i > 0 && 'border-t border-border-soft',
              t.skip && 'opacity-55',
            )}
            style={{
              background: t.hit
                ? '#f6fdf8'
                : t.skip
                  ? 'var(--color-card)'
                  : '#fffaf5',
            }}
          >
            <span className="gb-mono text-[11px] font-semibold text-subtle">
              {t.tier}
            </span>
            <span className="text-[12px] font-medium text-muted-foreground">
              {t.name}
            </span>
            <span
              className="text-[13px] font-semibold"
              style={{
                color: t.skip
                  ? 'var(--color-fainter)'
                  : t.hit
                    ? 'var(--color-live)'
                    : 'var(--color-warn)',
              }}
            >
              {t.skip ? '·' : t.hit ? '✓' : '✗'}
            </span>
            <span className="gb-mono text-[11.5px] text-muted-foreground truncate">
              {t.detail}
            </span>
            <span
              className={cn(
                'gb-mono text-[11px] text-right',
                t.skip ? 'text-fainter' : 'text-muted-foreground',
              )}
            >
              {t.conf}
            </span>
          </div>
        ))}
      </div>
    </Section>
  )
}
