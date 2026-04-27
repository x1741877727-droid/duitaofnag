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

export function PhaseTester() {
  const isRunning = useAppStore((s) => s.isRunning)
  const emulators = useAppStore((s) => s.emulators)
  const instances = useAppStore((s) => s.instances)
  const availableInstances = useMemo(() => {
    const ids = new Set<number>()
    for (const e of emulators) ids.add(e.index)
    for (const i of Object.values(instances)) ids.add(i.index)
    return Array.from(ids).sort((a, b) => a - b).map((idx) => ({
      index: idx,
      running: emulators.find((e) => e.index === idx)?.running ?? false,
    }))
  }, [instances, emulators])

  const [phases, setPhases] = useState<PhaseDef[]>([])
  const [selKeys, setSelKeys] = useState<string[]>([])     // 多选 (按勾选顺序)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [selInst, setSelInst] = useState<number | null>(null)
  const [selRole, setSelRole] = useState<'captain' | 'member'>('captain')
  const [keepGoing, setKeepGoing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState<string>('')
  const [results, setResults] = useState<RunResult[]>([])

  useEffect(() => {
    fetch('/api/runner/phases').then((r) => r.json()).then((d) => setPhases(d.phases || [])).catch(() => {})
  }, [])

  function togglePhase(key: string) {
    setSelKeys((cur) => cur.includes(key) ? cur.filter((k) => k !== key) : [...cur, key])
  }

  async function runAll() {
    setBusy(true)
    setResults([])
    setProgress('')
    const tgt = selInst ?? availableInstances.find((e) => e.running)?.index ?? availableInstances[0]?.index
    if (tgt === undefined) {
      setResults([{ phase: '-', phase_name: '-', ok: false, error: '没选实例' }])
      setBusy(false)
      return
    }
    const out: RunResult[] = []
    for (let i = 0; i < selKeys.length; i++) {
      const phase = selKeys[i]
      setProgress(`跑 ${i + 1}/${selKeys.length} · ${phase}`)
      try {
        const r = await fetch('/api/runner/test_phase', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ instance: tgt, phase, role: selRole }),
        })
        const data = await r.json()
        if (!r.ok) {
          out.push({ phase, phase_name: phase, ok: false, error: data.detail || data.error || `HTTP ${r.status}` })
          setResults([...out])
          if (!keepGoing) break
          continue
        }
        out.push({
          phase, phase_name: data.phase_name || phase,
          ok: data.ok, duration_ms: data.duration_ms, error: data.error,
        })
        setResults([...out])
        if (!data.ok && !keepGoing) break
      } catch (e) {
        out.push({ phase, phase_name: phase, ok: false, error: String(e) })
        setResults([...out])
        if (!keepGoing) break
      }
    }
    setProgress('')
    setBusy(false)
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
        <Button variant="ghost" size="sm" className="text-[11px]" onClick={() => setSelKeys(selKeys.length === allKeys.length ? [] : allKeys)}>
          {selKeys.length === allKeys.length ? '清空' : '全选'}
        </Button>
      </div>

      {/* 流程文档展开 */}
      {expanded && (
        <PhaseDoc def={phases.find((p) => p.key === expanded)!} />
      )}

      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-semibold">实例:</span>
        {availableInstances.map((e) => (
          <Button
            key={e.index}
            variant={selInst === e.index ? 'secondary' : 'outline'}
            size="sm"
            onClick={() => setSelInst(e.index)}
            className={!e.running ? 'opacity-60' : ''}
          >
            #{e.index}{e.running ? '' : '·停'}
          </Button>
        ))}
        {availableInstances.length === 0 && (
          <span className="text-[11px] text-muted-foreground">没检测到模拟器</span>
        )}
      </div>

      {selKeys.some((k) => ROLE_PHASES.has(k)) && (
        <div className="flex items-center gap-2 text-xs">
          <span>角色:</span>
          {(['captain', 'member'] as const).map((r) => (
            <Button
              key={r}
              variant={selRole === r ? 'secondary' : 'outline'}
              size="sm"
              onClick={() => setSelRole(r)}
            >
              {r === 'captain' ? '队长 (leader)' : '队员 (follower)'}
            </Button>
          ))}
        </div>
      )}

      <div className="flex items-center gap-2 flex-wrap">
        <Button
          size="sm"
          disabled={busy || isRunning || selKeys.length === 0}
          onClick={runAll}
          className="min-w-[180px]"
        >
          {busy ? `跑中… ${progress}` : `串跑 ${selKeys.length} 个阶段`}
        </Button>
        <label className="flex items-center gap-1 text-xs">
          <input
            type="checkbox"
            checked={keepGoing}
            onChange={(e) => setKeepGoing(e.target.checked)}
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
