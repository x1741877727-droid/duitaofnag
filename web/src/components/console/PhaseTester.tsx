/**
 * 阶段测试 — 多选阶段 + 选实例 + 串行跑 (单实例).
 *
 * 上层: 阶段勾选 (按勾选顺序串跑) + 文档展开 (description + flow_steps, 来自代码类属性)
 * 下层: 选实例 + 角色 + 一键串跑
 *
 * 实现: 依次 POST /api/runner/test_phase 每个 phase, 上一阶段失败则停止 (除非用户勾"忽略失败继续").
 */
import { useEffect, useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import { useAppStore } from '@/lib/store'
import { SquadDialog, type SquadAssignment } from './SquadDialog'
import type { TeamGroup } from '@/lib/store'

interface PhaseDef {
  key: string
  name: string
  description: string
  flow_steps: string[]
  max_rounds: number
  round_interval_s: number
}

interface RunResult {
  phase: string
  phase_name: string
  ok: boolean
  duration_ms?: number
  error?: string
}

const ROLE_PHASES = new Set(['P3a', 'P3b', 'P4'])


/**
 * 提交 test_phase 并拿结果.
 *
 * 兼容两种 backend 形态:
 *   - 同步: POST 直接返 {ok, phase_name, duration_ms, ...} (当前线上)
 *   - 异步队列: POST 返 {task_id, status:"running"} → 前端轮询 GET (旧设计)
 * 通过响应里有没有 duration_ms 来分流, 不再硬要求 task_id.
 */
async function runPhaseAsync(body: {
  instance: number; phase: string; role: 'captain' | 'member'; scheme?: string; expected_id?: string;
}): Promise<{ ok: boolean; phaseName: string; duration: number; error: string; scheme: string }> {
  const phaseName0 = body.phase
  const t0 = performance.now()
  // 1) 提交
  let data: any = null
  try {
    const r = await fetch('/api/runner/test_phase', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const text = await r.text()
    try { data = text ? JSON.parse(text) : null } catch {}
    if (!r.ok || !data) {
      const errMsg = data?.detail || data?.error || `HTTP ${r.status}: ${text.slice(0, 80)}`
      return { ok: false, phaseName: phaseName0, duration: performance.now() - t0, error: errMsg, scheme: '' }
    }
  } catch (e) {
    return { ok: false, phaseName: phaseName0, duration: performance.now() - t0, error: `提交异常: ${e}`, scheme: '' }
  }

  // 2a) 同步模式: backend 已直接跑完, 响应就是完整结果
  if (data.duration_ms !== undefined) {
    return {
      ok: !!data.ok,
      phaseName: data.phase_name || phaseName0,
      duration: data.duration_ms,
      error: data.error || '',
      scheme: data.game_scheme_url || '',
    }
  }

  // 2b) 异步队列模式 (兼容): 拿 task_id 轮询
  const taskId: string = data.task_id || ''
  if (!taskId) {
    return { ok: false, phaseName: phaseName0, duration: performance.now() - t0, error: `响应无 task_id 也无 duration_ms: ${JSON.stringify(data).slice(0, 120)}`, scheme: '' }
  }
  const POLL_MS = 1000
  const MAX_WAIT_MS = 10 * 60 * 1000
  const pollT0 = performance.now()
  while (performance.now() - pollT0 < MAX_WAIT_MS) {
    await new Promise((res) => setTimeout(res, POLL_MS))
    try {
      const r = await fetch(`/api/runner/test_phase/${encodeURIComponent(taskId)}`)
      const text = await r.text()
      let pd: any = null
      try { pd = text ? JSON.parse(text) : null } catch {}
      if (!pd) continue
      if (pd.status === 'running') continue
      const duration = pd.duration_ms ?? (performance.now() - t0)
      return {
        ok: !!pd.ok,
        phaseName: pd.phase_name || phaseName0,
        duration,
        error: pd.error || '',
        scheme: pd.game_scheme_url || '',
      }
    } catch {
      continue
    }
  }
  return { ok: false, phaseName: phaseName0, duration: performance.now() - t0, error: '轮询超时 10 分钟', scheme: '' }
}

export function PhaseTester() {
  const isRunning = useAppStore((s) => s.isRunning)
  const emulators = useAppStore((s) => s.emulators)
  const instances = useAppStore((s) => s.instances)
  const selectedInstances = useAppStore((s) => s.selectedInstances)
  // 持久状态 (切页面不重置)
  const phaseTester = useAppStore((s) => s.phaseTester)
  const setPhaseTester = useAppStore((s) => s.setPhaseTester)
  const addPhaseResult = useAppStore((s) => s.addPhaseResult)
  const clearPhaseResults = useAppStore((s) => s.clearPhaseResults)
  const addLog = useAppStore((s) => s.addLog)

  const { selKeys, selInst, selRole, expectedId, keepGoing, busy, progress, results } = phaseTester
  const needsExpectedId = selKeys.includes('P5')
  const expectedIdValid = !needsExpectedId || /^\d{10}$/.test(expectedId.trim())

  const availableInstances = useMemo(() => {
    const ids = new Set<number>()
    for (const e of emulators) ids.add(e.index)
    for (const i of Object.values(instances)) ids.add(i.index)
    return Array.from(ids).sort((a, b) => a - b).map((idx) => ({
      index: idx,
      running: emulators.find((e) => e.index === idx)?.running ?? false,
    }))
  }, [instances, emulators])

  const useFromConsole = selectedInstances.length > 0
  const targetsFromConsole = useFromConsole ? selectedInstances : []

  const [phases, setPhases] = useState<PhaseDef[]>([])
  const [expanded, setExpanded] = useState<string | null>(null)
  const [squadDialogOpen, setSquadDialogOpen] = useState(false)
  // 默认每组 3 人 (1 队长 + 2 队员). 用户可改 (PUBG 类 4 人, 三排 3 人, 双排 2 人)
  const [squadSize, setSquadSize] = useState(3)

  useEffect(() => {
    fetch('/api/runner/phases').then((r) => r.json()).then((d) => setPhases(d.phases || [])).catch(() => {})
  }, [])

  // 自动按 squadSize 把选中实例切分大组. 每组第 1 个 (实例号最小) 当队长, 其余队员.
  // 例: 选 #3#4#5 + size=3 → [{leader:3, members:[4,5]}]  (1 组)
  // 例: 选 #1#2#3#4#5#6 + size=3 → [{leader:1, members:[2,3]}, {leader:4, members:[5,6]}]  (2 组)
  // 组名按 A/B/C/... 顺序 (TeamGroup 字面量限制 6 组 = 18 实例, 实战够用)
  const autoSquads = useMemo<SquadAssignment[]>(() => {
    const groupNames: TeamGroup[] = ['A', 'B', 'C', 'D', 'E', 'F']
    const sorted = [...targetsFromConsole].sort((a, b) => a - b)
    const out: SquadAssignment[] = []
    for (let i = 0; i < sorted.length; i += squadSize) {
      const chunk = sorted.slice(i, i + squadSize)
      if (chunk.length === 0) continue
      if (out.length >= groupNames.length) break  // 超过 6 组就停 (异常多实例)
      out.push({
        group: groupNames[out.length],
        leader: chunk[0],
        members: chunk.slice(1),
      })
    }
    return out
  }, [targetsFromConsole, squadSize])

  function togglePhase(key: string) {
    const cur = selKeys
    const next = cur.includes(key) ? cur.filter((k) => k !== key) : [...cur, key]
    setPhaseTester({ selKeys: next })
  }

  // 入口: 决定走独立模式 / 自动大组 / 自定义大组对话框
  function onClickRun() {
    let targets: number[]
    if (useFromConsole) {
      targets = targetsFromConsole
    } else {
      const t = selInst ?? availableInstances.find((e) => e.running)?.index ?? availableInstances[0]?.index
      targets = t === undefined ? [] : [t]
    }
    if (targets.length === 0) return

    // 多实例 + 选了 P3+/P4 → 自动按 squadSize 切组直接跑
    // 用户原话: "三个实例的话就三个为一组 六个的话就是两个组"
    const hasSquadPhase = selKeys.some((k) => ROLE_PHASES.has(k))
    if (targets.length > 1 && hasSquadPhase) {
      runAllSquads(autoSquads)
      return
    }
    // 否则 (单实例 / 只跑 P0-P2) 直接独立模式
    runAllIndependent(targets)
  }

  async function runAllIndependent(targets: number[]) {
    setPhaseTester({ busy: true, progress: '' })
    clearPhaseResults()
    // 强制开新 session, 避免本次跟上次合并 (用户痛点: 两次测试塞在一个 session)
    try { await fetch('/api/runner/test_new_session', { method: 'POST' }) } catch {}

    const total = targets.length * selKeys.length
    addLog({
      timestamp: Date.now() / 1000,
      instance: 'SYS',
      level: 'info',
      message: `[阶段测试] 并发跑 ${targets.length} 实例 × ${selKeys.length} 阶段 (${selKeys.join(' → ')}) #${targets.join(',#')} 角色 ${selRole}`,
    })

    // 进度计数器 (closure shared by parallel tasks)
    let done = 0
    const updateProg = () => setPhaseTester({ progress: `${done}/${total}` })

    // 每实例独立跑自己的阶段链 — 用 runPhaseAsync 后台任务 + 轮询
    // 业务约束: P5 只在队长 (targets[0]) 跑, 其他 instance 跳过 (member 不需要等真人)
    const leader = targets[0]
    async function runOneInstance(tgt: number): Promise<void> {
      for (const phase of selKeys) {
        if (phase === 'P5' && targets.length > 1 && tgt !== leader) {
          addLog({
            timestamp: Date.now() / 1000,
            instance: tgt, level: 'info',
            message: `[阶段测试 #${tgt}] 跳过 P5 (只在队长 #${leader} 跑)`,
          })
          done += 1
          updateProg()
          continue
        }
        addLog({
          timestamp: Date.now() / 1000,
          instance: tgt, level: 'info',
          message: `[阶段测试 #${tgt}] 开始 ${phase}…`,
        })
        const r = await runPhaseAsync({
          instance: tgt, phase, role: selRole,
          expected_id: phase === 'P5' ? expectedId.trim() : undefined,
        })
        done += 1
        updateProg()
        addPhaseResult({
          phase: `#${tgt}/${phase}`,
          phase_name: `#${tgt} ${r.phaseName}`,
          ok: r.ok, duration_ms: r.duration, error: r.error,
        })
        addLog({
          timestamp: Date.now() / 1000,
          instance: tgt,
          level: r.ok ? 'info' : (r.error ? 'error' : 'warn'),
          message: `[阶段测试] #${tgt} ${r.phaseName} ${r.ok ? '✓ 完成' : '✗ 失败'} (${r.duration.toFixed(0)}ms)${r.error ? ' — ' + r.error : ''}`,
        })
        if (!r.ok && !keepGoing) return
      }
    }

    // 并发起所有实例
    await Promise.allSettled(targets.map(runOneInstance))

    addLog({
      timestamp: Date.now() / 1000,
      instance: 'SYS',
      level: 'info',
      message: `[阶段测试] 全部跑完 ${done}/${total}`,
    })
    // 跑完后清空结果区 + 进度 (按用户要求, 不留垃圾)
    setPhaseTester({ busy: false, progress: '' })
    setTimeout(() => clearPhaseResults(), 100)
  }


  // 大组联动模式: 大组之间 Promise.allSettled 并发, 组内 P0-P2 并发, P3a 拿 scheme 同步给队员 P3b, P4 队长收尾
  async function runAllSquads(squads: SquadAssignment[]) {
    setSquadDialogOpen(false)
    setPhaseTester({ busy: true, progress: '' })
    // 强制开新 session, 避免本次跟上次合并
    try { await fetch('/api/runner/test_new_session', { method: 'POST' }) } catch {}
    clearPhaseResults()

    const totalInstances = squads.reduce((n, sq) => n + 1 + sq.members.length, 0)
    const totalSteps = totalInstances * selKeys.length
    let done = 0
    const updateProg = () => setPhaseTester({ progress: `${done}/${totalSteps}` })

    addLog({
      timestamp: Date.now() / 1000, instance: 'SYS', level: 'info',
      message: `[阶段测试·大组联动] ${squads.length} 大组 / ${totalInstances} 实例 / ${selKeys.length} 阶段 (${selKeys.join(' → ')})`,
    })

    async function runPhase(tgt: number, phase: string, role: 'captain' | 'member', scheme?: string): Promise<{ ok: boolean; scheme: string; error: string }> {
      addLog({
        timestamp: Date.now() / 1000, instance: tgt, level: 'info',
        message: `[阶段测试·大组] #${tgt} ${role} 开始 ${phase}…`,
      })
      const r = await runPhaseAsync({
        instance: tgt, phase, role, scheme,
        expected_id: phase === 'P5' ? expectedId.trim() : undefined,
      })
      done += 1
      updateProg()
      addPhaseResult({
        phase: `#${tgt}/${phase}`, phase_name: `#${tgt} ${r.phaseName}`,
        ok: r.ok, duration_ms: r.duration, error: r.error,
      })
      addLog({
        timestamp: Date.now() / 1000, instance: tgt,
        level: r.ok ? 'info' : (r.error ? 'error' : 'warn'),
        message: `[阶段测试·大组] #${tgt} ${r.phaseName} ${r.ok ? '✓ 完成' : '✗ 失败'} (${r.duration.toFixed(0)}ms)${r.error ? ' — ' + r.error : ''}`,
      })
      return { ok: r.ok, scheme: r.scheme, error: r.error }
    }

    async function runOneSquad(sq: SquadAssignment): Promise<void> {
      // 重设计 (2026-04-30): 不再对 P0/P1/P2 用 await Promise.all 等齐.
      // 老逻辑: 队长 P2 完后白等慢队员 → 4-5s 浪费 (实测 inst0 等 inst2 慢 4s).
      // 新逻辑: 每实例独立跑 P0→P1→P2 链 (per-instance pipeline);
      //   队长 P2 完立刻进 P3a 出 scheme (不等队员);
      //   队员 P2 完 + 队长 scheme 就绪 → 立即跑 P3b.
      const all = [sq.leader, ...sq.members]
      const preSquadPhases = selKeys.filter((k) => !ROLE_PHASES.has(k))   // P0/P1/P2

      // 每实例独立 P0→P1→P2 串链 (返回 promise: ok=false 时算失败)
      const p2DonePromises: Record<number, Promise<boolean>> = {}
      for (const idx of all) {
        const role: 'captain' | 'member' = idx === sq.leader ? 'captain' : 'member'
        p2DonePromises[idx] = (async () => {
          for (const phase of preSquadPhases) {
            const r = await runPhase(idx, phase, role)
            if (!r.ok && !keepGoing) return false
          }
          return true
        })()
      }

      // 队长 P3a: 自己 P0-P2 完立刻跑, 不等队员
      let schemePromise: Promise<{ ok: boolean; scheme: string; error: string }> | null = null
      if (selKeys.includes('P3a')) {
        schemePromise = (async () => {
          const ok = await p2DonePromises[sq.leader]
          if (!ok && !keepGoing) return { ok: false, scheme: '', error: 'leader P0-P2 失败' }
          return runPhase(sq.leader, 'P3a', 'captain')
        })()
      }

      // 队员 P3b: 自己 P0-P2 完 AND 队长 scheme 就绪 → 立即跑 (互不等)
      const p3bPromises: Promise<unknown>[] = []
      if (selKeys.includes('P3b') && sq.members.length > 0 && schemePromise) {
        for (const memIdx of sq.members) {
          p3bPromises.push((async () => {
            const memOk = await p2DonePromises[memIdx]
            if (!memOk && !keepGoing) return
            const lr = await schemePromise!
            if (!lr.ok || !lr.scheme) {
              addLog({
                timestamp: Date.now() / 1000, instance: memIdx, level: 'warn',
                message: `[阶段测试·大组] #${memIdx} 等不到 scheme (大组 ${sq.group} 队长 P3a ${lr.error || '无 scheme'}), P3b 跳过`,
              })
              return
            }
            await runPhase(memIdx, 'P3b', 'member', lr.scheme)
          })())
        }
      }

      // 等队员 P3b 全部完, 再跑队长 P4 (P4 设计上要等队员就绪)
      await Promise.allSettled([
        ...(schemePromise ? [schemePromise] : []),
        ...p3bPromises,
      ])

      // P4: 队长收尾
      if (selKeys.includes('P4')) {
        await runPhase(sq.leader, 'P4', 'captain')
      }

      // P5: 等真人入队, 只跑队长 (业务约束: 队员只是凑人数, 不需要等)
      if (selKeys.includes('P5')) {
        await runPhase(sq.leader, 'P5', 'captain')
      }
    }

    await Promise.allSettled(squads.map(runOneSquad))

    addLog({
      timestamp: Date.now() / 1000, instance: 'SYS', level: 'info',
      message: `[阶段测试·大组联动] 全部跑完 ${done}/${totalSteps}`,
    })
    setPhaseTester({ busy: false, progress: '' })
    setTimeout(() => clearPhaseResults(), 100)
  }

  const allKeys = phases.map((p) => p.key)

  return (
    <div className="rounded-lg border border-border bg-card p-3 space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="font-semibold text-sm">阶段测试</span>
        <span className="text-[11px] text-muted-foreground">(dryrun · 不跑整套 · 多选按勾选顺序串跑)</span>
        {isRunning && (
          <span className="text-[11px] text-warning bg-warning-muted border border-warning/30 px-2 py-0.5 rounded">
            主 runner 在跑 — 先停才能阶段测试
          </span>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-semibold">阶段 (点选, 再点取消):</span>
        {phases.map((p) => {
          const idx = selKeys.indexOf(p.key)
          const sel = idx >= 0
          const isExp = expanded === p.key
          return (
            <div key={p.key} className="inline-flex items-center gap-0.5">
              <button
                onClick={() => togglePhase(p.key)}
                className={`px-2 py-1 rounded-l border text-xs flex items-center gap-1.5 transition ${
                  sel
                    ? 'bg-info/15 border-info text-info font-semibold'
                    : 'border-border hover:bg-muted'
                }`}
              >
                {sel && <span className="font-mono text-[10px] bg-info text-white rounded-full w-4 h-4 inline-flex items-center justify-center">{idx + 1}</span>}
                <span className="font-mono">{p.key}</span>
                <span className="text-[10px] opacity-70">{p.name}</span>
              </button>
              <button
                onClick={() => setExpanded(isExp ? null : p.key)}
                className="px-1 py-1 rounded-r border-y border-r border-border text-xs hover:bg-muted"
                title="看流程文档"
              >
                {isExp ? '▴' : '▾'}
              </button>
            </div>
          )
        })}
        <Button variant="ghost" size="sm" className="text-[11px]" onClick={() => setPhaseTester({ selKeys: selKeys.length === allKeys.length ? [] : allKeys })}>
          {selKeys.length === allKeys.length ? '清空' : '全选'}
        </Button>
      </div>

      {/* 流程文档展开 */}
      {expanded && (
        <PhaseDoc def={phases.find((p) => p.key === expanded)!} />
      )}

      {useFromConsole ? (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="font-semibold">实例:</span>
          <span className="text-info">
            从中控台多选 ({targetsFromConsole.length} 个):
          </span>
          {targetsFromConsole.map((idx) => (
            <span
              key={idx}
              className="px-2 py-0.5 rounded border border-info/30 bg-info/10 text-info font-mono text-[11px]"
            >
              #{idx}
            </span>
          ))}
          <span className="text-[11px] text-muted-foreground ml-1">
            (改选请到上面实例总览)
          </span>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="font-semibold">实例:</span>
          <span className="text-[11px] text-muted-foreground">
            (没多选 → 这里挑一个; 或上面实例总览多选可一键串跑)
          </span>
          {availableInstances.map((e) => (
            <Button
              key={e.index}
              variant={selInst === e.index ? 'secondary' : 'outline'}
              size="sm"
              onClick={() => setPhaseTester({ selInst: e.index })}
              className={!e.running ? 'opacity-60' : ''}
            >
              #{e.index}{e.running ? '' : '·停'}
            </Button>
          ))}
          {availableInstances.length === 0 && (
            <span className="text-[11px] text-muted-foreground">没检测到模拟器</span>
          )}
        </div>
      )}

      {/* 多实例 + role phase: 显示自动分组预览 (按 squadSize 切, 第 1 个当队长) */}
      {selKeys.some((k) => ROLE_PHASES.has(k)) && useFromConsole && targetsFromConsole.length > 1 && (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="font-semibold">分组:</span>
          <label className="flex items-center gap-1">
            <span className="text-[11px]">每组人数</span>
            <input
              type="number"
              min={2}
              max={6}
              value={squadSize}
              onChange={(e) => setSquadSize(Math.max(2, Math.min(6, parseInt(e.target.value) || 3)))}
              className="w-12 px-1 py-0.5 border border-border rounded text-center font-mono"
            />
          </label>
          <span className="text-muted-foreground">→</span>
          {autoSquads.map((sq) => (
            <span
              key={sq.group}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded border border-info/30 bg-info/10 text-[11px]"
            >
              <span className="font-bold text-info">{sq.group}组</span>
              <span className="font-mono">队长 #{sq.leader}</span>
              {sq.members.length > 0 && (
                <span className="font-mono text-muted-foreground">
                  + 队员 #{sq.members.join(' #')}
                </span>
              )}
            </span>
          ))}
          <button
            onClick={() => setSquadDialogOpen(true)}
            className="text-[11px] text-muted-foreground hover:text-foreground underline ml-1"
            title="自定义分组 (从 dashboard 配置 group/role 读)"
          >
            自定义…
          </button>
        </div>
      )}
      {/* 单实例 + role phase: 还是要让用户选角色 */}
      {selKeys.some((k) => ROLE_PHASES.has(k)) && (!useFromConsole || targetsFromConsole.length <= 1) && (
        <div className="flex items-center gap-2 text-xs">
          <span>角色:</span>
          {(['captain', 'member'] as const).map((r) => (
            <Button
              key={r}
              variant={selRole === r ? 'secondary' : 'outline'}
              size="sm"
              onClick={() => setPhaseTester({ selRole: r })}
            >
              {r === 'captain' ? '队长 (leader)' : '队员 (follower)'}
            </Button>
          ))}
        </div>
      )}

      {/* P5 必填: expected_id (10 位玩家编号) */}
      {needsExpectedId && (
        <div className="flex items-center gap-2 text-xs flex-wrap">
          <span className="font-semibold">P5 等待玩家 ID:</span>
          <input
            type="text"
            inputMode="numeric"
            pattern="\d{10}"
            maxLength={10}
            value={expectedId}
            onChange={(e) => setPhaseTester({ expectedId: e.target.value.replace(/\D/g, '').slice(0, 10) })}
            placeholder="10 位数字"
            className={`px-2 py-1 border rounded font-mono text-xs w-32 ${
              expectedIdValid
                ? 'border-border'
                : 'border-destructive bg-destructive/10 text-destructive'
            }`}
          />
          {useFromConsole && targetsFromConsole.length > 1 ? (
            <span className="text-[11px] text-info">
              (P5 只在队长 #{targetsFromConsole[0]} 跑, 其他 {targetsFromConsole.length - 1} 个实例自动跳过)
            </span>
          ) : (
            <span className="text-[11px] text-muted-foreground">
              (P5 等这个 ID 的真人加入队伍, 240s 超时)
            </span>
          )}
          {!expectedIdValid && expectedId.length > 0 && (
            <span className="text-[11px] text-destructive">必须 10 位数字</span>
          )}
        </div>
      )}

      <div className="flex items-center gap-2 flex-wrap">
        {busy ? (
          <Button
            size="sm"
            variant="destructive"
            onClick={async () => {
              try { await fetch('/api/runner/cancel', { method: 'POST' }) } catch {}
            }}
            className="min-w-[180px]"
          >
            停止测试 ({progress})
          </Button>
        ) : (
          <Button
            size="sm"
            disabled={isRunning || selKeys.length === 0 || !expectedIdValid}
            onClick={onClickRun}
            className="min-w-[180px]"
            title={!expectedIdValid ? 'P5 需要 10 位玩家 ID' : undefined}
          >
            {(() => {
              const hasSquadPhase = selKeys.some((k) => ROLE_PHASES.has(k))
              const multi = useFromConsole && targetsFromConsole.length > 1
              if (multi && hasSquadPhase && autoSquads.length > 0) {
                // 大组联动模式
                return `跑 ${autoSquads.length} 组 × ${selKeys.length} 阶段 (${targetsFromConsole.length} 实例)`
              }
              if (multi) {
                return `串跑 ${selKeys.length} 阶段 × ${targetsFromConsole.length} 实例`
              }
              return `串跑 ${selKeys.length} 个阶段`
            })()}
          </Button>
        )}
        <label className="flex items-center gap-1 text-xs">
          <input
            type="checkbox"
            checked={keepGoing}
            onChange={(e) => setPhaseTester({ keepGoing: e.target.checked })}
          />
          失败也继续 (默认遇错停)
        </label>
        <span className="text-[11px] text-muted-foreground ml-auto">
          跑完去「决策档案」看每帧详情
        </span>
      </div>

      {results.length > 0 && (
        <div className="rounded border border-border bg-muted p-2 space-y-1">
          {results.map((r, i) => (
            <div key={i} className={`flex items-center gap-2 text-[11px] ${r.ok ? 'text-success' : 'text-destructive'}`}>
              <span>{r.ok ? '✓' : '✗'}</span>
              <span className="font-mono">{r.phase}</span>
              <span>{r.phase_name}</span>
              {r.duration_ms !== undefined && (
                <span className="font-mono text-muted-foreground">{r.duration_ms.toFixed(0)}ms</span>
              )}
              {r.error && <span className="text-destructive">— {r.error}</span>}
            </div>
          ))}
        </div>
      )}

      {/* 大组联动弹窗 (选了 P3+/P4 + 多实例触发) */}
      <SquadDialog
        open={squadDialogOpen}
        selectedInstances={targetsFromConsole.length > 0 ? targetsFromConsole : (selInst !== null ? [selInst] : [])}
        selKeys={selKeys}
        onCancel={() => setSquadDialogOpen(false)}
        onConfirm={runAllSquads}
      />
    </div>
  )
}


function PhaseDoc({ def }: { def: PhaseDef }) {
  return (
    <div className="rounded border border-info/25 bg-info/10 p-3 text-xs space-y-2">
      <div className="flex items-center gap-2">
        <span className="font-mono font-bold">{def.key}</span>
        <span className="font-semibold text-info">{def.name}</span>
        <span className="ml-auto text-[10px] text-muted-foreground">
          max_rounds={def.max_rounds} · 间隔 {def.round_interval_s}s
        </span>
      </div>
      {def.description && (
        <div className="text-muted-foreground">{def.description}</div>
      )}
      {def.flow_steps && def.flow_steps.length > 0 && (
        <div>
          <div className="text-[11px] font-semibold mb-1 text-muted-foreground">执行流程:</div>
          <ol className="space-y-0.5 text-[11px] text-muted-foreground">
            {def.flow_steps.map((s, i) => (
              <li key={i} className="flex gap-2">
                <span className="text-info font-mono w-4 shrink-0">{i + 1}.</span>
                <span>{s}</span>
              </li>
            ))}
          </ol>
        </div>
      )}
      <div className="text-[10px] text-muted-foreground">
        ⓘ 文字来自 PhaseHandler.description / flow_steps (类属性). 改代码 → 这里立即同步.
      </div>
    </div>
  )
}
