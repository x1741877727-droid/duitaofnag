/**
 * PerfOptimizeWizard — 硬件性能优化向导 modal.
 *
 * 4 状态机:
 *   detecting  → 探测硬件 (loading)
 *   plan       → 显示推荐 (CTA: 立即优化 / 跳过)
 *   applying   → 实时进度 (poll /api/perf/apply/{tid})
 *   done       → 改动清单 + GUI 提示 (CTA: 完成)
 *
 * 风格: 跟 DataShell / RecognitionShell 同 — C.surface + C.border + Inter UI + IBM Plex Mono.
 * 设计文档: docs/PERF_TUNING.md
 */

import { useEffect, useRef, useState } from 'react'
import { C } from '@/lib/design-tokens'
import {
  detectAndPlan,
  applyPlan,
  pollApplyStatus,
  type HardwareInfo,
  type OptimizePlan,
  type AppliedResult,
  type PerfTaskStatus,
} from '@/lib/perfApi'

type Stage = 'detecting' | 'plan' | 'applying' | 'done' | 'error'
const TARGET_COUNT = 12

interface Props {
  open: boolean
  onClose: () => void
  /** 用户是否首次自动浮出来的 (vs 手动从设置页打开). 影响"跳过"文案. */
  autoTrigger?: boolean
}

export function PerfOptimizeWizard({ open, onClose, autoTrigger }: Props) {
  const [stage, setStage] = useState<Stage>('detecting')
  const [hw, setHw] = useState<HardwareInfo | null>(null)
  const [plan, setPlan] = useState<OptimizePlan | null>(null)
  const [taskId, setTaskId] = useState<string>('')
  const [progress, setProgress] = useState<PerfTaskStatus['progress']>([])
  const [result, setResult] = useState<AppliedResult | null>(null)
  const [errMsg, setErrMsg] = useState<string>('')
  const pollTimerRef = useRef<number | null>(null)

  // 进入 detecting 阶段
  useEffect(() => {
    if (!open) return
    setStage('detecting')
    setErrMsg('')
    setHw(null); setPlan(null); setProgress([]); setResult(null); setTaskId('')
    let alive = true
    detectAndPlan(TARGET_COUNT)
      .then((r) => {
        if (!alive) return
        setHw(r.hardware); setPlan(r.plan); setStage('plan')
      })
      .catch((e) => {
        if (!alive) return
        setErrMsg(String(e)); setStage('error')
      })
    return () => { alive = false }
  }, [open])

  // applying poll loop
  useEffect(() => {
    if (stage !== 'applying' || !taskId) return
    let alive = true
    const tick = async () => {
      try {
        const st = await pollApplyStatus(taskId)
        if (!alive) return
        setProgress(st.progress)
        if (st.status === 'done') {
          setResult(st.result)
          setStage(st.result?.success ? 'done' : 'error')
          if (st.result && !st.result.success) setErrMsg(st.result.error || '未知错误')
          return
        }
      } catch (e) {
        if (!alive) return
        setErrMsg(String(e)); setStage('error'); return
      }
      pollTimerRef.current = window.setTimeout(tick, 1500)
    }
    tick()
    return () => {
      alive = false
      if (pollTimerRef.current !== null) {
        window.clearTimeout(pollTimerRef.current)
        pollTimerRef.current = null
      }
    }
  }, [stage, taskId])

  if (!open) return null

  const handleStart = async () => {
    try {
      const r = await applyPlan(TARGET_COUNT)
      setTaskId(r.task_id); setStage('applying')
    } catch (e) {
      setErrMsg(String(e)); setStage('error')
    }
  }

  const handleClose = () => {
    onClose()
  }

  return (
    <Backdrop onClose={stage === 'applying' ? undefined : handleClose}>
      <div
        style={{
          background: C.surface,
          border: `1px solid ${C.border}`,
          borderRadius: 12,
          width: 'min(92vw, 760px)',
          maxHeight: '88vh',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          boxShadow: '0 12px 40px rgba(0,0,0,.18), 0 2px 6px rgba(0,0,0,.08)',
          fontFamily: C.fontUi,
        }}
      >
        <Header stage={stage} onClose={handleClose} disableClose={stage === 'applying'} />
        <div style={{ flex: 1, overflow: 'auto', padding: '18px 22px' }}>
          {stage === 'detecting' && <DetectingView />}
          {stage === 'plan' && hw && plan && <PlanView hw={hw} plan={plan} />}
          {stage === 'applying' && <ApplyingView progress={progress} plan={plan} />}
          {stage === 'done' && result && <DoneView result={result} />}
          {stage === 'error' && <ErrorView msg={errMsg} />}
        </div>
        <Footer
          stage={stage}
          autoTrigger={autoTrigger}
          plan={plan}
          onSkip={handleClose}
          onStart={handleStart}
          onClose={handleClose}
        />
      </div>
    </Backdrop>
  )
}

// ─────────────── 子组件 ───────────────

function Backdrop({
  children,
  onClose,
}: {
  children: React.ReactNode
  onClose?: () => void
}) {
  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(20, 18, 14, 0.55)',
        backdropFilter: 'blur(2px)',
        zIndex: 9999,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 20,
      }}
    >
      <div onClick={(e) => e.stopPropagation()} style={{ display: 'contents' }}>
        {children}
      </div>
    </div>
  )
}

function Header({
  stage,
  onClose,
  disableClose,
}: {
  stage: Stage
  onClose: () => void
  disableClose: boolean
}) {
  const dots = ['detecting', 'plan', 'applying', 'done'] as const
  const stageIdx = dots.indexOf(stage as typeof dots[number])
  return (
    <div
      style={{
        padding: '14px 22px',
        borderBottom: `1px solid ${C.borderSoft}`,
        display: 'flex',
        alignItems: 'center',
        gap: 14,
      }}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4, flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 14.5, fontWeight: 600, color: C.ink }}>
          硬件性能优化向导
        </div>
        <div style={{ fontSize: 11, color: C.ink3 }}>
          自动探测你的电脑 + 应用最优 LDPlayer / Android 配置
        </div>
      </div>
      {/* progress dots */}
      <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
        {dots.map((d, i) => {
          const active = stageIdx >= i && stageIdx >= 0
          const cur = stageIdx === i
          return (
            <div
              key={d}
              style={{
                width: cur ? 18 : 7,
                height: 7,
                borderRadius: 4,
                background: active ? C.ink : C.border,
                transition: 'all .2s',
              }}
            />
          )
        })}
      </div>
      <button
        type="button"
        onClick={disableClose ? undefined : onClose}
        disabled={disableClose}
        title={disableClose ? '正在优化, 请等完成' : '关闭'}
        style={{
          background: 'transparent', border: 'none',
          fontSize: 16, cursor: disableClose ? 'not-allowed' : 'pointer',
          color: disableClose ? C.ink4 : C.ink3, padding: '4px 8px',
          fontFamily: 'inherit',
        }}
      >×</button>
    </div>
  )
}

function DetectingView() {
  return (
    <div style={{ padding: '40px 0', textAlign: 'center', color: C.ink3 }}>
      <div style={{
        display: 'inline-block', width: 28, height: 28,
        border: `2px solid ${C.border}`, borderTopColor: C.ink,
        borderRadius: '50%', animation: 'spin 0.8s linear infinite',
      }} />
      <div style={{ marginTop: 14, fontSize: 13 }}>正在探测硬件…</div>
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  )
}

function PlanView({ hw, plan }: { hw: HardwareInfo; plan: OptimizePlan }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <Section title="检测到的硬件">
        <KvGrid items={[
          ['CPU', `${hw.cpu_name || '?'} (${hw.cpu_physical}C/${hw.cpu_logical}T${hw.cpu_has_ht ? ', HT' : ''}${hw.cpu_sockets > 1 ? `, ${hw.cpu_sockets} socket` : ''})`],
          ['RAM', `${hw.ram_gb} GB (free ${hw.ram_free_gb} GB)`],
          ['GPU', hw.gpu_name],
          ['磁盘', hw.disk_type],
          ['LDPlayer 实例', `${hw.instance_count_existing} 个 (index=${hw.instance_indexes.join(',') || '-'})`],
        ]} />
      </Section>

      <Section title={`推荐配置 ${plan.fits_target ? '' : '(已自动降级)'}`} accent={!plan.fits_target}>
        <KvGrid items={[
          ['实例数', `${plan.instance_count} / ${TARGET_COUNT}${plan.fits_target ? ' (目标全开)' : ' (硬件上限)'}`],
          ['每实例 RAM', `${plan.ram_per_inst_mb} MB`],
          ['每实例 CPU', `${plan.cpu_per_inst} 核 (mask 序列 ${plan.masks.length} 项)`],
          ['启动间隔', `${plan.startup_interval_s} s (按磁盘类型自动)`],
          ['GPU 模式', `${plan.gpu_mode_recommendation} ★ 需 GUI 手动设`],
        ]} />
      </Section>

      {plan.warnings.length > 0 && (
        <Section title="警告">
          <ul style={{ margin: 0, paddingLeft: 18, color: C.warn, fontSize: 12 }}>
            {plan.warnings.map((w, i) => <li key={i} style={{ marginBottom: 4 }}>{w}</li>)}
          </ul>
        </Section>
      )}

      <div style={{ fontSize: 11.5, color: C.ink4, lineHeight: 1.6, padding: '8px 12px', background: C.surface2, borderRadius: 6 }}>
        预计耗时 ~60s · 影响所有 LDPlayer 实例 (会重启)<br />
        过程中 backend 仍可用; 你的 ROI / 模板 / 决策档案不会被改动.
      </div>
    </div>
  )
}

function ApplyingView({
  progress,
  plan,
}: {
  progress: PerfTaskStatus['progress']
  plan: OptimizePlan | null
}) {
  // 总步数估算: 1 quitall + 1 globalsetting + N modify + N launch + 1 wait + 1 bind + N adb
  const totalSteps = plan ? (3 + plan.instance_count * 3) : 30
  const pct = Math.min(99, Math.round((progress.length / totalSteps) * 100))
  const last = progress[progress.length - 1]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {/* progress bar */}
      <div style={{ width: '100%', height: 6, background: C.surface2, borderRadius: 3, overflow: 'hidden' }}>
        <div style={{
          width: `${pct}%`, height: '100%', background: C.ink,
          transition: 'width .3s',
        }} />
      </div>
      <div style={{
        fontSize: 12, color: C.ink2, fontFamily: C.fontMono,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <span>{last?.message || '准备中…'}</span>
        <span>{pct}% · {progress.length}/{totalSteps}</span>
      </div>

      {/* step list (last 12) */}
      <div style={{
        background: C.surface2, borderRadius: 6, padding: '10px 12px',
        maxHeight: 280, overflowY: 'auto', fontFamily: C.fontMono, fontSize: 11.5,
      }}>
        {progress.slice(-15).map((p, i) => (
          <div key={i} style={{
            padding: '3px 0', color: C.ink2,
            display: 'flex', gap: 8, alignItems: 'baseline',
          }}>
            <span style={{ color: C.live, width: 10 }}>✓</span>
            <span style={{ color: C.ink4, width: 75 }}>{p.step_id}</span>
            <span style={{ flex: 1 }}>{p.message}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function DoneView({ result }: { result: AppliedResult }) {
  const byCategory = result.changes.reduce<Record<string, typeof result.changes>>((acc, c) => {
    (acc[c.category] = acc[c.category] || []).push(c); return acc
  }, {})
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{
        fontSize: 14, color: C.live, fontWeight: 600,
        display: 'flex', alignItems: 'center', gap: 8,
      }}>
        <span style={{
          display: 'inline-block', width: 18, height: 18, borderRadius: '50%',
          background: C.live, color: '#fff', textAlign: 'center', lineHeight: '18px', fontSize: 11,
        }}>✓</span>
        优化完成 · 用时 {result.duration_s}s
      </div>

      {Object.entries(byCategory).map(([cat, items]) => (
        <Section key={cat} title={cat}>
          <ul style={{ margin: 0, paddingLeft: 0, listStyle: 'none' }}>
            {items.map((c, i) => (
              <li key={i} style={{
                padding: '6px 0', borderBottom: `1px solid ${C.borderSoft}`,
                fontSize: 12, color: C.ink2,
                display: 'flex', alignItems: 'baseline', gap: 8,
              }}>
                <span style={{ color: C.live, fontSize: 10, width: 10 }}>●</span>
                <div style={{ flex: 1 }}>
                  <div>{c.label}</div>
                  {c.detail && (
                    <div style={{
                      fontFamily: C.fontMono, fontSize: 10.5, color: C.ink4, marginTop: 2,
                    }}>{c.detail}</div>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </Section>
      ))}

      {result.skipped.length > 0 && (
        <Section title="还需要你手动操作 (重要)">
          <ul style={{ margin: 0, paddingLeft: 0, listStyle: 'none' }}>
            {result.skipped.map((s, i) => (
              <li key={i} style={{
                padding: '6px 10px', background: C.warnSoft, borderRadius: 5,
                marginBottom: 6, fontSize: 12, color: '#92400e',
              }}>★ {s}</li>
            ))}
          </ul>
        </Section>
      )}

      <div style={{
        padding: '10px 12px', background: C.surface2, borderRadius: 6,
        fontSize: 11.5, color: C.ink3, lineHeight: 1.7,
      }}>
        <b style={{ color: C.ink2 }}>预估收益</b><br />
        · 单实例 fps 稳 30 (绑核 + audio off)<br />
        · 单实例 RAM 留 ~800 MB 缓冲, 不再被 LMK 杀<br />
        · {result.plan.instance_count} 实例并发不卡 (跨核 cache 优化)<br />
        · VM 死自动 30s 重启 (vm_watchdog 已生效)
      </div>
    </div>
  )
}

function ErrorView({ msg }: { msg: string }) {
  return (
    <div style={{ padding: '30px 0' }}>
      <div style={{
        color: C.error, fontSize: 14, fontWeight: 600, marginBottom: 8,
      }}>✕ 优化失败</div>
      <div style={{
        fontFamily: C.fontMono, fontSize: 11.5, color: C.ink2,
        background: C.errorBg, padding: '10px 12px', borderRadius: 6,
        whiteSpace: 'pre-wrap', wordBreak: 'break-all',
      }}>{msg || '未知错误'}</div>
      <div style={{ marginTop: 14, fontSize: 12, color: C.ink3 }}>
        排查: 看 backend 日志 (logs/be_dev.err) 找 [perf_optimizer] 关键字.
      </div>
    </div>
  )
}

function Footer({
  stage,
  autoTrigger,
  plan,
  onSkip,
  onStart,
  onClose,
}: {
  stage: Stage
  autoTrigger?: boolean
  plan: OptimizePlan | null
  onSkip: () => void
  onStart: () => void
  onClose: () => void
}) {
  return (
    <div style={{
      padding: '12px 22px',
      borderTop: `1px solid ${C.borderSoft}`,
      display: 'flex',
      alignItems: 'center',
      gap: 10,
      background: C.surface,
    }}>
      {stage === 'plan' && (
        <>
          <span style={{ fontSize: 11, color: C.ink4, flex: 1 }}>
            {autoTrigger ? '首次使用建议优化, 不影响生产' : '可重复跑, 上次配置会被覆盖'}
          </span>
          <GhostButton onClick={onSkip}>跳过</GhostButton>
          <PrimaryButton onClick={onStart} disabled={!plan || plan.instance_count < 1}>
            立即优化
          </PrimaryButton>
        </>
      )}
      {stage === 'applying' && (
        <span style={{ fontSize: 11.5, color: C.ink3, flex: 1 }}>
          正在跑, 不要关窗口…
        </span>
      )}
      {stage === 'done' && (
        <>
          <span style={{ flex: 1 }} />
          <PrimaryButton onClick={onClose}>完成</PrimaryButton>
        </>
      )}
      {stage === 'error' && (
        <>
          <span style={{ flex: 1 }} />
          <GhostButton onClick={onClose}>关闭</GhostButton>
        </>
      )}
      {stage === 'detecting' && <span style={{ fontSize: 11.5, color: C.ink4 }}>探测中…</span>}
    </div>
  )
}

// ─────────────── 通用小组件 ───────────────

function Section({
  title,
  children,
  accent,
}: {
  title: string
  children: React.ReactNode
  accent?: boolean
}) {
  return (
    <div>
      <div style={{
        fontSize: 11, fontWeight: 600, color: accent ? C.warn : C.ink3,
        textTransform: 'uppercase', letterSpacing: '.05em', marginBottom: 8,
      }}>{title}</div>
      {children}
    </div>
  )
}

function KvGrid({ items }: { items: Array<[string, string]> }) {
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '6px 18px',
      fontSize: 12.5, alignItems: 'baseline',
    }}>
      {items.map(([k, v]) => (
        <>
          <div key={`k-${k}`} style={{ color: C.ink3, fontSize: 11.5 }}>{k}</div>
          <div key={`v-${k}`} style={{ color: C.ink, fontFamily: C.fontMono }}>{v}</div>
        </>
      ))}
    </div>
  )
}

function PrimaryButton({
  onClick, children, disabled,
}: { onClick?: () => void; children: React.ReactNode; disabled?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      style={{
        padding: '7px 16px', borderRadius: 6, fontSize: 12.5, fontWeight: 500,
        color: '#fff', background: disabled ? C.ink4 : C.ink, border: 'none',
        cursor: disabled ? 'not-allowed' : 'pointer', fontFamily: 'inherit',
      }}
    >{children}</button>
  )
}

function GhostButton({
  onClick, children,
}: { onClick?: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        padding: '7px 14px', borderRadius: 6, fontSize: 12.5, fontWeight: 500,
        color: C.ink2, background: C.surface, border: `1px solid ${C.border}`,
        cursor: 'pointer', fontFamily: 'inherit',
      }}
    >{children}</button>
  )
}
