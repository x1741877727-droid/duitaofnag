/**
 * PhaseTester — dev-only 阶段测试面板.
 * 来源: phase-tester.jsx (设计原型 821 行, 此处实现核心闭环).
 *
 * 核心:
 *   - RUNNER_PHASES P0..P5 (含 role / needsId / captainOnly / single 元数据)
 *   - selKeys 多选 (chip, 顺序敏感, 串行跑)
 *   - selRole 单选 (仅当选中 P3a/P3b/P4 任一才显示)
 *   - expectedId 10 位数字 (仅当选中 P5 才显示, 必填校验)
 *   - keepGoing 失败是否继续
 *   - targets = useAppStore.selectedInstances (从主页卡选)
 *   - runTest → POST /api/runner/test_phase
 *   - cancel  → POST /api/runner/cancel
 *
 * 业务约束 (UI_CONTENT_SPEC §2.7.9):
 *   - P5 only captain (实际跑后端实现; UI 只在 selRole=member 时给警告)
 *   - 多实例 + ROLE_PHASES → 业务约束由后端切组逻辑处理
 *   - 主 runner 跑时禁测试 (isRunning=true → canRun=false)
 *
 * 简化 (设计原型 dev override, 后续增强):
 *   - per-instance roleOver / teamOver
 *   - drag-drop 改队
 *   - one-captain-per-team enforcement (移到 backend)
 */

import { useEffect, useMemo, useState } from 'react'
import { cn } from '@/lib/utils'
import { useAppStore, type AccountAssignment, type Instance } from '@/lib/store'
import { Kbd } from '@/components/ui/kbd'

const TEAM_TINT: Record<string, string> = {
  A: '#7c8ff5',
  B: '#5db8a4',
  C: '#d28a5c',
  D: '#b87dc8',
  E: '#c4a850',
  F: '#7da7d9',
}

interface PhaseDef {
  key: string
  name: string
  desc: string
  /** 该 phase 是否需要 role (captain/member) */
  role: boolean
  /** 仅 captain 跑这个 phase (其他 instance skip) */
  captainOnly?: boolean
  /** 该 phase 需要 expected_id (10 位数字) */
  needsId?: boolean
  /** 单实例 phase, 不参与多实例切组 */
  single?: boolean
}

const RUNNER_PHASES: PhaseDef[] = [
  { key: 'P0',  name: '加速器校验',   desc: 'TUN 链路自检',         role: false, single: true },
  { key: 'P1',  name: '启动游戏',     desc: '模拟器内拉起游戏到 Splash',   role: false, single: true },
  { key: 'P2',  name: '清理弹窗',     desc: '开局前所有 popup → lobby',    role: false, single: true },
  { key: 'P3a', name: '创建队伍',     desc: '队长建房 + 同步 scheme',      role: true },
  { key: 'P3b', name: '加入队伍',     desc: '队员凭 scheme 加入',          role: true },
  { key: 'P4',  name: '准备就绪',     desc: '队长收尾 / 队员举旗',         role: true },
  { key: 'P5',  name: '等待真人入队', desc: '盯着 ID 出现, 240s 超时',     role: false, needsId: true, captainOnly: true },
]

const ROLE_PHASES = new Set(['P3a', 'P3b', 'P4'])

interface ResultRow {
  phase: string
  phase_name: string
  ok: boolean
  duration_ms?: number
  error?: string
}

export function PhaseTester() {
  const isRunning = useAppStore((s) => s.isRunning)
  const selectedInstances = useAppStore((s) => s.selectedInstances)
  const instancesRecord = useAppStore((s) => s.instances)
  const accounts = useAppStore((s) => s.accounts)
  const phaseTester = useAppStore((s) => s.phaseTester)
  const setPhaseTester = useAppStore((s) => s.setPhaseTester)
  const addPhaseResult = useAppStore((s) => s.addPhaseResult)
  const clearPhaseResults = useAppStore((s) => s.clearPhaseResults)

  const [open, setOpen] = useState(true)
  const selKeys = phaseTester.selKeys
  const expectedId = phaseTester.expectedId
  const keepGoing = phaseTester.keepGoing
  const busy = phaseTester.busy
  const progress = phaseTester.progress
  const results = phaseTester.results

  // per-instance role override (Map<idx, 'captain'|'member'>) — chip 内 toggle 改这个
  const [roleOver, setRoleOver] = useState<Record<number, 'captain' | 'member'>>(
    {},
  )

  function effRole(idx: number, fallback: 'captain' | 'member'): 'captain' | 'member' {
    return roleOver[idx] ?? fallback
  }

  function toggleChipRole(
    inst: { index: number; group: string },
    fallback: 'captain' | 'member',
  ) {
    const cur = roleOver[inst.index] ?? fallback
    const becomingCaptain = cur !== 'captain'
    setRoleOver((prev) => {
      const next = { ...prev }
      if (becomingCaptain) {
        // demote any other captain in the same team (one-captain-per-team)
        for (const idx of targets) {
          if (idx === inst.index) continue
          const acct = accounts.find((a) => a.index === idx)
          const i2 = instancesRecord[String(idx)]
          if (!acct && !i2) continue
          const team2 = i2?.group ?? acct?.group
          if (team2 !== inst.group) continue
          const r2 = next[idx] ?? (acct?.role || 'captain')
          if (r2 === 'captain') next[idx] = 'member'
        }
        next[inst.index] = 'captain'
      } else {
        next[inst.index] = 'member'
      }
      return next
    })
  }

  function removeTarget(idx: number) {
    useAppStore.getState().setSelectedInstances(
      selectedInstances.filter((x) => x !== idx),
    )
  }

  // targets 严格 = 主页选择, 没选就 0 实例 (用户必须主动勾选)
  const targets = useMemo<number[]>(
    () => selectedInstances,
    [selectedInstances],
  )

  // 派生
  const needsId = useMemo(() => selKeys.includes('P5'), [selKeys])
  const idOk = !needsId || /^\d{10}$/.test(expectedId)

  const canRun =
    !busy && !isRunning && selKeys.length > 0 && targets.length > 0 && idOk

  const togglePhase = (k: string) => {
    setPhaseTester({
      selKeys: selKeys.includes(k)
        ? selKeys.filter((x) => x !== k)
        : [...selKeys, k],
    })
  }

  const runTest = async () => {
    if (!canRun) return
    // 快照 targets — 后续监控墙用 runningTargets, 跟用户在监控墙点卡片选择脱钩
    setPhaseTester({ busy: true, progress: '准备...', runningTargets: [...targets] })
    let okCount = 0
    let failCount = 0
    try {
      for (const phase of selKeys) {
        const meta = RUNNER_PHASES.find((p) => p.key === phase)
        if (!meta) continue

        // 业务约束: P5 only captain — 自动 skip 非队长 target
        const phaseTargets = meta.captainOnly
          ? targets.filter((idx) => {
              const acct = accounts.find((a) => a.index === idx)
              return acct?.role === 'captain'
            })
          : targets

        for (const tgt of phaseTargets) {
          setPhaseTester({ progress: `${phase} 跑中... #${tgt}` })
          const acct = accounts.find((a) => a.index === tgt)
          const fallbackRole = (acct?.role || 'captain') as 'captain' | 'member'
          const role = meta.role ? effRole(tgt, fallbackRole) : fallbackRole
          const t0 = Date.now()
          try {
            // 1) POST 启动后台 task
            const startRes = await fetch('/api/runner/test_phase', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                instance: tgt,
                phase,
                role,
                expected_id: phase === 'P5' ? expectedId : undefined,
                keep_going: keepGoing,
              }),
            })
            const startData = (await startRes.json()) as {
              ok?: boolean; task_id?: string; error?: string
            }
            if (!startData.task_id) {
              throw new Error(startData.error || 'task_id 缺失')
            }

            // 2) 轮询直到 done — 这里 await 才真等到 phase 跑完, 保证 phase/target 严格串行
            let data: { ok?: boolean; phase_name?: string; duration_ms?: number; error?: string } = {}
            const tid = startData.task_id
            const POLL_MS = 1000
            const MAX_WAIT_MS = 240_000   // 4 分钟单 phase 上限 (P2 max_seconds 180s + 余量)
            const tStart = Date.now()
            while (true) {
              await new Promise((r) => setTimeout(r, POLL_MS))
              if (Date.now() - tStart > MAX_WAIT_MS) {
                throw new Error(`轮询超时 ${MAX_WAIT_MS / 1000}s`)
              }
              const stRes = await fetch(`/api/runner/test_phase/${tid}`)
              const st = (await stRes.json()) as {
                status?: string; result?: typeof data; elapsed_ms?: number
              }
              setPhaseTester({
                progress: `${phase} 跑中... #${tgt} ${Math.round((st.elapsed_ms || 0) / 1000)}s`,
              })
              if (st.status === 'done') {
                data = st.result || {}
                break
              }
            }

            const ok = data.ok !== false
            const result: ResultRow = {
              phase,
              phase_name: `#${tgt} ${data.phase_name || meta.name}`,
              ok,
              duration_ms: data.duration_ms ?? Date.now() - t0,
              error: ok ? undefined : data.error,
            }
            addPhaseResult(result)
            ok ? okCount++ : failCount++
            if (!ok && !keepGoing) break
          } catch (e) {
            addPhaseResult({
              phase,
              phase_name: `#${tgt} ${meta.name}`,
              ok: false,
              duration_ms: Date.now() - t0,
              error: String(e),
            })
            failCount++
            if (!keepGoing) break
          }
        }
        if (failCount > 0 && !keepGoing) break
      }
    } finally {
      setPhaseTester({
        busy: false,
        progress: `完成: ${okCount} 通过 / ${failCount} 失败`,
      })
    }
  }

  const cancel = async () => {
    try {
      await fetch('/api/runner/cancel', { method: 'POST' })
    } catch {
      /* ignore */
    }
    setPhaseTester({ busy: false, progress: '' })
  }

  const exportJson = () => {
    const blob = new Blob([JSON.stringify(results, null, 2)], {
      type: 'application/json',
    })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `phase-test-${Date.now()}.json`
    a.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  return (
    <section
      className="overflow-hidden rounded-[14px] bg-card border border-border"
      style={{ boxShadow: '0 1px 0 rgba(0,0,0,.02)' }}
    >
      {/* header */}
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className={cn(
          'w-full flex items-center gap-2.5 px-4 py-3 cursor-pointer text-left border-0 bg-transparent',
          open && 'border-b border-border-soft',
        )}
      >
        <svg
          width="10"
          height="10"
          viewBox="0 0 10 10"
          className="text-subtle"
          style={{
            transform: open ? 'rotate(90deg)' : 'rotate(0deg)',
            transition: 'transform .15s',
          }}
          aria-hidden
        >
          <path
            d="M3 1l4 4-4 4"
            stroke="currentColor"
            strokeWidth="1.4"
            strokeLinecap="round"
            strokeLinejoin="round"
            fill="none"
          />
        </svg>
        <span className="text-[13px] font-semibold text-foreground">
          阶段测试
        </span>
        <span
          className="text-[10px] font-bold px-1.5 py-0.5 rounded-[4px]"
          style={{
            background: '#fef3c7',
            color: '#92400e',
            letterSpacing: '.06em',
          }}
        >
          DEV
        </span>
        <span className="text-[11.5px] text-subtle">
          单独跑某阶段验证逻辑, 不进游戏整局
        </span>
        <span className="flex-1" />
        <span className="gb-mono text-[11px] text-subtle">
          已选 {selKeys.length} 阶段 · {targets.length} 实例
        </span>
      </button>

      {open && (
        <div className="p-5 flex flex-col gap-[18px]">
          {/* Section 1 — phase chips */}
          <div>
            <ColHeader n="1" title="选阶段" hint="点击勾选 · 按勾选顺序串行跑" />
            <div className="flex flex-wrap gap-2">
              {RUNNER_PHASES.map((p) => {
                const idx = selKeys.indexOf(p.key)
                const sel = idx >= 0
                return (
                  <button
                    key={p.key}
                    type="button"
                    onClick={() => togglePhase(p.key)}
                    className={cn(
                      'flex items-center gap-2 px-3 py-2 rounded-[9px] border cursor-pointer text-left',
                      'min-w-[175px] transition-colors',
                      sel
                        ? 'border-foreground bg-[#f4f1ea]'
                        : 'border-border bg-card hover:border-border-soft',
                    )}
                  >
                    <span
                      className={cn(
                        'gb-mono inline-flex items-center justify-center w-6 h-6 rounded-md text-[11px] font-bold flex-none',
                        sel
                          ? 'bg-foreground text-card'
                          : 'bg-secondary text-subtle',
                      )}
                    >
                      {sel ? idx + 1 : p.key.replace(/^P/, '')}
                    </span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-baseline gap-1.5">
                        <span className="gb-mono text-[10.5px] font-semibold text-subtle">
                          {p.key}
                        </span>
                        <span className="text-[12.5px] font-semibold text-foreground">
                          {p.name}
                        </span>
                        {p.role && (
                          <span
                            className="text-[9px] font-bold px-1 rounded-[3px]"
                            style={{
                              background: '#eef2ff',
                              color: '#4338ca',
                              letterSpacing: '.04em',
                            }}
                          >
                            ROLE
                          </span>
                        )}
                        {p.needsId && (
                          <span
                            className="text-[9px] font-bold px-1 rounded-[3px]"
                            style={{
                              background: '#fef3c7',
                              color: '#92400e',
                              letterSpacing: '.04em',
                            }}
                          >
                            ID
                          </span>
                        )}
                      </div>
                      <div className="text-[10.5px] text-subtle mt-0.5">
                        {p.desc}
                      </div>
                    </div>
                  </button>
                )
              })}
            </div>
          </div>

          {/* Section 2 — workbench */}
          <div>
            <ColHeader
              n="2"
              title="工作台"
              hint={`目标 ${targets.length} 实例 · ${selKeys.length} 阶段串行`}
            />
            <div
              className="relative px-4 py-4 rounded-[12px] border border-border-soft"
              style={{
                background: '#fbf9f3',
                backgroundImage:
                  'radial-gradient(rgba(0,0,0,.045) 1px, transparent 1px)',
                backgroundSize: '14px 14px',
                backgroundPosition: '-1px -1px',
                boxShadow: 'inset 0 0 0 1px rgba(255,255,255,.6)',
              }}
            >
              {/* TARGETS 行 */}
              <SubHead tag="TARGETS" count={`${targets.length} 实例`} />
              {targets.length === 0 ? (
                <div
                  className="gb-mono text-[11px] text-fainter text-center py-3"
                  style={{
                    borderTop: '1px dashed var(--color-border-soft)',
                    borderBottom: '1px dashed var(--color-border-soft)',
                    letterSpacing: '.04em',
                  }}
                >
                  ↑ 主页卡上选实例 ── 至少 1 台
                </div>
              ) : (
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {targets.map((idx) => (
                    <TargetChip
                      key={idx}
                      idx={idx}
                      instance={instancesRecord[String(idx)]}
                      account={accounts.find((a) => a.index === idx)}
                      role={effRole(
                        idx,
                        (accounts.find((a) => a.index === idx)?.role ||
                          'captain') as 'captain' | 'member',
                      )}
                      onToggleRole={(inst, fallback) =>
                        toggleChipRole(inst, fallback)
                      }
                      onRemove={() => removeTarget(idx)}
                    />
                  ))}
                </div>
              )}

              {/* divider */}
              <div className="my-3.5 border-t border-dashed border-border-soft -mx-1" />

              {/* CONFIG 行 — ROLE 切换在 chip 内部, 这里没全局 ROLE */}
              <SubHead tag="CONFIG" />
              <div className="flex flex-wrap gap-4 items-end mt-2">
                {needsId && (
                  <Field
                    label="P5 PLAYER ID"
                    sub={idOk ? '10 位数字' : '⚠ 必须 10 位数字'}
                    warn={!idOk}
                  >
                    <input
                      type="text"
                      value={expectedId}
                      onChange={(e) =>
                        setPhaseTester({
                          expectedId: e.target.value
                            .replace(/\D/g, '')
                            .slice(0, 10),
                        })
                      }
                      placeholder="1234567890"
                      className={cn(
                        'gb-mono w-[150px] px-2.5 py-2 rounded-md outline-none',
                        'text-[12.5px] tracking-[.06em] border',
                        idOk
                          ? 'bg-card border-border'
                          : 'bg-[#fff5f5] border-[#fca5a5]',
                      )}
                    />
                  </Field>
                )}

                <Field label="ON FAIL">
                  <KeepGoingToggle
                    value={keepGoing}
                    onChange={(v) => setPhaseTester({ keepGoing: v })}
                  />
                </Field>

                <span className="flex-1 min-w-3" />

                {/* RUN */}
                {!busy ? (
                  <button
                    type="button"
                    disabled={!canRun}
                    onClick={runTest}
                    title={
                      isRunning
                        ? '主跑运行中, 此面板锁定'
                        : !canRun
                          ? '勾选阶段 + 实例 + 校验通过才能跑'
                          : ''
                    }
                    className={cn(
                      'inline-flex items-center gap-2.5 px-5 py-2.5 rounded-[9px] border-0',
                      'text-[13.5px] font-bold cursor-pointer',
                      canRun
                        ? 'text-card'
                        : 'text-fainter cursor-not-allowed',
                    )}
                    style={{
                      background: canRun
                        ? 'linear-gradient(180deg, #d97757 0%, #c66845 100%)'
                        : '#e8e3d6',
                      letterSpacing: '.04em',
                      boxShadow: canRun
                        ? '0 1px 0 rgba(255,255,255,.3) inset, 0 4px 12px rgba(217,119,87,.28)'
                        : 'none',
                    }}
                  >
                    <svg
                      width="10"
                      height="10"
                      viewBox="0 0 10 10"
                      fill="currentColor"
                    >
                      <path d="M2 1l7 4-7 4z" />
                    </svg>
                    <span>RUN</span>
                    <span
                      className="gb-mono text-[11.5px] font-semibold pl-1.5 ml-1"
                      style={{
                        opacity: 0.85,
                        borderLeft: '1px solid rgba(255,255,255,.3)',
                        paddingLeft: 6,
                      }}
                    >
                      {targets.length}×{selKeys.length}
                    </span>
                  </button>
                ) : (
                  <div className="flex gap-2">
                    <div
                      className="inline-flex items-center gap-2 px-4 py-2.5 rounded-[9px] border text-[12.5px] font-medium"
                      style={{
                        background: '#fffaeb',
                        color: '#92400e',
                        borderColor: 'var(--color-border)',
                      }}
                    >
                      <span
                        className="gb-pulse w-1.5 h-1.5 rounded-full"
                        style={{ background: '#f59e0b' }}
                      />
                      <span className="gb-mono">{progress || '运行中...'}</span>
                    </div>
                    <button
                      type="button"
                      onClick={cancel}
                      className="px-4 py-2.5 rounded-[9px] border border-border bg-card text-muted-foreground text-[12.5px] font-medium cursor-pointer"
                    >
                      取消
                    </button>
                  </div>
                )}
              </div>
            </div>

            <div className="text-[10.5px] text-fainter leading-snug pt-2 px-0.5 flex gap-1.5">
              <span className="flex-none">ℹ</span>
              <span>
                主跑运行时此面板锁定 · 大组之间并发, P3a→P3b→P4→P5 串行
              </span>
            </div>
          </div>

          {/* Section 3 — results */}
          <div className="flex flex-col min-h-0">
            <ColHeader
              n="3"
              title="结果"
              hint={
                results.length === 0
                  ? '还没跑过'
                  : `${results.length} 条 · ${results.filter((r) => r.ok).length} 通过 · ${results.filter((r) => !r.ok).length} 失败`
              }
              actions={
                results.length > 0 ? (
                  <div className="flex gap-1">
                    <SmallBtn label="导出 JSON" onClick={exportJson} />
                    <SmallBtn label="清空" onClick={clearPhaseResults} />
                  </div>
                ) : undefined
              }
            />
            <div
              className="flex-1 overflow-y-auto rounded-lg border border-border-soft p-1.5"
              style={{ background: '#fafaf7', maxHeight: 280 }}
            >
              {results.length === 0 ? (
                <div className="p-4 text-center text-fainter text-[11.5px]">
                  按上面配置 → 点 RUN, 这里出结果
                </div>
              ) : (
                <div className="flex flex-col gap-1">
                  {results.map((r, i) => (
                    <ResultRowItem key={i} r={r} />
                  ))}
                </div>
              )}
            </div>
            <div className="text-[10.5px] text-fainter pt-2 flex gap-3">
              <span>
                <Kbd>R</Kbd> 重跑 (TODO)
              </span>
              <span>
                <Kbd>E</Kbd> 导出
              </span>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}

// ─── helpers ───────────────────────────────────────────────────────────

function ColHeader({
  n,
  title,
  hint,
  actions,
}: {
  n: string
  title: string
  hint?: string
  actions?: React.ReactNode
}) {
  return (
    <div className="flex items-baseline gap-2 mb-2">
      <span className="gb-mono inline-flex items-center justify-center w-[18px] h-[18px] rounded-[4px] bg-secondary text-subtle text-[11px] font-bold">
        {n}
      </span>
      <span className="text-[12.5px] font-semibold text-foreground">
        {title}
      </span>
      {hint && <span className="text-[11px] text-subtle">{hint}</span>}
      <span className="flex-1" />
      {actions}
    </div>
  )
}

function SubHead({ tag, count }: { tag: string; count?: string }) {
  return (
    <div
      className="gb-mono flex items-baseline gap-1.5 text-[10.5px] font-semibold text-subtle"
      style={{ letterSpacing: '.05em' }}
    >
      <span style={{ color: '#c4a850' }}>//</span>
      <span>{tag}</span>
      {count && (
        <span className="text-fainter font-medium">· {count}</span>
      )}
    </div>
  )
}

function Field({
  label,
  sub,
  warn,
  children,
}: {
  label: string
  sub?: string
  warn?: boolean
  children: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-1.5 min-w-0">
      <span
        className="gb-mono text-[10px] font-semibold text-subtle"
        style={{ letterSpacing: '.07em' }}
      >
        {label}
        {sub && (
          <span
            className="ml-1.5 font-medium"
            style={{
              letterSpacing: '.02em',
              color: warn ? '#b45309' : 'var(--color-fainter)',
            }}
          >
            · {sub}
          </span>
        )}
      </span>
      <div>{children}</div>
    </div>
  )
}

function SegBtns<T extends string>({
  value,
  onChange,
  options,
}: {
  value: T
  onChange: (v: T) => void
  options: Array<{ value: T; label: string }>
}) {
  return (
    <div className="inline-flex bg-secondary rounded-[7px] p-0.5 gap-px">
      {options.map((o) => {
        const sel = value === o.value
        return (
          <button
            key={o.value}
            type="button"
            onClick={() => onChange(o.value)}
            className={cn(
              'px-3 py-1.5 rounded-[5px] text-[12px] cursor-pointer border-0',
              sel
                ? 'bg-card text-foreground font-semibold shadow-[0_1px_2px_rgba(0,0,0,0.06)]'
                : 'bg-transparent text-subtle font-medium',
            )}
          >
            {o.label}
          </button>
        )
      })}
    </div>
  )
}

function KeepGoingToggle({
  value,
  onChange,
}: {
  value: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <label className="inline-flex items-center gap-2 text-[12px] text-muted-foreground cursor-pointer h-8">
      <span
        className="w-[30px] h-[17px] rounded-[9px] relative transition-colors"
        style={{
          background: value ? 'var(--color-foreground)' : '#e7e3d8',
        }}
      >
        <span
          className="absolute top-0.5 w-[13px] h-[13px] rounded-full bg-card"
          style={{
            left: value ? 15 : 2,
            transition: 'left .12s',
            boxShadow: '0 1px 2px rgba(0,0,0,.15)',
          }}
        />
      </span>
      <input
        type="checkbox"
        checked={value}
        onChange={(e) => onChange(e.target.checked)}
        className="sr-only"
      />
      <span
        className={cn(
          'gb-mono text-[11.5px] font-medium',
          value ? 'text-foreground' : 'text-subtle',
        )}
      >
        {value ? 'CONTINUE' : 'STOP'}
      </span>
    </label>
  )
}

function ResultRowItem({ r }: { r: ResultRow }) {
  return (
    <div
      className="bg-card border border-border-soft rounded-md px-2.5 py-1.5 flex items-center gap-2.5"
    >
      <span
        className="inline-flex items-center justify-center w-[18px] h-[18px] rounded text-[11px] font-bold flex-none"
        style={{
          background: r.ok ? '#dcfce7' : '#fee2e2',
          color: r.ok ? '#166534' : '#991b1b',
        }}
      >
        {r.ok ? '✓' : '✗'}
      </span>
      <span className="gb-mono text-[11px] font-semibold text-subtle min-w-[30px]">
        {r.phase}
      </span>
      <span className="text-[12px] text-foreground flex-1 truncate">
        {r.phase_name}
        {!r.ok && r.error && (
          <span className="text-[#991b1b] ml-2 text-[11px]">· {r.error}</span>
        )}
      </span>
      {r.duration_ms != null && (
        <span className="gb-mono text-[11px] text-subtle flex-none">
          {r.duration_ms < 1000
            ? `${r.duration_ms}ms`
            : `${(r.duration_ms / 1000).toFixed(2)}s`}
        </span>
      )}
    </div>
  )
}

function SmallBtn({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="px-2 py-0.5 rounded-[4px] border border-border bg-card text-subtle text-[10.5px] cursor-pointer"
    >
      {label}
    </button>
  )
}

/**
 * TargetChip — TARGETS 行单个实例 chip.
 * 来源: phase-tester.jsx TargetChip (设计原型 ~552 行)
 * 含: ⋮⋮ drag handle + 编号 + team 字母 + ★队长/·队员 toggle + × remove
 */
function TargetChip({
  idx,
  instance,
  account,
  role,
  onToggleRole,
  onRemove,
}: {
  idx: number
  instance?: Instance
  account?: AccountAssignment
  role: 'captain' | 'member'
  onToggleRole: (
    inst: { index: number; group: string },
    fallback: 'captain' | 'member',
  ) => void
  onRemove: () => void
}) {
  const team = instance?.group ?? account?.group ?? 'A'
  const tint = TEAM_TINT[team] || '#888'
  const isCap = role === 'captain'
  const fallback = (account?.role || 'captain') as 'captain' | 'member'

  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        padding: 0,
        borderRadius: 16,
        background: '#fff',
        border: '1px solid var(--color-border)',
        fontSize: 11,
        height: 26,
        overflow: 'hidden',
        position: 'relative',
        userSelect: 'none',
      }}
    >
      {/* drag handle (visual cue, drag-drop 暂不实现) */}
      <span
        aria-hidden
        style={{
          height: 26,
          width: 10,
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: 'var(--color-fainter)',
          fontSize: 8,
          letterSpacing: -1,
          background: `${tint}10`,
          borderRight: `1px solid ${tint}22`,
        }}
      >
        ⋮⋮
      </span>

      {/* number badge */}
      <span
        className="gb-mono"
        style={{
          height: 26,
          minWidth: 24,
          padding: '0 6px',
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: `${tint}14`,
          color: 'var(--color-foreground)',
          fontWeight: 700,
          fontSize: 11,
          borderRight: '1px solid var(--color-border-soft)',
        }}
      >
        {String(idx).padStart(2, '0')}
      </span>

      {/* team letter */}
      <span
        className="gb-mono"
        style={{
          padding: '0 6px',
          fontWeight: 700,
          color: tint,
          fontSize: 10.5,
        }}
      >
        {team}
      </span>

      {/* role toggle (★队长 / ·队员) */}
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation()
          onToggleRole({ index: idx, group: team }, fallback)
        }}
        title={`点击切换为${isCap ? '队员' : '队长'} · 一队仅一队长`}
        style={{
          height: 26,
          padding: '0 8px',
          display: 'inline-flex',
          alignItems: 'center',
          gap: 4,
          background: isCap ? '#fff8eb' : 'transparent',
          color: isCap ? '#b45309' : 'var(--color-subtle)',
          border: 'none',
          borderLeft: '1px solid var(--color-border-soft)',
          fontFamily: 'inherit',
          fontSize: 10.5,
          fontWeight: isCap ? 700 : 500,
          cursor: 'pointer',
        }}
      >
        <span style={{ fontSize: 10 }}>{isCap ? '★' : '·'}</span>
        <span>{isCap ? '队长' : '队员'}</span>
      </button>

      {/* × remove */}
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation()
          onRemove()
        }}
        title="从测试目标移除"
        style={{
          height: 26,
          width: 22,
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: 'transparent',
          color: 'var(--color-fainter)',
          border: 'none',
          borderLeft: '1px solid var(--color-border-soft)',
          cursor: 'pointer',
          fontSize: 13,
          lineHeight: 1,
        }}
      >
        ×
      </button>
    </span>
  )
}

// 抑制 unused import 警告
void useEffect
void SegBtns
