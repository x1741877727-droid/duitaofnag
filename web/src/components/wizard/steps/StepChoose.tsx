// Step 2: 选择实例数 + 性能模式.

import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import type { HardwareInfo, RuntimeMode, RuntimeProfile } from '../types'
import { MODE_LABEL, MODE_DESC } from '../helpers'
import { wizardApi } from '../wizardApi'
import { cn } from '@/lib/utils'

interface Props {
  targetCount: number
  mode: RuntimeMode
  hardware: HardwareInfo | null
  onTargetCountChange: (n: number) => void
  onModeChange: (m: RuntimeMode) => void
  onBack: () => void
  onNext: () => void
}

export function StepChoose({
  targetCount,
  mode,
  hardware,
  onTargetCountChange,
  onModeChange,
  onBack,
  onNext,
}: Props) {
  const [localCount, setLocalCount] = useState(String(targetCount))
  const parsed = parseInt(localCount, 10)
  const valid = !isNaN(parsed) && parsed >= 1 && parsed <= 20

  // 快速建议: 根据硬件给的 RAM/CPU 上限
  const ramLimit = hardware ? Math.floor((hardware.ram_gb - 4) / 3) : null
  const cpuLimit = hardware ? Math.floor((hardware.cpu_logical || 4) / 2) : null
  const safeMax = hardware ? Math.min(ramLimit ?? 6, cpuLimit ?? 6) : null

  // 拉 3 个 mode 的预设参数, 给透明化展示用
  const [presets, setPresets] = useState<Record<RuntimeMode, RuntimeProfile | null>>({
    stable: null,
    balanced: null,
    speed: null,
  })
  useEffect(() => {
    let cancelled = false
    ;(['stable', 'balanced', 'speed'] as RuntimeMode[]).forEach(async (m) => {
      try {
        const data = await wizardApi.previewPreset(m)
        if (!cancelled) setPresets((p) => ({ ...p, [m]: data.profile }))
      } catch {
        // 忽略
      }
    })
    return () => {
      cancelled = true
    }
  }, [])
  const cur = presets[mode]

  return (
    <div className="flex flex-col gap-6">
      {/* 实例数 */}
      <div>
        <label className="text-[13px] font-medium block mb-2">
          一般同时运行多少个模拟器?
        </label>
        <div className="flex items-center gap-3">
          <Input
            type="number"
            min={1}
            max={20}
            value={localCount}
            onChange={(e) => setLocalCount(e.target.value)}
            onBlur={() => {
              if (valid && parsed !== targetCount) onTargetCountChange(parsed)
            }}
            className="w-24 text-center font-mono tabular-nums"
          />
          <span className="text-[12px] text-subtle">
            项目硬性要求 6 实例;
            {safeMax !== null && (
              <span className="ml-1">
                你的机器稳定上限 ≈{' '}
                <span className="font-mono tabular-nums text-foreground">{safeMax}</span>{' '}
                (按 RAM / CPU 算)
              </span>
            )}
          </span>
        </div>
        {valid && safeMax !== null && parsed > safeMax && (
          <div className="mt-2 text-[12px] text-warn">
            选 {parsed} 个超过稳定上限 {safeMax}, 会有红色警告 (不阻拦)
          </div>
        )}
      </div>

      {/* 模式 */}
      <div>
        <div className="text-[13px] font-medium mb-2">优先级模式</div>
        <div className="grid grid-cols-3 gap-2">
          {(['stable', 'balanced', 'speed'] as RuntimeMode[]).map((m) => {
            const active = m === mode
            return (
              <button
                key={m}
                type="button"
                onClick={() => onModeChange(m)}
                className={cn(
                  'flex flex-col items-start gap-1.5 p-3 rounded-md text-left',
                  'border transition-colors duration-150 cursor-pointer',
                  active
                    ? 'border-foreground bg-secondary'
                    : 'border-border bg-card hover:border-ink3',
                )}
              >
                <div className="flex items-center gap-2">
                  <div
                    className={cn(
                      'h-3 w-3 rounded-full border-2 transition-colors duration-150',
                      active ? 'border-foreground bg-foreground' : 'border-ink3',
                    )}
                  />
                  <span className="text-[13px] font-medium">{MODE_LABEL[m]}</span>
                </div>
                <div className="text-[11px] text-subtle leading-tight">
                  {MODE_DESC[m]}
                </div>
              </button>
            )
          })}
        </div>
      </div>

      {/* 当前 mode 用户感知到的实际差异 (隐藏底层技术 jargon) */}
      {cur && (
        <div className="rounded border border-border-soft bg-secondary/40 p-3">
          <div className="text-[11px] uppercase tracking-wide text-subtle mb-2">
            {MODE_LABEL[mode]} 模式的实际差异
          </div>
          <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 text-[12px]">
            <Param
              k="反应速度"
              v={describeReactionSpeed(cur.p1_round_interval, cur.p2_round_interval)}
            />
            <Param k="并行任务数" v={String(cur.pool_max_workers)} />
            <Param
              k="后台监控"
              v={`${cur.daemon_target_fps} fps`}
            />
            <Param
              k="画面变化敏感度"
              v={describeMotionSens(cur.daemon_motion_threshold)}
            />
            <Param
              k="文字识别并发"
              v={`${cur.ocr_pool_min}-${cur.ocr_pool_max} 进程`}
            />
            <Param
              k="物体识别严格度"
              v={describeYoloStrictness(cur.yolo_conf)}
            />
          </div>
        </div>
      )}

      <div className="flex items-center justify-between pt-2">
        <Button variant="outline" onClick={onBack}>
          上一步
        </Button>
        <Button
          disabled={!valid}
          onClick={() => {
            if (valid && parsed !== targetCount) onTargetCountChange(parsed)
            onNext()
          }}
        >
          下一步
        </Button>
      </div>
    </div>
  )
}

function Param({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-baseline gap-2">
      <span className="text-subtle w-28 shrink-0">{k}</span>
      <span className="text-foreground truncate">{v}</span>
    </div>
  )
}

/** 用户可感知的描述, 隐藏 hamming distance 等技术细节. */
function describeReactionSpeed(p1: number, p2: number): string {
  // p1 0.1s 算"极快", 0.2s "快", 0.3+ "标准"
  if (p1 <= 0.15) return `极快 (启动 ${p1}s)`
  if (p1 <= 0.25) return `快 (启动 ${p1}s)`
  return `稳 (启动 ${p1}s)`
}

function describeMotionSens(threshold: number): string {
  // motion threshold: 越小越敏感 (4 严格, 6 中等, 8 松)
  if (threshold <= 4) return '高 (帧间小变化也响应)'
  if (threshold <= 6) return '中 (默认)'
  return '低 (节省算力)'
}

function describeYoloStrictness(conf: number): string {
  // yolo conf: 越高越严
  if (conf >= 0.30) return '严 (只留高置信)'
  if (conf >= 0.25) return '默认'
  return '宽松 (多检漏 / 误检率↑)'
}
