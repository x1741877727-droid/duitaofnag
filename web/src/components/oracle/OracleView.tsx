// Oracle 集 — 决策回放 + 回归批跑面板.
//
// 工作流:
//   1. 在 Archive 页给某条 decision 标"错误 + 正确位置" -> POST /api/oracle
//   2. 这里看积累的 oracle 列表 + 点"重跑全集"看哪些回归
//   3. PASS = 当前代码符合期望 / FAIL_* = 哪类不符合
//
// 颜色编码: 通过=绿, FAIL_WRONG_TARGET=橙, FAIL_NO_ACTION=红, FAIL_OVER_ACTING=红, ERROR=灰

import { useEffect, useState, useMemo } from 'react'
import {
  listOracles, replayAll, deleteOracle,
  type OracleEntry, type ReplayAllResult, type OracleComparison,
} from '@/lib/oracleApi'

const MATCH_COLORS: Record<OracleComparison['match'], string> = {
  PASS: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40',
  FAIL_WRONG_TARGET: 'bg-amber-500/15 text-amber-300 border-amber-500/40',
  FAIL_NO_ACTION: 'bg-red-500/15 text-red-300 border-red-500/40',
  FAIL_OVER_ACTING: 'bg-red-500/15 text-red-300 border-red-500/40',
  ERROR: 'bg-zinc-500/15 text-zinc-400 border-zinc-500/40',
}

const MATCH_LABELS: Record<OracleComparison['match'], string> = {
  PASS: '通过',
  FAIL_WRONG_TARGET: '位置偏',
  FAIL_NO_ACTION: '没动作',
  FAIL_OVER_ACTING: '不该动',
  ERROR: '回放出错',
}

function fmtTime(ts: number): string {
  const d = new Date(ts * 1000)
  return d.toLocaleString('zh-CN', { hour12: false })
}

export function OracleView() {
  const [oracles, setOracles] = useState<OracleEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [replayResult, setReplayResult] = useState<ReplayAllResult | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')

  async function refresh() {
    setLoading(true)
    setError('')
    try {
      const r = await listOracles()
      setOracles(r.oracles)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { refresh() }, [])

  async function onReplayAll() {
    setRunning(true)
    setError('')
    try {
      const r = await replayAll()
      setReplayResult(r)
    } catch (e) {
      setError(String(e))
    } finally {
      setRunning(false)
    }
  }

  async function onDelete(id: string) {
    if (!confirm(`删除 oracle ${id}?`)) return
    try {
      await deleteOracle(id)
      await refresh()
      if (replayResult) {
        setReplayResult({
          ...replayResult,
          results: replayResult.results.filter(r => r.id !== id),
          total: replayResult.total - 1,
        })
      }
    } catch (e) {
      setError(String(e))
    }
  }

  const resultsById = useMemo(() => {
    const m = new Map<string, ReplayAllResult['results'][number]>()
    for (const r of replayResult?.results ?? []) m.set(r.id, r)
    return m
  }, [replayResult])

  const passRate = replayResult && replayResult.total > 0
    ? Math.round((replayResult.passed / replayResult.total) * 100)
    : null

  return (
    <div className="h-full flex flex-col gap-3">
      {/* 顶栏 */}
      <div className="flex items-center gap-3 flex-wrap">
        <div>
          <h2 className="text-lg font-semibold">Oracle 集</h2>
          <p className="text-xs text-muted-foreground">
            标注过的"决策应该这样"清单 — 每次代码改完跑一遍验证回归
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={refresh}
            disabled={loading}
            className="text-xs px-2 py-1 rounded border border-border hover:bg-muted disabled:opacity-50"
          >
            {loading ? '...' : '刷新'}
          </button>
          <button
            onClick={onReplayAll}
            disabled={running || oracles.length === 0}
            className="text-xs px-3 py-1 rounded bg-primary text-primary-foreground font-semibold disabled:opacity-50"
          >
            {running ? '回放中...' : `重跑全集 (${oracles.length})`}
          </button>
        </div>
      </div>

      {/* 错误提示 */}
      {error && (
        <div className="text-xs text-red-300 bg-red-500/10 border border-red-500/40 rounded px-3 py-2">
          {error}
        </div>
      )}

      {/* 汇总卡片 */}
      {replayResult && (
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 text-xs">
          <SummaryCard label="总计" value={replayResult.total} tone="neutral" />
          <SummaryCard
            label="通过"
            value={`${replayResult.passed}${passRate !== null ? ` (${passRate}%)` : ''}`}
            tone="pass"
          />
          <SummaryCard label="位置偏" value={replayResult.by_kind.FAIL_WRONG_TARGET || 0} tone="warn" />
          <SummaryCard
            label="没动作"
            value={(replayResult.by_kind.FAIL_NO_ACTION || 0) + (replayResult.by_kind.FAIL_OVER_ACTING || 0)}
            tone="fail"
          />
          <SummaryCard label="出错" value={replayResult.by_kind.ERROR || 0} tone="neutral" />
        </div>
      )}

      {/* Oracle 列表 */}
      <div className="flex-1 overflow-auto rounded-lg border border-border bg-card">
        {oracles.length === 0 && !loading && (
          <div className="text-center text-sm text-muted-foreground py-12 px-4">
            <div className="font-medium mb-2">还没有 oracle</div>
            <div className="text-xs">
              到 <span className="text-primary">档案</span> 页面找一条错的决策, 点 <span className="font-mono bg-muted px-1 rounded">"标记错误"</span> 标注正确位置即可
            </div>
          </div>
        )}
        {oracles.length > 0 && (
          <table className="w-full text-xs">
            <thead className="bg-muted/50 sticky top-0">
              <tr className="text-left">
                <th className="px-2 py-1.5">状态</th>
                <th className="px-2 py-1.5">期望</th>
                <th className="px-2 py-1.5">原决策</th>
                <th className="px-2 py-1.5">代码现在选</th>
                <th className="px-2 py-1.5">距离</th>
                <th className="px-2 py-1.5">备注</th>
                <th className="px-2 py-1.5">创建</th>
                <th className="px-2 py-1.5"></th>
              </tr>
            </thead>
            <tbody>
              {oracles.map(o => {
                const result = resultsById.get(o.id)
                const ann = o.annotation
                const orig = o.original_action as Record<string, unknown>
                const cur = result?.current
                return (
                  <tr key={o.id} className="border-t border-border hover:bg-muted/30">
                    <td className="px-2 py-1.5">
                      {result ? (
                        <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold border ${MATCH_COLORS[result.match]}`}>
                          {MATCH_LABELS[result.match]}
                        </span>
                      ) : (
                        <span className="text-muted-foreground">未跑</span>
                      )}
                    </td>
                    <td className="px-2 py-1.5">
                      {ann.correct === 'no_action' ? (
                        <span className="text-zinc-400">不动作</span>
                      ) : (
                        <>
                          <span className="font-mono">{ann.label || '?'}</span>
                          <span className="text-muted-foreground"> @ ({ann.click_x},{ann.click_y})</span>
                        </>
                      )}
                    </td>
                    <td className="px-2 py-1.5 font-mono">
                      <span className="text-muted-foreground">
                        {String(orig.label || orig.method || '-')}
                      </span>
                      {orig.x !== null && orig.x !== undefined && (
                        <span className="text-muted-foreground"> @ ({String(orig.x)},{String(orig.y)})</span>
                      )}
                    </td>
                    <td className="px-2 py-1.5 font-mono">
                      {cur ? (
                        <>
                          <span>{cur.label}</span>
                          <span className="text-muted-foreground"> @ ({cur.x},{cur.y})</span>
                          <span className="text-muted-foreground/70"> conf={cur.conf}</span>
                        </>
                      ) : result ? (
                        <span className="text-muted-foreground">无</span>
                      ) : (
                        <span className="text-muted-foreground/50">—</span>
                      )}
                    </td>
                    <td className="px-2 py-1.5 font-mono">
                      {result?.distance_px !== undefined ? `${result.distance_px}px` : '—'}
                    </td>
                    <td className="px-2 py-1.5 text-muted-foreground max-w-[200px] truncate" title={ann.note}>
                      {ann.note || '—'}
                    </td>
                    <td className="px-2 py-1.5 text-muted-foreground text-[10px]">
                      {fmtTime(o.created_at)}
                    </td>
                    <td className="px-2 py-1.5">
                      <button
                        onClick={() => onDelete(o.id)}
                        className="text-red-400 hover:text-red-300 text-[10px]"
                      >删</button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* 详情 / reason 列表 (仅失败的) */}
      {replayResult && replayResult.failed > 0 && (
        <div className="rounded-lg border border-border bg-card p-3 max-h-[200px] overflow-auto">
          <div className="text-xs font-semibold mb-2">失败详情 ({replayResult.failed})</div>
          <ul className="space-y-1 text-[11px]">
            {replayResult.results.filter(r => r.match !== 'PASS').map(r => (
              <li key={r.id} className="font-mono">
                <span className={`inline-block px-1 py-0.5 rounded mr-2 text-[9px] border ${MATCH_COLORS[r.match]}`}>
                  {MATCH_LABELS[r.match]}
                </span>
                <span className="text-muted-foreground">{r.decision_id}</span>
                <span className="ml-2">{r.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function SummaryCard({ label, value, tone }: { label: string; value: number | string; tone: 'pass' | 'warn' | 'fail' | 'neutral' }) {
  const toneClass = {
    pass: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300',
    warn: 'border-amber-500/40 bg-amber-500/10 text-amber-300',
    fail: 'border-red-500/40 bg-red-500/10 text-red-300',
    neutral: 'border-border bg-muted/40 text-foreground',
  }[tone]
  return (
    <div className={`rounded border px-3 py-2 ${toneClass}`}>
      <div className="text-[10px] opacity-70">{label}</div>
      <div className="text-base font-semibold">{value}</div>
    </div>
  )
}
