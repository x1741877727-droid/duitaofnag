/**
 * YOLO 数据集 tab — raw_screenshots 网格.
 *
 * 状态色:
 *   绿 = 已标 (有 .txt 且非空)
 *   橙 = 跳过 (空 .txt = 背景图)
 *   灰 = 未标
 *
 * 顶栏: 总数 / 已标 / 跳过 / 剩余 + 每类实例数 + 导出 zip + 过滤
 * 点缩略图 → onLabel(name) 切到标注 tab
 */
import { useEffect, useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import {
  fetchDataset,
  datasetImgSrc,
  deleteDatasetImage,
  exportZipUrl,
  type DatasetList,
  type DatasetItem,
} from '@/lib/yoloApi'

type Filter = 'all' | 'labeled' | 'skipped' | 'remaining'

export function YoloDataset({ onLabel }: { onLabel: (name: string) => void }) {
  const [data, setData] = useState<DatasetList | null>(null)
  const [filter, setFilter] = useState<Filter>('all')
  const [classFilter, setClassFilter] = useState<Set<number>>(new Set())   // 多选 cid; 空=不筛
  const [loading, setLoading] = useState(false)
  const [tick, setTick] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetchDataset()
      .then((d) => { if (!cancelled) setData(d) })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [tick])

  const filtered = useMemo(() => {
    if (!data) return []
    let list = data.items
    if (filter === 'labeled') list = list.filter((x) => x.labeled)
    else if (filter === 'skipped') list = list.filter((x) => x.skipped)
    else if (filter === 'remaining') list = list.filter((x) => !x.labeled && !x.skipped)
    if (classFilter.size > 0) {
      // 含任一选中 cid → 显示
      list = list.filter((x) => x.class_ids?.some((c) => classFilter.has(c)))
    }
    return list.slice().reverse() // 最新在前
  }, [data, filter, classFilter])

  function toggleClass(cid: number) {
    setClassFilter((cur) => {
      const n = new Set(cur)
      if (n.has(cid)) n.delete(cid)
      else n.add(cid)
      return n
    })
  }

  async function trash(name: string) {
    if (!confirm(`废弃图片 ${name}? (移到 .trash/)`)) return
    await deleteDatasetImage(name)
    setTick((x) => x + 1)
  }

  return (
    <div className="flex flex-col gap-2 h-full min-h-0">
      <div className="flex flex-wrap items-center gap-3 text-xs">
        {data ? (
          <>
            <span className="font-semibold">数据集</span>
            <span>总 <b className="font-mono">{data.total}</b></span>
            <span>已标 <b className="font-mono text-emerald-700">{data.labeled}</b></span>
            <span>跳过 <b className="font-mono text-amber-700">{data.skipped}</b></span>
            <span>剩余 <b className="font-mono">{data.remaining}</b></span>
            <span className="text-muted-foreground">|</span>
            {data.per_class.map((c) => (
              <span key={c.id} className="font-mono">
                {c.name}: <b>{c.instances}</b> ({c.images} 图)
              </span>
            ))}
          </>
        ) : (
          <span className="text-muted-foreground">{loading ? '加载中…' : '无数据'}</span>
        )}
        <a
          href={exportZipUrl()}
          download
          className="ml-auto text-xs text-blue-600 underline hover:text-blue-700"
        >
          下载训练 zip ↓
        </a>
        <Button variant="ghost" size="sm" onClick={() => setTick((x) => x + 1)}>刷新</Button>
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-xs text-muted-foreground">状态:</span>
        {(['all', 'labeled', 'skipped', 'remaining'] as Filter[]).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`text-xs px-2 py-1 rounded-md border transition ${
              filter === f
                ? 'bg-primary text-primary-foreground border-primary'
                : 'bg-card border-border text-muted-foreground hover:border-primary/40'
            }`}
          >
            {({ all: '全部', labeled: '已标', skipped: '跳过', remaining: '未标' } as const)[f]}
          </button>
        ))}
        {data && data.per_class.length > 0 && (
          <>
            <span className="ml-3 text-xs text-muted-foreground">含类别 (多选):</span>
            {data.per_class.filter((c) => c.id < (data.classes?.length || 0)).map((c) => {
              const sel = classFilter.has(c.id)
              return (
                <button
                  key={c.id}
                  onClick={() => toggleClass(c.id)}
                  className={`text-xs px-2 py-1 rounded-md border transition ${
                    sel
                      ? 'bg-info text-white border-info'
                      : 'bg-card border-border text-muted-foreground hover:border-info/40'
                  }`}
                >
                  {c.name} <span className="text-[10px] opacity-70">({c.images})</span>
                </button>
              )
            })}
            {classFilter.size > 0 && (
              <button
                onClick={() => setClassFilter(new Set())}
                className="text-xs text-muted-foreground hover:text-foreground underline"
              >
                清空类别筛选
              </button>
            )}
          </>
        )}
      </div>

      <div className="flex-1 overflow-y-auto rounded-lg border border-border bg-card p-3">
        {filtered.length === 0 ? (
          <div className="text-xs text-muted-foreground text-center py-8">
            没有图片 — 切到「采集」tab 抓几张
          </div>
        ) : (
          <div
            className="grid gap-2"
            style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))' }}
          >
            {filtered.map((it) => (
              <DatasetCard
                key={it.name}
                item={it}
                onLabel={() => onLabel(it.name)}
                onDelete={() => trash(it.name)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function DatasetCard({
  item, onLabel, onDelete,
}: {
  item: DatasetItem
  onLabel: () => void
  onDelete: () => void
}) {
  const stateCls = item.labeled
    ? 'border-emerald-300 bg-emerald-50/30'
    : item.skipped
      ? 'border-amber-300 bg-amber-50/30'
      : 'border-zinc-200 bg-zinc-50/40'
  const stateLabel = item.labeled ? '已标' : item.skipped ? '跳过' : '未标'
  const stateBadge = item.labeled
    ? 'bg-emerald-500 text-white'
    : item.skipped
      ? 'bg-amber-500 text-white'
      : 'bg-zinc-400 text-white'

  return (
    <div className={`group relative rounded border p-1.5 ${stateCls}`}>
      <button
        onClick={onLabel}
        className="block w-full bg-zinc-200 rounded h-28 overflow-hidden mb-1 hover:opacity-90 relative"
      >
        <img
          src={datasetImgSrc(item.name)}
          alt={item.name}
          className="w-full h-full object-cover"
          loading="lazy"
        />
        <span className={`absolute top-1 left-1 px-1.5 py-0.5 rounded text-[10px] font-bold ${stateBadge}`}>
          {stateLabel}
        </span>
      </button>
      <div className="text-[10px] font-mono truncate" title={item.name}>{item.name}</div>
      {/* 删除按钮: 默认隐藏, hover 才出来 */}
      <button
        onClick={(e) => { e.stopPropagation(); onDelete() }}
        className="absolute top-1 right-1 opacity-0 group-hover:opacity-100 bg-black/60 text-white text-[10px] w-6 h-6 rounded-full leading-6 transition"
        title="废弃 (移到 .trash/)"
      >
        ✕
      </button>
    </div>
  )
}
