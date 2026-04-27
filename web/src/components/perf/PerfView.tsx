/**
 * 性能监控 — 整机 + 每实例 + 瓶颈分析.
 *
 * 数据源: /api/perf/snapshot (3s 自动刷新)
 */
import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'

interface PerfSnapshot {
  ts: number
  global: {
    cpu_percent?: number
    mem_percent?: number
    mem_used_mb?: number
    mem_total_mb?: number
    process_count?: number
    error?: string
  }
  instances: Record<number, {
    fps: number
    adb_screenshot_ms: number
    tier_ms: Record<string, { count: number; avg: number; p95: number; max: number }>
    phase: string
    round: number
    health: 'ok' | 'slow' | 'stuck'
  }>
  bottleneck: Array<{
    ts: number
    instance: number
    phase: string
    round: number
    tier: string
    duration_ms: number
  }>
  metrics_path: string
  metrics_records_count: number
  window_s: number
}

function fmtMB(mb?: number): string {
  if (!mb) return '—'
  if (mb < 1024) return `${mb} MB`
  return `${(mb / 1024).toFixed(2)} GB`
}

function fmtTimeShort(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour12: false })
}

export function PerfView() {
  const [snap, setSnap] = useState<PerfSnapshot | null>(null)
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [window, setWindow] = useState<number>(30)
  const [loading, setLoading] = useState(false)

  async function load() {
    setLoading(true)
    try {
      const r = await fetch(`/api/perf/snapshot?window=${window}`)
      if (r.ok) setSnap(await r.json())
    } catch {} finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [window])

  useEffect(() => {
    if (!autoRefresh) return
    const t = setInterval(load, 3000)
    return () => clearInterval(t)
  }, [autoRefresh, window])

  return (
    <div className="flex flex-col gap-3 h-full min-h-0">
      <div className="flex items-center gap-2 flex-wrap">
        <h2 className="text-base font-semibold">性能监控</h2>
        <span className="text-xs text-muted-foreground">
          窗口
        </span>
        {[10, 30, 60, 300].map((w) => (
          <button
            key={w}
            onClick={() => setWindow(w)}
            className={`text-xs px-2 py-0.5 rounded border ${
              window === w ? 'bg-info/10 border-info/30 text-info' : 'border-border'
            }`}
          >
            {w}s
          </button>
        ))}
        <label className="ml-2 flex items-center gap-1 text-xs">
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={(e) => setAutoRefresh(e.target.checked)}
          />
          自动刷新 (3s)
        </label>
        {loading && <span className="text-xs text-info">刷新中…</span>}
        <Button variant="ghost" size="sm" className="ml-auto" onClick={load}>立刻刷新</Button>
      </div>

      {!snap ? (
        <div className="text-xs text-muted-foreground">加载中…</div>
      ) : (
        <div className="space-y-3 flex-1 overflow-y-auto">
          {/* 整机 */}
          <div className="rounded-lg border border-border bg-card p-3">
            <div className="text-xs font-semibold mb-2">整机</div>
            {snap.global.error ? (
              <div className="text-xs text-destructive">{snap.global.error}</div>
            ) : (
              <div className="grid gap-3 text-sm" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
                <Stat title="CPU" value={`${snap.global.cpu_percent ?? 0}%`}
                      barPct={snap.global.cpu_percent ?? 0}
                      color={(snap.global.cpu_percent ?? 0) > 80 ? 'red' : (snap.global.cpu_percent ?? 0) > 60 ? 'amber' : 'emerald'} />
                <Stat title="内存" value={`${snap.global.mem_percent ?? 0}%`}
                      barPct={snap.global.mem_percent ?? 0}
                      sub={`${fmtMB(snap.global.mem_used_mb)} / ${fmtMB(snap.global.mem_total_mb)}`}
                      color={(snap.global.mem_percent ?? 0) > 80 ? 'red' : (snap.global.mem_percent ?? 0) > 60 ? 'amber' : 'emerald'} />
                <Stat title="进程数" value={String(snap.global.process_count ?? '—')} />
                <Stat title="metrics 记录" value={String(snap.metrics_records_count)}
                      sub={`最近 ${snap.window_s}s 窗口`} />
              </div>
            )}
          </div>

          {/* 每实例 */}
          <div className="rounded-lg border border-border bg-card p-3">
            <div className="text-xs font-semibold mb-2">每实例</div>
            {Object.keys(snap.instances).length === 0 ? (
              <div className="text-xs text-muted-foreground">
                没有实例 metrics — 主 runner 跑起来才有数据 ({snap.metrics_path || '无 metrics.jsonl'})
              </div>
            ) : (
              <div className="grid gap-2"
                   style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))' }}>
                {Object.entries(snap.instances)
                  .map(([k, v]) => [Number(k), v] as const)
                  .sort(([a], [b]) => a - b)
                  .map(([idx, p]) => (
                    <InstancePerfCard key={idx} idx={idx} p={p as any} />
                  ))}
              </div>
            )}
          </div>

          {/* 瓶颈 */}
          {snap.bottleneck.length > 0 && (
            <div className="rounded-lg border border-border bg-card p-3">
              <div className="text-xs font-semibold mb-2">最慢 5 帧 (耗时 ≥ 50ms)</div>
              <ul className="space-y-1 font-mono text-[11px]">
                {snap.bottleneck.map((b, i) => (
                  <li key={i} className="flex items-center gap-2">
                    <span className="text-muted-foreground">{fmtTimeShort(b.ts)}</span>
                    <span>实例 #{b.instance}</span>
                    <span>{b.phase} R{b.round}</span>
                    <span className="text-info">{b.tier}</span>
                    <span className={`ml-auto font-bold ${
                      b.duration_ms > 200 ? 'text-destructive' : 'text-warning'
                    }`}>
                      {b.duration_ms}ms
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function Stat({ title, value, sub, barPct, color }: {
  title: string
  value: string
  sub?: string
  barPct?: number
  color?: 'red' | 'amber' | 'emerald'
}) {
  const colorCls = color === 'red' ? 'bg-destructive' : color === 'amber' ? 'bg-warning' : 'bg-success'
  return (
    <div className="space-y-1">
      <div className="text-[11px] text-muted-foreground">{title}</div>
      <div className="text-lg font-bold font-mono">{value}</div>
      {sub && <div className="text-[10px] text-muted-foreground">{sub}</div>}
      {barPct !== undefined && (
        <div className="h-1 bg-muted rounded overflow-hidden">
          <div className={colorCls} style={{ width: `${Math.min(100, barPct)}%`, height: '100%' }} />
        </div>
      )}
    </div>
  )
}

function InstancePerfCard({ idx, p }: {
  idx: number
  p: {
    fps: number
    adb_screenshot_ms: number
    tier_ms: Record<string, { count: number; avg: number; p95: number; max: number }>
    phase: string
    round: number
    health: string
  }
}) {
  const healthCls = p.health === 'slow' ? 'text-warning' :
                    p.health === 'stuck' ? 'text-destructive' : 'text-success'
  const tiers = Object.entries(p.tier_ms).sort((a, b) => b[1].avg - a[1].avg)
  return (
    <div className="rounded border border-border p-2 text-xs space-y-1">
      <div className="flex items-center gap-2">
        <span className="font-mono font-bold">#{idx}</span>
        <span className="text-muted-foreground">{p.phase || '—'} R{p.round || 0}</span>
        <span className={`ml-auto text-[11px] ${healthCls}`}>
          {p.health === 'slow' ? '⚠ 慢' : p.health === 'stuck' ? '✗ 卡' : '✓ 正常'}
        </span>
      </div>
      <div className="flex items-center gap-3 text-[11px]">
        <span>FPS <span className="font-mono font-bold">{p.fps || '—'}</span></span>
        <span>ADB 截屏 <span className="font-mono">{p.adb_screenshot_ms}ms</span></span>
      </div>
      {tiers.length > 0 && (
        <ul className="space-y-0.5">
          {tiers.map(([name, t]) => (
            <li key={name} className="flex items-center gap-2 text-[10px] font-mono">
              <span className="w-16 text-muted-foreground">{name}</span>
              <span className="flex-1">
                avg <b>{t.avg}ms</b>
                <span className="text-muted-foreground ml-1">p95 {t.p95} max {t.max}</span>
              </span>
              <span className="text-muted-foreground">×{t.count}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
