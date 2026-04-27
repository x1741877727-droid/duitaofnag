/**
 * 单阶段测试面板 — 选实例 + 选阶段 + 跑一次.
 *
 * 不进入完整流程, 跑完看决策档案 (Archive) 即可.
 * 要求主 runner 没在跑.
 */
import { useEffect, useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import { useAppStore } from '@/lib/store'

interface PhaseDef {
  key: string
  name: string
  handler: string
}

const ROLES = [
  { value: 'captain', label: '队长 (leader)' },
  { value: 'member', label: '队员 (follower)' },
] as const

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
  const [selPhase, setSelPhase] = useState<string>('P0')
  const [selInst, setSelInst] = useState<number | null>(null)
  const [selRole, setSelRole] = useState<'captain' | 'member'>('captain')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<any>(null)

  useEffect(() => {
    fetch('/api/runner/phases')
      .then((r) => r.json())
      .then((d) => setPhases(d.phases || []))
      .catch(() => {})
  }, [])

  async function run() {
    setBusy(true)
    setResult(null)
    try {
      const tgt = selInst ?? availableInstances.find((e) => e.running)?.index ?? availableInstances[0]?.index
      if (tgt === undefined) {
        setResult({ ok: false, error: '没选实例' })
        return
      }
      const r = await fetch('/api/runner/test_phase', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ instance: tgt, phase: selPhase, role: selRole }),
      })
      const data = await r.json()
      if (!r.ok) {
        setResult({ ok: false, error: data.detail || data.error || `HTTP ${r.status}` })
      } else {
        setResult(data)
      }
    } catch (e) {
      setResult({ ok: false, error: String(e) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="rounded-lg border border-border bg-card p-3 space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="font-semibold text-sm">阶段测试 (dryrun, 不跑整套)</span>
        {isRunning && (
          <span className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 px-2 py-0.5 rounded">
            主 runner 在跑 — 先停才能阶段测试
          </span>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span>选阶段:</span>
        {phases.map((p) => (
          <Button
            key={p.key}
            variant={selPhase === p.key ? 'secondary' : 'outline'}
            size="sm"
            onClick={() => setSelPhase(p.key)}
            title={p.name}
          >
            <span className="font-mono">{p.key}</span>
            <span className="ml-1 text-[10px] opacity-70">{p.name}</span>
          </Button>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span>选实例:</span>
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

      {(selPhase === 'P3a' || selPhase === 'P3b' || selPhase === 'P4') && (
        <div className="flex items-center gap-2 text-xs">
          <span>角色:</span>
          {ROLES.map((r) => (
            <Button
              key={r.value}
              variant={selRole === r.value ? 'secondary' : 'outline'}
              size="sm"
              onClick={() => setSelRole(r.value)}
            >
              {r.label}
            </Button>
          ))}
        </div>
      )}

      <div className="flex items-center gap-2">
        <Button
          size="sm"
          disabled={busy || isRunning}
          onClick={run}
          className="min-w-[160px]"
        >
          {busy ? '跑中…' : `跑一次 ${selPhase}`}
        </Button>
        <span className="text-[11px] text-muted-foreground">
          跑完去「决策档案」看详情 (input.jpg / 标注图 / 5 层 Tier)
        </span>
      </div>

      {result && (
        <div className={`rounded p-2 text-[11px] ${
          result.ok ? 'bg-emerald-50 text-emerald-800' : 'bg-red-50 text-red-800'
        }`}>
          {result.ok ? (
            <div className="space-y-0.5">
              <div className="font-semibold">✓ {result.phase} ({result.phase_name}) 在 #{result.instance} 完成</div>
              <div>耗时 <span className="font-mono">{result.duration_ms?.toFixed?.(0) || result.duration_ms}ms</span> · 角色 {result.role} · {result.fresh_runner ? '临时 runner' : '复用 runner'}</div>
              {result.decision_session && (
                <div>决策会话: <span className="font-mono">{result.decision_session}</span> (去「决策档案」看)</div>
              )}
            </div>
          ) : (
            <div>✗ 失败: {result.error || result.detail || '未知错误'}</div>
          )}
        </div>
      )}
    </div>
  )
}
