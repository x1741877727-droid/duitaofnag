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
import { useEffect, useMemo, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useAppStore } from '@/lib/store'
import {
  fetchTemplateList,
  fetchTemplateStats,
  fetchTemplateDetail,
  templateImgSrc,
  templateOriginalSrc,
  testTemplate,
  deleteTemplate,
  type TemplateMeta,
  type TemplateStats,
  type TemplateDetail,
  type TemplateTestResult,
} from '@/lib/templatesApi'
import { fetchYoloInfo, runYoloTest, type YoloInfo, type YoloTestResult } from '@/lib/yoloApi'
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
  const [detail, setDetail] = useState<TemplateDetail | null>(null)
  const [stats, setStats] = useState<TemplateStats | null>(null)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<TemplateTestResult | null>(null)
  const [testInst, setTestInst] = useState<number | null>(null)
  const [cropperOpen, setCropperOpen] = useState(false)
  const [refreshTick, setRefreshTick] = useState(0)
  const [loading, setLoading] = useState(false)
  // 右侧 tab: detail (模版详情) | yolo (YOLO 测试)
  const [rightTab, setRightTab] = useState<'detail' | 'yolo'>('detail')
  const [yoloInfo, setYoloInfo] = useState<YoloInfo | null>(null)
  const [yoloResult, setYoloResult] = useState<YoloTestResult | null>(null)
  const [yoloRunning, setYoloRunning] = useState(false)
  const [yoloConf, setYoloConf] = useState(0.4)
  const [yoloInst, setYoloInst] = useState<number | null>(null)
  const origImgRef = useRef<HTMLImageElement | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetchTemplateList()
      .then((d) => { if (!cancelled) setItems(d.items) })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [refreshTick])

  // 选中变化 → 拉 stats + detail
  useEffect(() => {
    if (!selected) { setStats(null); setDetail(null); return }
    let cancelled = false
    fetchTemplateStats(selected.name)
      .then((s) => { if (!cancelled) setStats(s) })
      .catch(() => { if (!cancelled) setStats(null) })
    fetchTemplateDetail(selected.name)
      .then((d) => { if (!cancelled) setDetail(d) })
      .catch(() => { if (!cancelled) setDetail(null) })
    return () => { cancelled = true }
  }, [selected])

  // 切到 yolo tab 时拉一次 info
  useEffect(() => {
    if (rightTab !== 'yolo' || yoloInfo) return
    fetchYoloInfo().then(setYoloInfo).catch(() => setYoloInfo(null))
  }, [rightTab, yoloInfo])

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

  async function runYolo() {
    setYoloRunning(true)
    setYoloResult(null)
    try {
      const tgt = yoloInst ?? Object.values(instances)[0]?.index ?? 0
      const r = await runYoloTest({ instance: tgt, conf_thr: yoloConf })
      setYoloResult(r)
    } catch (e) {
      setYoloResult({
        ok: false, source: '', source_image_size: [0, 0],
        conf_thr: yoloConf, duration_ms: 0,
        detections: [], annotated_b64: '', error: String(e),
      })
    } finally {
      setYoloRunning(false)
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

        {/* 右: 详情 + 测试 + YOLO */}
        <div className="rounded-lg border border-border bg-card flex flex-col overflow-hidden">
          {/* tab 切换 */}
          <div className="flex border-b border-border">
            <button
              onClick={() => setRightTab('detail')}
              className={`flex-1 text-xs py-2 ${
                rightTab === 'detail' ? 'bg-blue-50 text-blue-700 font-semibold' : 'text-muted-foreground'
              }`}
            >
              模版详情 / 测试
            </button>
            <button
              onClick={() => setRightTab('yolo')}
              className={`flex-1 text-xs py-2 border-l border-border ${
                rightTab === 'yolo' ? 'bg-blue-50 text-blue-700 font-semibold' : 'text-muted-foreground'
              }`}
            >
              YOLO 测试
            </button>
          </div>

          {rightTab === 'detail' && (
            !selected ? (
              <div className="p-6 text-xs text-muted-foreground text-center">
                选个模版看详情和测试
              </div>
            ) : (
              <div className="flex flex-col overflow-hidden">
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
                  <div className="bg-zinc-100 rounded p-2 flex items-center justify-center max-h-[140px]">
                    <img
                      src={templateImgSrc(selected.name)}
                      alt={selected.name}
                      className="max-w-full max-h-[120px] object-contain"
                    />
                  </div>
                  <div className="text-[11px] text-muted-foreground space-y-0.5">
                    <div>分类: {selected.category}</div>
                    <div>尺寸: {selected.width} × {selected.height}</div>
                    <div className="font-mono">phash: {selected.phash}</div>
                  </div>

                  {/* 原图 + 裁剪框 */}
                  <div>
                    <div className="text-[11px] font-semibold mb-1">原图 + 裁剪位置</div>
                    {detail && detail.has_original ? (
                      <OriginalWithBox
                        url={templateOriginalSrc(detail.name)}
                        bbox={detail.crop_bbox}
                      />
                    ) : (
                      <div className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded p-2">
                        原图丢失（早期模版未保存原图，新上传/裁剪的会保留）
                      </div>
                    )}
                  </div>

                  {stats && stats.match_count > 0 && (
                    <div className="rounded border border-border p-2 bg-zinc-50 text-[11px] space-y-0.5">
                      <div className="font-semibold">命中率统计 (最近 {stats.sessions_scanned} 会话)</div>
                      <div>匹配/命中: <span className="font-mono">{stats.match_count}/{stats.hit_count}</span></div>
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
                        没有实例信息 — 试着先在「运行控制」加载实例
                      </div>
                    )}
                  </div>
                  <Button
                    size="sm"
                    disabled={testing}
                    onClick={runTest}
                    className="w-full"
                  >
                    {testing ? '测试中…' : '抓帧测试匹配 (无需启动 runner)'}
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
              </div>
            )
          )}

          {rightTab === 'yolo' && (
            <div className="p-3 space-y-2 overflow-y-auto">
              <div className="rounded border border-border bg-zinc-50 p-2 text-[11px] space-y-0.5">
                <div className="font-semibold">YOLO 状态</div>
                {!yoloInfo ? (
                  <div className="text-muted-foreground">加载中…</div>
                ) : (
                  <>
                    <div>可用: <span className={yoloInfo.available ? 'text-emerald-700 font-bold' : 'text-red-700 font-bold'}>
                      {yoloInfo.available ? '✓' : '✗'}
                    </span></div>
                    <div className="font-mono break-all text-[10px]">{yoloInfo.model_path || '—'}</div>
                    <div>类别 ({yoloInfo.classes.length}): {yoloInfo.classes.join(', ') || '—'}</div>
                    <div>输入尺寸: {yoloInfo.input_size}</div>
                    {yoloInfo.error && <div className="text-red-700">{yoloInfo.error}</div>}
                  </>
                )}
              </div>
              <div className="space-y-1.5">
                <div className="text-xs font-semibold">选实例 (无需启动 runner)</div>
                <div className="flex flex-wrap gap-1">
                  {Object.values(instances).map((inst) => (
                    <Button
                      key={inst.index}
                      variant={yoloInst === inst.index ? 'secondary' : 'outline'}
                      size="sm"
                      onClick={() => setYoloInst(inst.index)}
                    >
                      #{inst.index}
                    </Button>
                  ))}
                </div>
                <div className="flex items-center gap-2 text-[11px]">
                  <span>置信度阈值:</span>
                  <input
                    type="number" step="0.05" min="0" max="1"
                    value={yoloConf}
                    onChange={(e) => setYoloConf(Number(e.target.value) || 0.4)}
                    className="w-16 h-7 px-1 border border-border rounded text-xs font-mono"
                  />
                </div>
                <Button
                  size="sm"
                  disabled={yoloRunning || !yoloInfo?.available}
                  onClick={runYolo}
                  className="w-full"
                >
                  {yoloRunning ? '推理中…' : '抓帧跑 YOLO'}
                </Button>
              </div>
              {yoloResult && (
                <div className="space-y-2 pt-2 border-t border-border">
                  {!yoloResult.ok ? (
                    <div className="text-[11px] text-red-700">{yoloResult.error || '失败'}</div>
                  ) : (
                    <>
                      <div className="text-xs font-semibold">
                        {yoloResult.detections.length} 个检测 · <span className="font-mono">{yoloResult.duration_ms.toFixed(1)}ms</span>
                      </div>
                      {yoloResult.annotated_b64 && (
                        <img src={yoloResult.annotated_b64} alt="yolo" className="w-full rounded border" />
                      )}
                      {yoloResult.detections.length > 0 && (
                        <ul className="space-y-0.5">
                          {yoloResult.detections.slice(0, 12).map((d, i) => (
                            <li key={i} className="font-mono text-[10px] flex items-center gap-2">
                              <span className="text-blue-600">▣</span>
                              <span className="flex-1">{d.name}</span>
                              <span className="text-muted-foreground">{d.score.toFixed(3)}</span>
                              <span className="text-muted-foreground">@({d.cx},{d.cy})</span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </>
                  )}
                </div>
              )}
            </div>
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


/**
 * 原图 + 裁剪框 overlay.
 * bbox 是原图自然坐标 [x1,y1,x2,y2]; 没有 bbox 就只显示原图.
 */
function OriginalWithBox({ url, bbox }: { url: string; bbox: number[] | null }) {
  const imgRef = useRef<HTMLImageElement | null>(null)
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null)
  const [render, setRender] = useState<{ w: number; h: number } | null>(null)

  useEffect(() => {
    setNatural(null)
    setRender(null)
  }, [url])

  function recomputeRender() {
    const img = imgRef.current
    if (!img) return
    const r = img.getBoundingClientRect()
    setRender({ w: r.width, h: r.height })
  }

  useEffect(() => {
    if (!natural) return
    const onResize = () => recomputeRender()
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [natural])

  let boxStyle: React.CSSProperties | null = null
  if (bbox && bbox.length === 4 && natural && render) {
    const sx = render.w / natural.w
    const sy = render.h / natural.h
    boxStyle = {
      position: 'absolute',
      left: bbox[0] * sx,
      top: bbox[1] * sy,
      width: (bbox[2] - bbox[0]) * sx,
      height: (bbox[3] - bbox[1]) * sy,
      border: '2px solid #2563eb',
      background: 'rgba(37, 99, 235, 0.15)',
      pointerEvents: 'none',
    }
  }

  return (
    <div className="relative bg-zinc-900 rounded overflow-hidden">
      <img
        ref={imgRef}
        src={url}
        alt="原图"
        className="block w-full max-h-[260px] object-contain"
        onLoad={(e) => {
          const img = e.currentTarget
          setNatural({ w: img.naturalWidth, h: img.naturalHeight })
          recomputeRender()
        }}
      />
      {boxStyle && <div style={boxStyle} />}
      {bbox && (
        <div className="absolute bottom-1 right-1 px-1.5 py-0.5 rounded bg-blue-500 text-white text-[10px] font-mono">
          {bbox[0]},{bbox[1]} → {bbox[2]},{bbox[3]} ({bbox[2] - bbox[0]}×{bbox[3] - bbox[1]})
        </div>
      )}
    </div>
  )
}
