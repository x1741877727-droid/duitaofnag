/**
 * 记忆库 — Memory L1 浏览 / 操作.
 *
 * 布局:
 *   顶: 总数 / 命中 / 成功率 / target chip
 *   左: 卡片网格 (缩略图 + 命中/失败计数 + 命中率徽章)
 *   右: 详情 (大图 + 元数据 + 类似条目 + 操作)
 */
import { useEffect, useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import {
  fetchMemoryList,
  fetchMemoryDetail,
  memorySnapshotSrc,
  deleteMemory,
  markMemoryFail,
  fetchPendingList,
  pendingSampleSrc,
  discardPending,
  type MemoryRecord,
  type MemoryDetail,
  type MemoryListResp,
  type PendingEntry,
  type PendingListResp,
} from '@/lib/memoryApi'

function fmtTime(ts: number): string {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false })
}

export function MemoryView() {
  const [data, setData] = useState<MemoryListResp | null>(null)
  const [pending, setPending] = useState<PendingListResp | null>(null)
  const [loading, setLoading] = useState(false)
  const [target, setTarget] = useState<string>('')
  const [selId, setSelId] = useState<number | null>(null)
  const [detail, setDetail] = useState<MemoryDetail | null>(null)
  const [tick, setTick] = useState(0)
  const [viewer, setViewer] = useState<{ entry: PendingEntry; idx: number } | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    Promise.allSettled([fetchMemoryList(target), fetchPendingList(target)])
      .then(([listR, pendR]) => {
        if (cancelled) return
        if (listR.status === 'fulfilled') setData(listR.value)
        if (pendR.status === 'fulfilled') setPending(pendR.value)
      })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [target, tick])

  // pending 刷新 (5s 一次, 不闪屏)
  useEffect(() => {
    const id = setInterval(() => {
      fetchPendingList(target).then((d) => setPending(d)).catch(() => {})
    }, 5000)
    return () => clearInterval(id)
  }, [target])

  useEffect(() => {
    if (selId === null) { setDetail(null); return }
    let cancelled = false
    fetchMemoryDetail(selId)
      .then((d) => { if (!cancelled) setDetail(d) })
      .catch(() => { if (!cancelled) setDetail(null) })
    return () => { cancelled = true }
  }, [selId, tick])

  const overall = useMemo(() => {
    if (!data) return null
    let s = 0, f = 0, h = 0
    for (const it of data.items) { s += it.success_count; f += it.fail_count; h += it.hit_count }
    return { total: data.count, hits: h, succ: s, fail: f, rate: (s + f > 0 ? s / (s + f) : 0) }
  }, [data])

  async function onDelete(id: number) {
    if (!confirm(`删除记忆 #${id}? (不可撤销)`)) return
    try { await deleteMemory(id) } catch (e) { alert(String(e)); return }
    setSelId(null)
    setTick((x) => x + 1)
  }
  async function onMarkFail(id: number) {
    try { await markMemoryFail(id) } catch (e) { alert(String(e)); return }
    setTick((x) => x + 1)
  }
  async function onDiscardPending(key: string) {
    if (!confirm(`丢弃这条 pending? (清掉所有样本快照, 重新累计)`)) return
    try { await discardPending(key) } catch (e) { alert(String(e)); return }
    setViewer(null)
    setTick((x) => x + 1)
  }

  return (
    <div className="flex flex-col gap-3 h-full min-h-0">
      {/* 顶栏 */}
      <div className="flex flex-wrap items-center gap-3 text-xs">
        <h2 className="text-base font-semibold">记忆库</h2>
        {!data?.available ? (
          <span className="text-muted-foreground">db 未就绪</span>
        ) : overall ? (
          <>
            <span>总 <b className="font-mono">{overall.total}</b> 条</span>
            <span>累计命中 <b className="font-mono">{overall.hits}</b></span>
            <span>成功 <b className="font-mono text-success">{overall.succ}</b> / 失败 <b className="font-mono text-destructive">{overall.fail}</b></span>
            <span>命中率 <b className="font-mono">{(overall.rate * 100).toFixed(1)}%</b></span>
          </>
        ) : null}
        <span className="text-muted-foreground ml-2">target:</span>
        <button
          onClick={() => setTarget('')}
          className={`text-xs px-2 py-1 rounded-md border transition ${
            target === ''
              ? 'bg-primary text-primary-foreground border-primary'
              : 'bg-card border-border text-muted-foreground hover:border-primary/40'
          }`}
        >全部</button>
        {(data?.targets || []).map((t) => (
          <button
            key={t.name}
            onClick={() => setTarget(t.name)}
            className={`text-xs px-2 py-1 rounded-md border transition ${
              target === t.name
                ? 'bg-primary text-primary-foreground border-primary'
                : 'bg-card border-border text-muted-foreground hover:border-primary/40'
            }`}
          >
            {t.name} <span className="text-[10px] opacity-70">({t.count})</span>
          </button>
        ))}
        <Button variant="ghost" size="sm" className="ml-auto" onClick={() => setTick((x) => x + 1)}>刷新</Button>
      </div>

      {/* 蓄水池 Pending 区 (顶部, 跟已入库分开显示) */}
      <PendingSection
        pending={pending}
        onView={(entry, idx) => setViewer({ entry, idx })}
        onDiscard={onDiscardPending}
      />

      <div className="text-[11px] text-muted-foreground -mb-1">
        ✅ 已入库 ({data?.items.length ?? 0}) — 已通过 ≥{pending?.items[0]?.needed ?? 5} 次确认 + std 检查
      </div>

      <div className="flex-1 grid gap-3 min-h-0" style={{ gridTemplateColumns: '1fr 420px' }}>
        {/* 左: 卡片 */}
        <div className="rounded-lg border border-border bg-card p-3 overflow-y-auto">
          {loading ? (
            <div className="text-xs text-muted-foreground">加载中…</div>
          ) : !data || data.items.length === 0 ? (
            <div className="text-xs text-muted-foreground text-center py-8">
              没有记忆 — 跑 P2 清弹窗成功几次就有了 (每次 P2 进大厅时 commit pending memory)
            </div>
          ) : (
            <div className="grid gap-2"
                 style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))' }}>
              {data.items.map((m) => (
                <MemCard key={m.id} m={m}
                         selected={selId === m.id}
                         onClick={() => setSelId(m.id)} />
              ))}
            </div>
          )}
        </div>

        {/* 右: 详情 */}
        <div className="rounded-lg border border-border bg-card overflow-y-auto">
          {!selId ? (
            <div className="p-6 text-xs text-muted-foreground text-center">
              选条记忆看详情
            </div>
          ) : !detail ? (
            <div className="p-6 text-xs text-muted-foreground text-center">加载中…</div>
          ) : (
            <MemDetail
              d={detail}
              onDelete={() => onDelete(detail.id)}
              onMarkFail={() => onMarkFail(detail.id)}
            />
          )}
        </div>
      </div>

      {viewer && (
        <PendingSampleViewer
          entry={viewer.entry}
          idx={viewer.idx}
          onClose={() => setViewer(null)}
          onChange={(idx) => setViewer({ entry: viewer.entry, idx })}
          onDiscard={() => onDiscardPending(viewer.entry.key)}
        />
      )}
    </div>
  )
}


function MemCard({ m, selected, onClick }: { m: MemoryRecord; selected: boolean; onClick: () => void }) {
  const total = m.success_count + m.fail_count
  const rate = total > 0 ? m.success_count / total : 0
  const rateColor = rate >= 0.8 ? 'text-success' : rate >= 0.5 ? 'text-warning' : 'text-destructive'
  return (
    <button
      onClick={onClick}
      className={`group rounded-md border p-1.5 text-left transition ${
        selected ? 'border-primary ring-2 ring-primary/30' : 'border-border bg-card hover:border-primary/40'
      }`}
    >
      <div className="bg-foreground/95 rounded h-24 overflow-hidden mb-1.5 relative">
        {m.snapshot_path ? (
          <img
            src={memorySnapshotSrc(m.id)}
            alt={`#${m.id}`}
            className="w-full h-full object-cover"
            loading="lazy"
            onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none' }}
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-[10px] text-background/60">
            (旧记录无快照)
          </div>
        )}
        <span className="absolute top-1 left-1 bg-foreground/70 text-background text-[10px] px-1.5 py-0.5 rounded">
          #{m.id}
        </span>
      </div>
      <div className="flex items-center gap-2 text-[11px]">
        <span className="font-mono">({m.action_x},{m.action_y})</span>
        <span className={`ml-auto font-mono font-bold ${rateColor}`}>
          {total > 0 ? `${(rate * 100).toFixed(0)}%` : '新'}
        </span>
      </div>
      <div className="text-[10px] text-muted-foreground mt-0.5">
        {m.target_name} · {m.success_count}✓/{m.fail_count}✗
      </div>
    </button>
  )
}

function MemDetail({ d, onDelete, onMarkFail }: {
  d: MemoryDetail
  onDelete: () => void
  onMarkFail: () => void
}) {
  const total = d.success_count + d.fail_count
  const rate = total > 0 ? d.success_count / total : 0
  const isStale = d.fail_count >= 5 && d.fail_count > d.success_count
  return (
    <div className="flex flex-col h-full">
      <div className="px-3 py-2 border-b border-border flex items-center gap-2">
        <span className="font-mono font-bold">#{d.id}</span>
        <span className="text-xs text-muted-foreground">{d.target_name}</span>
        {isStale && (
          <span className="text-[10px] px-1.5 py-0.5 bg-destructive/10 text-destructive rounded">已失效</span>
        )}
        <Button size="sm" variant="ghost" onClick={onDelete} className="ml-auto text-destructive text-[11px]">
          删除
        </Button>
      </div>

      <div className="p-3 space-y-3 overflow-y-auto">
        {/* 原图 + 红圈点击位置 */}
        <div>
          <div className="text-xs font-semibold mb-1">记录时的画面 (红圈 = 点击位置)</div>
          {d.snapshot_path ? (
            <img
              src={memorySnapshotSrc(d.id)}
              alt={`#${d.id}`}
              className="w-full rounded border border-border"
            />
          ) : (
            <div className="rounded border border-dashed border-border bg-muted p-6 text-xs text-muted-foreground text-center">
              旧记录没保存快照 (本次重启后新写入的会有)
            </div>
          )}
        </div>

        {/* 元数据 */}
        <div className="rounded border border-border bg-secondary p-2 text-[11px] space-y-0.5">
          <div className="font-mono break-all">phash: {d.phash}</div>
          <div>点击位置: <span className="font-mono">({d.action_x}, {d.action_y})</span></div>
          {(d.action_w > 0 || d.action_h > 0) && (
            <div>目标尺寸: <span className="font-mono">{d.action_w}×{d.action_h}</span></div>
          )}
          <div>最后使用: {fmtTime(d.last_seen_ts)}</div>
        </div>

        {/* 命中统计 */}
        <div className="grid grid-cols-3 gap-2 text-center">
          <div className="rounded border border-border bg-card p-2">
            <div className="text-[10px] text-muted-foreground">累计命中</div>
            <div className="text-base font-mono font-bold">{d.hit_count}</div>
          </div>
          <div className="rounded border border-success/30 bg-success-muted p-2">
            <div className="text-[10px] text-success">成功</div>
            <div className="text-base font-mono font-bold text-success">{d.success_count}</div>
          </div>
          <div className="rounded border border-destructive/30 bg-destructive/10 p-2">
            <div className="text-[10px] text-destructive">失败</div>
            <div className="text-base font-mono font-bold text-destructive">{d.fail_count}</div>
          </div>
        </div>

        <div className="flex items-center gap-2 text-xs">
          <span>命中率:</span>
          <span className={`font-mono font-bold ${rate >= 0.8 ? 'text-success' : rate >= 0.5 ? 'text-warning' : 'text-destructive'}`}>
            {total > 0 ? `${(rate * 100).toFixed(1)}%` : '尚未使用'}
          </span>
          {isStale && (
            <span className="text-destructive">— 已被自动忽略 (失败 ≥ 5 且 失败 &gt; 成功)</span>
          )}
        </div>

        {/* 操作 */}
        <div className="flex gap-2 border-t border-border pt-3">
          <Button size="sm" variant="outline" onClick={onMarkFail}>
            +1 失败 (这条点错了)
          </Button>
          <span className="text-[11px] text-muted-foreground self-center">
            达到失败 ≥ 5 且 &gt; 成功 → 自动忽略
          </span>
        </div>

        {/* 类似条目 */}
        {d.similar.length > 0 && (
          <div className="border-t border-border pt-3">
            <div className="text-xs font-semibold mb-1">类似条目 ({d.similar.length}, phash 距离 ≤ 5)</div>
            <ul className="space-y-1 text-[11px]">
              {d.similar.map((s) => (
                <li key={s.id} className="flex items-center gap-2 font-mono">
                  <span className="text-muted-foreground">dist={s.phash_dist}</span>
                  <span className="text-muted-foreground">#{s.id}</span>
                  <span>({s.action_xy[0]},{s.action_xy[1]})</span>
                  <span className="ml-auto text-success">{s.succ}✓</span>
                  <span className="text-destructive">{s.fail}✗</span>
                </li>
              ))}
            </ul>
            <div className="text-[10px] text-muted-foreground mt-1">
              ⓘ 类似条目 ≥ 1 通常表示有冗余记录, 可考虑合并/删除部分
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ──────── Pending (蓄水池) section ────────

function PendingSection({
  pending,
  onView,
  onDiscard,
}: {
  pending: PendingListResp | null
  onView: (entry: PendingEntry, idx: number) => void
  onDiscard: (key: string) => void
}) {
  if (!pending || !pending.available) return null
  const items = pending.items
  return (
    <div className="rounded-lg border border-warning/40 bg-warning-muted/30 p-2.5">
      <div className="flex items-center gap-2 text-xs mb-1.5">
        <span className="font-semibold">📦 待入库 (蓄水池)</span>
        <span className="text-muted-foreground">
          {items.length} 条 · 累计 ≥{items[0]?.needed ?? 5} 次 + std 检查后才真入库
        </span>
      </div>
      {items.length === 0 ? (
        <div className="text-[11px] text-muted-foreground py-1">
          暂无待入库条目 — 跑一轮自动化, 同坐标累计 5 次会出现在这里
        </div>
      ) : (
        <div className="grid gap-1.5"
             style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))' }}>
          {items.map((e) => (
            <PendingCard key={e.key} e={e} onView={onView} onDiscard={onDiscard} />
          ))}
        </div>
      )}
    </div>
  )
}

function PendingCard({
  e,
  onView,
  onDiscard,
}: {
  e: PendingEntry
  onView: (entry: PendingEntry, idx: number) => void
  onDiscard: (key: string) => void
}) {
  const progress = Math.min(e.samples / e.needed, 1)
  const stdBad = e.std_x > e.max_std_allowed || e.std_y > e.max_std_allowed
  return (
    <div className="rounded-md border border-border bg-card p-2 text-[11px]">
      <div className="flex items-center gap-2 mb-1">
        <span className="font-semibold truncate flex-1">{e.target_name}</span>
        <span className="font-mono">{e.samples}/{e.needed}</span>
      </div>
      {/* 进度条 */}
      <div className="h-1.5 rounded bg-muted overflow-hidden mb-1.5">
        <div className={`h-full ${stdBad ? 'bg-destructive' : 'bg-warning'}`}
             style={{ width: `${progress * 100}%` }} />
      </div>
      <div className="flex items-center gap-2 text-[10px] text-muted-foreground mb-1">
        <span>📍 ({e.median_xy[0]},{e.median_xy[1]})</span>
        {e.samples < 2 ? (
          <span className="text-muted-foreground/60" title="只有 1 个样本, σ 暂无意义">
            σ —
          </span>
        ) : e.std_x === 0 && e.std_y === 0 ? (
          <span className="text-success" title="所有样本同坐标, 完美一致">
            σ 0px ✓
          </span>
        ) : (
          <span className={stdBad ? 'text-destructive font-bold' : 'text-success'}
                title={`阈值 ${e.max_std_allowed}px, 超过则拒绝入库`}>
            σ {e.std_x}/{e.std_y}px
          </span>
        )}
        <span className="ml-auto">{e.age_s}s</span>
      </div>
      {/* 样本按钮 */}
      <div className="flex flex-wrap gap-1">
        {e.samples_detail.map((s) => (
          <button
            key={s.idx}
            onClick={() => onView(e, s.idx)}
            disabled={!s.has_snapshot}
            className="px-1.5 py-0.5 rounded border border-border bg-secondary hover:border-primary/50 hover:bg-primary/5 transition disabled:opacity-40 disabled:cursor-not-allowed text-[10px] font-mono"
            title={`样本 ${s.idx} (${s.x},${s.y})`}
          >
            [{s.idx}]
          </button>
        ))}
        <button
          onClick={() => onDiscard(e.key)}
          className="ml-auto px-1.5 py-0.5 rounded text-destructive hover:bg-destructive/10 text-[10px]"
        >
          丢弃
        </button>
      </div>
    </div>
  )
}

function PendingSampleViewer({
  entry,
  idx,
  onClose,
  onChange,
  onDiscard,
}: {
  entry: PendingEntry
  idx: number
  onClose: () => void
  onChange: (idx: number) => void
  onDiscard: () => void
}) {
  const sample = entry.samples_detail[idx]
  const total = entry.samples_detail.length
  const prev = () => onChange((idx - 1 + total) % total)
  const next = () => onChange((idx + 1) % total)
  if (!sample) return null
  return (
    <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-6"
         onClick={onClose}>
      <div className="bg-card border border-border rounded-lg max-w-3xl w-full max-h-[90vh] overflow-hidden flex flex-col"
           onClick={(e) => e.stopPropagation()}>
        <div className="px-3 py-2 border-b border-border flex items-center gap-2 text-xs">
          <span className="font-semibold">{entry.target_name}</span>
          <span className="text-muted-foreground">样本 {idx + 1}/{total}</span>
          <span className="font-mono text-muted-foreground">@({sample.x},{sample.y})</span>
          <span className="text-muted-foreground">·  {sample.age_s}s 前</span>
          <Button size="sm" variant="ghost" className="ml-auto text-destructive" onClick={onDiscard}>
            丢弃整条
          </Button>
          <Button size="sm" variant="ghost" onClick={onClose}>关闭</Button>
        </div>
        <div className="flex-1 overflow-auto bg-foreground/95 flex items-center justify-center relative">
          <img
            src={pendingSampleSrc(entry.key, idx)}
            alt={`sample ${idx}`}
            className="max-w-full max-h-[70vh] object-contain"
            onError={(e) => {
              const t = e.currentTarget as HTMLImageElement
              t.style.display = 'none'
              const fallback = t.nextElementSibling as HTMLElement | null
              if (fallback) fallback.style.display = 'flex'
            }}
          />
          <div className="hidden absolute inset-0 items-center justify-center text-background/70 text-xs">
            (快照不可用)
          </div>
        </div>
        <div className="px-3 py-2 border-t border-border flex items-center gap-2">
          <Button size="sm" variant="outline" onClick={prev} disabled={total <= 1}>← 上一张</Button>
          <div className="flex gap-1 mx-auto">
            {entry.samples_detail.map((s) => (
              <button
                key={s.idx}
                onClick={() => onChange(s.idx)}
                className={`w-6 h-6 rounded text-[10px] font-mono border transition ${
                  s.idx === idx
                    ? 'bg-primary text-primary-foreground border-primary'
                    : 'bg-secondary border-border hover:border-primary/40'
                }`}
              >
                {s.idx}
              </button>
            ))}
          </div>
          <Button size="sm" variant="outline" onClick={next} disabled={total <= 1}>下一张 →</Button>
        </div>
      </div>
    </div>
  )
}

