// 性能优化 Wizard — 4 步 modal.
//
// 用法 (在 dashboard 入口处):
//   const [open, setOpen] = useState(true)  // 第一次启动 open=true
//   <PerfWizard open={open} onClose={() => setOpen(false)} />
//
// 跳过逻辑由调用方决定 (e.g. 检测 audit needs_action=false 就不弹).

import { useEffect, useState } from 'react'
import type { AuditResponse, RuntimeMode, WizardStep } from './types'
import { wizardApi } from './wizardApi'
import { StepDetect } from './steps/StepDetect'
import { StepChoose } from './steps/StepChoose'
import { StepRecommend } from './steps/StepRecommend'
import { StepApply } from './steps/StepApply'
import { cn } from '@/lib/utils'

interface Props {
  open: boolean
  onClose: () => void
  /** 跳过 detect 步直接到 audit (重新打开 wizard 时用) */
  skipDetect?: boolean
}

export function PerfWizard({ open, onClose, skipDetect = false }: Props) {
  const [step, setStep] = useState<WizardStep>('detect')
  const [targetCount, setTargetCount] = useState<number>(6)
  const [mode, setMode] = useState<RuntimeMode>('balanced')
  const [audit, setAudit] = useState<AuditResponse | null>(null)
  const [auditLoading, setAuditLoading] = useState(false)
  const [auditError, setAuditError] = useState<string | null>(null)

  // 控制进入/退出动画 — open 变 true 时 mounted=true, false 时延迟 250ms 卸载让动画播完
  const [mounted, setMounted] = useState(false)
  const [show, setShow] = useState(false)
  useEffect(() => {
    if (open) {
      setMounted(true)
      // 下一帧切 show=true 才能触发 transition
      requestAnimationFrame(() => setShow(true))
    } else {
      setShow(false)
      const t = setTimeout(() => setMounted(false), 250)
      return () => clearTimeout(t)
    }
  }, [open])

  // step 切换的子动画 (淡入)
  const [stepShow, setStepShow] = useState(true)
  function gotoStep(s: WizardStep) {
    if (s === step) return
    setStepShow(false)
    // 短暂 fade-out 后切 step + 立即 fade-in
    setTimeout(() => {
      setStep(s)
      requestAnimationFrame(() => setStepShow(true))
    }, 120)
  }

  useEffect(() => {
    if (!open) return
    if (skipDetect) {
      gotoStep('choose')
      return
    }
    gotoStep('detect')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, skipDetect])

  // 触发 audit 拉数据 (detect step 进入 / 用户改 targetCount 都重拉)
  async function refreshAudit(count: number) {
    setAuditLoading(true)
    setAuditError(null)
    try {
      const data = await wizardApi.audit(count)
      setAudit(data)
    } catch (e) {
      setAuditError(e instanceof Error ? e.message : String(e))
    } finally {
      setAuditLoading(false)
    }
  }

  if (!mounted) return null

  return (
    <div
      className={cn(
        'fixed inset-0 z-[1000] flex items-center justify-center',
        'transition-[background-color,backdrop-filter] duration-200',
        show ? 'bg-black/40 backdrop-blur-[2px]' : 'bg-black/0',
      )}
      onClick={(e) => {
        if (e.target === e.currentTarget && step !== 'apply') onClose()
      }}
    >
      <div
        className={cn(
          'flex flex-col w-[720px] max-w-[92vw] max-h-[88vh]',
          'bg-card rounded-lg border border-border',
          'shadow-[0_20px_50px_-15px_rgba(0,0,0,0.25)]',
          'transition-[opacity,transform] duration-200 ease-out',
          show ? 'opacity-100 translate-y-0 scale-100' : 'opacity-0 translate-y-4 scale-[0.97]',
        )}
      >
        <Header step={step} onClose={onClose} />

        <div className="flex-1 overflow-y-auto px-6 py-5">
          <div
            className={cn(
              'transition-opacity duration-150 ease-out',
              stepShow ? 'opacity-100' : 'opacity-0',
            )}
          >
            {step === 'detect' && (
              <StepDetect
                targetCount={targetCount}
                audit={audit}
                loading={auditLoading}
                error={auditError}
                onRefresh={() => refreshAudit(targetCount)}
                onNext={() => gotoStep('choose')}
              />
            )}
            {step === 'choose' && (
              <StepChoose
                targetCount={targetCount}
                mode={mode}
                hardware={audit?.hardware ?? null}
                onTargetCountChange={(n) => {
                  setTargetCount(n)
                  refreshAudit(n)
                }}
                onModeChange={setMode}
                onBack={() => gotoStep('detect')}
                onNext={() => gotoStep('recommend')}
              />
            )}
            {step === 'recommend' && audit && (
              <StepRecommend
                audit={audit}
                mode={mode}
                targetCount={targetCount}
                onBack={() => gotoStep('choose')}
                onApply={() => gotoStep('apply')}
              />
            )}
            {step === 'apply' && (
              <StepApply
                targetCount={targetCount}
                mode={mode}
                onDone={() => {
                  gotoStep('done')
                  onClose()
                }}
                onCancel={onClose}
              />
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

function Header({ step, onClose }: { step: WizardStep; onClose: () => void }) {
  const steps: Array<{ id: WizardStep; label: string }> = [
    { id: 'detect', label: '1 检测' },
    { id: 'choose', label: '2 选择' },
    { id: 'recommend', label: '3 推荐' },
    { id: 'apply', label: '4 应用' },
  ]
  const currentIdx = steps.findIndex((s) => s.id === step)
  return (
    <div className="flex items-center justify-between px-6 py-4 border-b border-border-soft">
      <div className="flex items-center gap-4">
        <h2 className="text-[15px] font-semibold tracking-tight">硬件优化向导</h2>
        <div className="flex items-center gap-1.5">
          {steps.map((s, i) => (
            <div key={s.id} className="flex items-center gap-1.5">
              <span
                className={cn(
                  'text-[11px] tabular-nums',
                  i < currentIdx && 'text-subtle',
                  i === currentIdx && 'text-foreground font-medium',
                  i > currentIdx && 'text-fainter',
                )}
              >
                {s.label}
              </span>
              {i < steps.length - 1 && (
                <span
                  className={cn(
                    'h-px w-4',
                    i < currentIdx ? 'bg-foreground' : 'bg-border',
                  )}
                />
              )}
            </div>
          ))}
        </div>
      </div>
      {step !== 'apply' && (
        <button
          type="button"
          onClick={onClose}
          className="text-subtle hover:text-foreground text-[13px] cursor-pointer bg-transparent border-0 px-1"
        >
          跳过
        </button>
      )}
    </div>
  )
}
