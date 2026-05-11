// Step 3: 推荐 plan + audit 增量列表 + 警告 + 应用按钮.

import { Pill } from '@/components/ui/pill'
import { Button } from '@/components/ui/button'
import type { AuditResponse, RuntimeMode } from '../types'
import { tierTone, tierLabel, MODE_LABEL } from '../helpers'
import { AuditList } from '../AuditList'

interface Props {
  audit: AuditResponse
  mode: RuntimeMode
  targetCount: number
  onBack: () => void
  onApply: () => void
}

export function StepRecommend({ audit, mode, targetCount, onBack, onApply }: Props) {
  const { expected_plan: plan, report } = audit
  return (
    <div className="flex flex-col gap-5">
      {/* 推荐配置概览 */}
      <div className="rounded-md border border-border bg-secondary/50 p-4">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Pill tone={tierTone(plan.tier)}>{tierLabel(plan.tier)}</Pill>
            <span className="text-[11px] text-subtle">
              {targetCount} 实例 · {MODE_LABEL[mode]} 模式
            </span>
          </div>
          <span className="text-[12px] text-subtle">
            估闪退率{' '}
            <span className="font-mono tabular-nums text-foreground">
              {plan.estimated_crash_rate_pct}%
            </span>
          </span>
        </div>
        <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 text-[12px]">
          <Item k="RAM 每实例" v={`${plan.ram_per_inst_mb} MB`} />
          <Item k="CPU 每实例" v={`${plan.cpu_per_inst} 核`} />
          <Item k="分辨率" v={`${plan.resolution_w}×${plan.resolution_h} (锁死)`} />
          <Item k="DPI" v={`${plan.dpi} (锁死)`} />
          <Item k="启动间隔" v={`${plan.startup_interval_s}s`} />
          <Item k="GPU 模式" v={plan.gpu_mode_recommendation} mono={false} />
        </div>
      </div>

      {/* 警告区 */}
      {plan.warnings.length > 0 && (
        <div className="rounded-md border border-warn/30 bg-warn-soft p-3 text-[12px] text-warn">
          {plan.warnings.map((w, i) => (
            <div key={i} className="leading-relaxed">
              {w}
            </div>
          ))}
        </div>
      )}

      {/* Audit 增量 */}
      <div>
        <div className="text-[12px] uppercase tracking-wide text-subtle mb-2">
          当前 LDPlayer 实际配置审计
        </div>
        <AuditList report={report} />
      </div>

      <div className="flex items-center justify-between pt-2 border-t border-border-soft">
        <Button variant="outline" onClick={onBack}>
          上一步
        </Button>
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-subtle">
            点应用会关闭所有 LDPlayer 重新配置 + 启动
          </span>
          <Button onClick={onApply}>一键应用</Button>
        </div>
      </div>
    </div>
  )
}

function Item({ k, v, mono = true }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className="flex items-baseline gap-2">
      <span className="text-subtle w-20 shrink-0">{k}</span>
      <span
        className={
          mono
            ? 'font-mono text-[12px] tabular-nums text-foreground'
            : 'text-foreground'
        }
      >
        {v}
      </span>
    </div>
  )
}
