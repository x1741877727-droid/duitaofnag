/**
 * 模版库 — 主页.
 *
 * 布局:
 *   顶栏: 搜索 + 分类筛选 + [+裁剪截图]
 *   中: 卡片网格 (按分类分组)
 *   右下浮层: 选中模版详情 + 命中率 + [测试] / [删除]
 *
 * 测试 (dryrun):
 *   选模版 + 选源 (实例 # / 上传图 / 历史决策) → /api/templates/test → 显示标注图.
 */
import { useEffect, useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useAppStore } from '@/lib/store'
import {
  fetchTemplateList,
  fetchTemplateStats,
  templateImgSrc,
  testTemplate,
  deleteTemplate,
  type TemplateMeta,
  type TemplateStats,
  type TemplateTestResult,
} from '@/lib/templatesApi'
import { TemplateCropper } from './TemplateCropper'

const CAT_ORDER = [
  '加速器', '大厅', '关闭按钮', '操作按钮', '卡片',
  '模式', '地图', '标签页', '文本', '杂项',
]

export function TemplateLibrary() {
  const instances = useAppStore((s) => s.instances)
  const [items, setItems] = useState<TemplateMeta[]>([])
  const [search, setSearch] = useState('')
  const [activeCat, setActiveCat] = useState<string>('全部')
  const [selected, setSelected] = useState<TemplateMeta | null>(null)
  const [stats, setStats] = useState<TemplateStats | null>(null)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<TemplateTestResult | null>(null)
  const [testInst, setTestInst] = useState<number | null>(null)
  const [cropperOpen, setCropperOpen] = useState(false)
  const [refreshTick, setRefreshTick] = useState(0)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetchTemplateList()
      .then((d) => { if (!cancelled) setItems(d.items) })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [refreshTick])

  // 选中变化 → 拉 stats
  useEffect(() => {
    if (!selected) { setStats(null); return }
    let cancelled = false
    fetchTemplateStats(selected.name)
      .then((s) => { if (!cancelled) setStats(s) })
      .catch(() => { if (!cancelled) setStats(null) })
    return () => { cancelled = true }
  }, [selected])

  const cats = useMemo(() => {
    const cnt = new Map<string, number>()
    for (const it of items) cnt.set(it.category, (cnt.get(it.category) || 0) + 1)
    const list = CAT_ORDER.filter((c) => cnt.has(c)).map((c) => ({ name: c, count: cnt.get(c)! }))
    // 任何不在 ORDER 里的也加上
    for (const [k, v] of cnt) if (!CAT_ORDER.includes(k)) list.push({ name: k, count: v })
    return list
  }, [items])

  const filtered = useMemo(() => {
    let list = items
    if (activeCat !== '全部') list = list.filter((it) => it.category === activeCat)
    if (search.trim()) {
      const s = search.toLowerCase()
      list = list.filter((it) => it.name.toLowerCase().includes(s))
    }
    return list
  }, [items, activeCat, search])

  async function runTest() {
    if (!selected) return
    setTesting(true)
    setTestResult(null)
    try {
      let args: { name: string; instance?: number; threshold?: number } = {
        name: selected.name,
      }
      const tgt = testInst ?? Object.values(instances)[0]?.index
      if (tgt !== undefined) args.instance = tgt
      const r = await testTemplate(args)
      setTestResult(r)
    } catch (e) {
      setTestResult({
        template: selected.name, threshold: 0, hit: false, score: 0,
        cx: 0, cy: 0, w: 0, h: 0, bbox: null,
        duration_ms: 0, annotated_b64: '',
        note: '测试失败: ' + String(e),
      })
    } finally {
      setTesting(false)
    }
  }

  async function doDelete(name: string) {
    if (!confirm(`确定删除模版 ${name}?`)) return
    try {
      await deleteTemplate(name)
      setSelected(null)
      setRefreshTick((x) => x + 1)
    } catch (e) {
      alert('删除失败: ' + String(e))
    }
  }

  return (
    <div className="flex flex-col gap-3 h-full min-h-0">
      {/* 顶栏 */}
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-base font-semibold">模版库</h2>
        <span className="text-xs text-muted-foreground">{items.length} 个模版</span>
        <Input
          placeholder="搜索模版名..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="h-8 w-48 text-xs"
        />
        <div className="flex flex-wrap gap-1">
          <button
            onClick={() => setActiveCat('全部')}
            className={`text-xs px-2 py-1 rounded border ${
              activeCat === '全部' ? 'bg-blue-50 border-blue-300 text-blue-700' : 'border-border text-muted-foreground'
            }`}
          >
            全部 ({items.length})
          </button>
          {cats.map((c) => (
            <button
              key={c.name}
              onClick={() => setActiveCat(c.name)}
              className={`text-xs px-2 py-1 rounded border ${
                activeCat === c.name ? 'bg-blue-50 border-blue-300 text-blue-700' : 'border-border text-muted-foreground'
              }`}
            >
              {c.name} ({c.count})
            </button>
          ))}
        </div>
        <Button
          variant="outline"
          size="sm"
          className="ml-auto"
          onClick={() => setCropperOpen(true)}
        >
          + 裁剪截图做模版
        </Button>
      </div>

      <div className="flex-1 grid gap-3 min-h-0" style={{ gridTemplateColumns: '1fr 380px' }}>
        {/* 左: 网格 */}
        <div className="rounded-lg border border-border bg-card p-3 overflow-y-auto">
          {loading ? (
            <div className="text-xs text-muted-foreground">加载中…</div>
          ) : filtered.length === 0 ? (
            <div className="text-xs text-muted-foreground">没有匹配的模版</div>
          ) : (
            <div className="grid gap-2"
                 style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))' }}>
              {filtered.map((it) => {
                const sel = selected?.name === it.name
                return (
                  <button
                    key={it.name}
                    onClick={() => setSelected(it)}
                    className={`rounded border p-2 text-left transition-all hover:shadow ${
                      sel ? 'border-blue-400 bg-blue-50/40 ring-1 ring-blue-300' : 'border-border bg-background'
                    }`}
                  >
                    <div className="bg-zinc-100 rounded h-20 flex items-center justify-center overflow-hidden">
                      <img
                        src={templateImgSrc(it.name)}
                        alt={it.name}
                        className="max-w-full max-h-full object-contain"
                      />
                    </div>
                    <div className="mt-1 text-[11px] font-mono truncate">{it.name}</div>
                    <div className="text-[10px] text-muted-foreground flex items-center gap-1.5">
                      <span>{it.width}×{it.height}</span>
                      <span>·</span>
                      <span>{it.category}</span>
                    </div>
                  </button>
                )
              })}
            </div>
          )}
        </div>

        {/* 右: 详情 + 测试 */}
        <div className="rounded-lg border border-border bg-card flex flex-col overflow-hidden">
          {!selected ? (
            <div className="p-6 text-xs text-muted-foreground text-center">
              选个模版看详情和测试
            </div>
          ) : (
            <>
              <div className="p-3 border-b border-border space-y-2">
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-sm font-mono truncate flex-1">
                    {selected.name}
                  </span>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="text-red-600 text-[11px]"
                    onClick={() => doDelete(selected.name)}
                  >
                    删除
                  </Button>
                </div>
                <div className="bg-zinc-100 rounded p-2 flex items-center justify-center max-h-[180px]">
                  <img
                    src={templateImgSrc(selected.name)}
                    alt={selected.name}
                    className="max-w-full max-h-[160px] object-contain"
                  />
                </div>
                <div className="text-[11px] text-muted-foreground space-y-0.5">
                  <div>分类: {selected.category}</div>
                  <div>尺寸: {selected.width} × {selected.height}</div>
                  <div className="font-mono">phash: {selected.phash}</div>
                </div>
                {stats && stats.match_count > 0 && (
                  <div className="rounded border border-border p-2 bg-zinc-50 text-[11px] space-y-0.5">
                    <div className="font-semibold">命中率统计 (最近 {stats.sessions_scanned} 个会话)</div>
                    <div>匹配次数: <span className="font-mono">{stats.match_count}</span></div>
                    <div>命中次数: <span className="font-mono">{stats.hit_count}</span></div>
                    <div>命中率: <span className="font-mono">{(stats.hit_rate * 100).toFixed(1)}%</span></div>
                    <div>平均得分: <span className="font-mono">{stats.avg_score.toFixed(3)}</span></div>
                  </div>
                )}
              </div>

              <div className="p-3 space-y-2 border-b border-border">
                <div className="text-xs font-semibold">模版测试 (dryrun, 不实点)</div>
                <div className="flex flex-wrap gap-1">
                  {Object.values(instances).map((inst) => (
                    <Button
                      key={inst.index}
                      variant={testInst === inst.index ? 'secondary' : 'outline'}
                      size="sm"
                      onClick={() => setTestInst(inst.index)}
                    >
                      #{inst.index}
                    </Button>
                  ))}
                  {Object.values(instances).length === 0 && (
                    <div className="text-[11px] text-muted-foreground">
                      没有运行实例 — 启动后可点这里抓帧测
                    </div>
                  )}
                </div>
                <Button
                  size="sm"
                  disabled={testing || Object.values(instances).length === 0}
                  onClick={runTest}
                  className="w-full"
                >
                  {testing ? '测试中…' : '抓一帧测试匹配'}
                </Button>
              </div>

              {testResult && (
                <div className="p-3 flex-1 overflow-y-auto space-y-2">
                  <div className={`text-xs font-semibold ${testResult.hit ? 'text-emerald-700' : 'text-red-700'}`}>
                    {testResult.hit ? '✓ 命中' : '✗ 未命中'}
                    {' · '}得分 <span className="font-mono">{testResult.score.toFixed(3)}</span>
                    {' · '}<span className="font-mono">{testResult.duration_ms.toFixed(1)}ms</span>
                  </div>
                  {testResult.hit && (
                    <div className="text-[11px] font-mono text-muted-foreground">
                      位置 ({testResult.cx}, {testResult.cy}) · {testResult.w}×{testResult.h}
                    </div>
                  )}
                  {testResult.note && (
                    <div className="text-[11px] text-muted-foreground">{testResult.note}</div>
                  )}
                  {testResult.annotated_b64 && (
                    <img
                      src={testResult.annotated_b64}
                      alt="标注"
                      className="w-full rounded border border-border"
                    />
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </div>

      <TemplateCropper
        open={cropperOpen}
        onClose={() => setCropperOpen(false)}
        onSaved={(name) => {
          setCropperOpen(false)
          setRefreshTick((x) => x + 1)
          // 自动选中刚保存的
          setTimeout(() => {
            setSelected({ name } as TemplateMeta)
          }, 200)
        }}
      />
    </div>
  )
}
