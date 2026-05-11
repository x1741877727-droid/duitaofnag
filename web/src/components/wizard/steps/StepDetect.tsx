// Step 1: 检测硬件 — 显示 CPU/RAM/GPU/Disk + LDPlayer 实例数.

import { useEffect } from 'react'
import { Pill } from '@/components/ui/pill'
import { Button } from '@/components/ui/button'
import type { AuditResponse } from '../types'
import { tierTone, tierLabel } from '../helpers'

interface Props {
  targetCount: number
  audit: AuditResponse | null
  loading: boolean
  error: string | null
  onRefresh: () => void
  onNext: () => void
}

export function StepDetect({
  targetCount,
  audit,
  loading,
  error,
  onRefresh,
  onNext,
}: Props) {
  useEffect(() => {
    if (!audit && !loading && !error) onRefresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (loading) {
    return (
      <div className="flex flex-col items-center gap-4 py-16">
        <div className="h-8 w-8 rounded-full border-2 border-border border-t-foreground animate-spin" />
        <div className="text-[13px] text-subtle">正在检测硬件…</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex flex-col gap-3 py-8">
        <div className="text-[13px] text-error">检测失败: {error}</div>
        <Button variant="outline" onClick={onRefresh} className="self-start">
          重试
        </Button>
      </div>
    )
  }

  if (!audit) return null

  const { hardware: hw, expected_plan: plan } = audit
  return (
    <div className="flex flex-col gap-5">
      <div>
        <div className="text-[12px] uppercase tracking-wide text-subtle mb-2">
          硬件
        </div>
        <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-[13px]">
          <Row k="CPU" v={`${hw.cpu_name}`} mono={false} />
          <Row k="核数" v={`${hw.cpu_physical} 物理 / ${hw.cpu_logical} 逻辑${hw.cpu_has_ht ? ' · HT' : ''}`} />
          <Row k="内存" v={`${hw.ram_gb.toFixed(1)} GB (free ${hw.ram_free_gb.toFixed(1)})`} />
          <Row k="GPU" v={hw.gpu_name} mono={false} />
          <Row k="磁盘" v={hw.disk_type} />
          <Row k="LDPlayer" v={hw.ldplayer_path || '(未检测到)'} mono={false} />
        </div>
      </div>

      <div className="border-t border-border-soft pt-4">
        <div className="text-[12px] uppercase tracking-wide text-subtle mb-2">
          实例
        </div>
        <div className="text-[13px]">
          检测到 <span className="font-medium tabular-nums">{hw.instance_count_existing}</span> 个 LDPlayer 实例
          {hw.instance_indexes.length > 0 && (
            <span className="text-subtle ml-2 font-mono text-[12px]">
              [{hw.instance_indexes.join(', ')}]
            </span>
          )}
        </div>
      </div>

      <div className="border-t border-border-soft pt-4">
        <div className="text-[12px] uppercase tracking-wide text-subtle mb-2">
          初步评估 (按 {targetCount} 实例 PUBG 计算)
        </div>
        <div className="flex items-center gap-3">
          <Pill tone={tierTone(plan.tier)}>{tierLabel(plan.tier)}</Pill>
          <span className="text-[13px] text-ink2">
            估闪退率 <span className="font-mono tabular-nums">{plan.estimated_crash_rate_pct}%</span>
          </span>
        </div>
        {plan.warnings.length > 0 && (
          <ul className="mt-3 flex flex-col gap-1 text-[12px] text-warn">
            {plan.warnings.map((w, i) => (
              <li key={i}>· {w}</li>
            ))}
          </ul>
        )}
      </div>

      <div className="flex items-center justify-end gap-2 mt-2">
        <Button variant="outline" onClick={onRefresh}>重新检测</Button>
        <Button onClick={onNext}>下一步</Button>
      </div>
    </div>
  )
}

function Row({ k, v, mono = true }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className="flex items-baseline gap-2">
      <span className="text-subtle w-14 shrink-0">{k}</span>
      <span className={mono ? 'font-mono text-[12px] tabular-nums text-foreground' : 'text-foreground'}>{v}</span>
    </div>
  )
}
