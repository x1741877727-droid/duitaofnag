// Step 4: 调 /api/perf/apply + 实时进度条 + log + 完成后 refetch emulators.

import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { useAppStore } from '@/lib/store'
import type { ApplyTask, RuntimeMode } from '../types'
import { wizardApi, pollApplyUntilDone } from '../wizardApi'
import { cn } from '@/lib/utils'

interface Props {
  targetCount: number
  mode: RuntimeMode
  onDone: () => void
  onCancel: () => void
}

export function StepApply({ targetCount, mode, onDone, onCancel }: Props) {
  const [task, setTask] = useState<ApplyTask | null>(null)
  const [error, setError] = useState<string | null>(null)
  const startedRef = useRef(false)
  const logEndRef = useRef<HTMLDivElement | null>(null)
  const setEmulators = useAppStore((s) => s.setEmulators)

  useEffect(() => {
    if (startedRef.current) return
    startedRef.current = true
    ;(async () => {
      try {
        // 1) 切 mode (持久化 runtime profile, 立即生效)
        try {
          await wizardApi.setMode(mode)
        } catch (e) {
          console.warn('[wizard] setMode 失败:', e)
        }
        // 2) 触发 apply
        const { task_id } = await wizardApi.apply(targetCount)
        await pollApplyUntilDone(task_id, (t) => setTask(t))
        // 3) 完成后刷新模拟器列表 (实例数 / RAM / CPU 可能变了)
        try {
          const res = await fetch('/api/emulators')
          if (res.ok) {
            const data = await res.json()
            if (Array.isArray(data?.emulators)) setEmulators(data.emulators)
            else if (Array.isArray(data)) setEmulators(data)
          }
        } catch (e) {
          console.warn('[wizard] refetch /api/emulators 失败:', e)
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      }
    })()
  }, [targetCount, mode, setEmulators])

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [task?.progress.length])

  const finished = task?.status === 'done'
  const success = finished && task?.result?.success === true
  const totalSteps = task?.estimate?.total_steps ?? 0
  const doneSteps = task?.progress.length ?? 0
  const estSeconds = task?.estimate?.est_seconds ?? 0
  // 进度百分比: 优先按步数, fallback 按 elapsed/est_seconds (但不超 95% 直到 finished)
  let pct = 0
  if (finished) {
    pct = success ? 100 : Math.max(20, Math.min(95, doneSteps / Math.max(1, totalSteps) * 100))
  } else if (totalSteps > 0) {
    pct = Math.min(95, (doneSteps / totalSteps) * 100)
  } else if (task && estSeconds > 0) {
    pct = Math.min(90, (task.elapsed_s / estSeconds) * 100)
  }

  return (
    <div className="flex flex-col gap-4">
      {/* 顶部状态行 */}
      <div className="flex items-center justify-between">
        <div className="text-[13px]">
          {finished
            ? success
              ? '应用完成'
              : '应用失败'
            : '正在应用配置 (关 LDPlayer 改设置 重启 系统调优)'}
        </div>
        <div className="text-[11px] text-subtle font-mono tabular-nums">
          {task ? `${task.elapsed_s.toFixed(1)}s` : '0.0s'}
          {estSeconds > 0 && !finished && (
            <span className="ml-1 text-fainter">/ ~{estSeconds}s</span>
          )}
        </div>
      </div>

      {/* 进度条 */}
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center justify-between text-[11px] text-subtle">
          <span>
            {totalSteps > 0 ? (
              <>
                <span className="font-mono tabular-nums">{doneSteps}</span>
                <span className="text-fainter"> / </span>
                <span className="font-mono tabular-nums">{totalSteps}</span>
                <span className="ml-1">步</span>
              </>
            ) : (
              '准备中…'
            )}
          </span>
          <span className="font-mono tabular-nums">{pct.toFixed(0)}%</span>
        </div>
        <div className="h-2 rounded-full bg-secondary overflow-hidden">
          <div
            className={cn(
              'h-full transition-[width] duration-500 ease-out',
              !finished && 'bg-foreground',
              finished && success && 'bg-live',
              finished && !success && 'bg-error',
            )}
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>

      {/* 即将做 / 跳过 摘要 (running 时显示) */}
      {!finished && task?.estimate && (
        <div className="grid grid-cols-2 gap-3 text-[11px]">
          <div className="rounded border border-border-soft bg-card p-2">
            <div className="text-subtle uppercase tracking-wide mb-1">将执行</div>
            <ul className="text-foreground leading-relaxed">
              {task.estimate.will_run.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
              {task.estimate.will_run.length === 0 && (
                <li className="text-subtle">无</li>
              )}
            </ul>
          </div>
          <div className="rounded border border-border-soft bg-card p-2">
            <div className="text-subtle uppercase tracking-wide mb-1">跳过</div>
            <ul className="text-subtle leading-relaxed">
              {task.estimate.will_skip.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
              {task.estimate.will_skip.length === 0 && (
                <li className="text-fainter">无</li>
              )}
            </ul>
          </div>
        </div>
      )}

      {/* 进度日志 */}
      <div
        className={cn(
          'flex-1 max-h-[280px] overflow-y-auto',
          'rounded border border-border bg-secondary/40 p-3',
          'font-mono text-[11px] leading-[1.6]',
        )}
      >
        {(!task || task.progress.length === 0) && (
          <div className="text-subtle">等待 backend 响应…</div>
        )}
        {task?.progress.map((p, i) => (
          <div key={i} className="flex gap-2">
            <span className="text-fainter shrink-0 w-32 truncate">[{p.step_id}]</span>
            <span className="text-foreground">{p.message}</span>
          </div>
        ))}
        {error && <div className="text-error">错误: {error}</div>}
        <div ref={logEndRef} />
      </div>

      {/* 完成后摘要 */}
      {finished && task?.result && (
        <div className="border-t border-border-soft pt-3">
          {task.result.success ? (
            <div className="flex flex-col gap-2 text-[12px]">
              <div className="text-foreground">
                完成 {task.result.changes.length} 项改动 (耗时{' '}
                <span className="font-mono tabular-nums">
                  {task.result.duration_s.toFixed(1)}s
                </span>
                ):
              </div>
              <ul className="flex flex-col gap-1 text-ink2">
                {task.result.changes.map((c, i) => (
                  <li key={i} className="flex items-start gap-2">
                    <span className="text-subtle font-mono w-12 shrink-0">
                      [{c.category}]
                    </span>
                    <span>{c.label}</span>
                  </li>
                ))}
              </ul>
              {task.result.skipped.length > 0 && (
                <div className="mt-2 text-subtle">
                  跳过:
                  {task.result.skipped.map((s, i) => (
                    <div key={i} className="ml-4">- {s}</div>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div className="text-[12px] text-error">
              失败原因: {task.result.error || '未知'}
            </div>
          )}
        </div>
      )}

      <div className="flex items-center justify-end gap-2 pt-2">
        {!finished && (
          <Button variant="outline" onClick={onCancel}>
            取消并关闭
          </Button>
        )}
        {finished && (
          <Button onClick={onDone}>{success ? '完成' : '关闭'}</Button>
        )}
      </div>
    </div>
  )
}
