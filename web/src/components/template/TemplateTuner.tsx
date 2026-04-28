/**
 * 模版调试 / Preprocessing 校准页.
 *
 * 三栏:
 *   左: 模版列表 (按分类分组), 已配 preprocessing 的标 ⚙
 *   中: 上 = 模版原图 + 应用 preprocessing 后的预览 (并排, 让你看 edge/clahe 效果)
 *       下 = 抓帧 + 匹配结果 (命中 bbox / MISS)
 *   右: 6 个 preprocessing checkbox + 阈值 slider + 抓帧来源 + 测试 + 保存
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { useAppStore } from '@/lib/store'
import {
  fetchTemplateList, saveTemplateMeta, testTemplate, fetchTemplatePreview,
  type TemplateMeta, type Preprocessing, ALL_PREPROC,
  type TemplateTestResult,
} from '@/lib/templatesApi'

const PREPROC_LABEL: Record<Preprocessing, { label: string; hint: string }> = {
  grayscale: { label: '灰度', hint: '只保留亮度信息' },
  clahe: { label: '增强对比', hint: 'CLAHE; 抗光照' },
  binarize: { label: '二值化', hint: 'Otsu 黑白' },
  sharpen: { label: '锐化', hint: '抗模糊' },
  invert: { label: '反相', hint: '颜色翻转' },
  edge: { label: '边缘 (Canny)', hint: '抗动画/光照, 模板首选' },
}

type FrameSrc = { kind: 'instance'; idx: number } | null

export function TemplateTuner() {
  const emulators = useAppStore((s) => s.emulators)

  const [items, setItems] = useState<TemplateMeta[]>([])
  const [selectedName, setSelectedName] = useState<string>('')
  const [filter, setFilter] = useState('')

  // 编辑中字段 (不立即保存)
  const [editPreproc, setEditPreproc] = useState<Preprocessing[]>([])
  const [editThreshold, setEditThreshold] = useState<number>(0)  // 0 = 不动 (用默认 0.80)

  // 模版预览 (origin / processed) base64
  const [previewOrig, setPreviewOrig] = useState('')
  const [previewProc, setPreviewProc] = useState('')

  // 抓帧 + 匹配结果
  const [src, setSrc] = useState<FrameSrc>(null)
  const [matchResult, setMatchResult] = useState<TemplateTestResult | null>(null)

  const [busy, setBusy] = useState(false)
  const [hint, setHint] = useState('')

  const availInsts = useMemo(() => emulators.filter((e) => e.running), [emulators])
  useEffect(() => {
    if (!src && availInsts.length > 0) {
      setSrc({ kind: 'instance', idx: availInsts[0].index })
    }
  }, [availInsts])

  async function loadList() {
    try {
      const r = await fetchTemplateList()
      setItems(r.items)
      if (!selectedName && r.items.length > 0) {
        selectTemplate(r.items[0])
      }
    } catch (e: any) {
      setHint(`加载模版列表失败: ${e?.message || e}`)
    }
  }
  useEffect(() => { loadList() }, [])

  function selectTemplate(it: TemplateMeta) {
    setSelectedName(it.name)
    setEditPreproc([...it.preprocessing])
    setEditThreshold(it.threshold || 0)
    setMatchResult(null)
  }

  // preprocessing 改变时刷新模版预览
  const previewSeq = useRef(0)
  useEffect(() => {
    if (!selectedName) return
    const my = ++previewSeq.current
    fetchTemplatePreview(selectedName, editPreproc).then((r) => {
      if (my !== previewSeq.current) return
      setPreviewOrig(r.original_b64)
      setPreviewProc(r.processed_b64)
    }).catch(() => {})
  }, [selectedName, editPreproc])

  function togglePreproc(m: Preprocessing) {
    setEditPreproc((cur) => cur.includes(m) ? cur.filter((x) => x !== m) : [...cur, m])
  }

  async function runMatch() {
    if (!selectedName) return
    if (!src || src.kind !== 'instance') {
      setHint('请先选实例 (没有运行中的实例)')
      return
    }
    setBusy(true); setHint('抓帧 + 匹配中...')
    try {
      const r = await testTemplate({
        name: selectedName,
        instance: src.idx,
        threshold: editThreshold > 0 ? editThreshold : undefined,
        preprocessing: editPreproc,
      })
      setMatchResult(r)
      setHint(r.hit
        ? `命中 conf=${r.score.toFixed(3)} @ (${r.cx},${r.cy}), 耗时 ${r.duration_ms.toFixed(1)}ms`
        : `MISS (${r.note}); 耗时 ${r.duration_ms.toFixed(1)}ms`)
    } catch (e: any) {
      setHint(`匹配出错: ${e?.message || e}`)
    } finally { setBusy(false) }
  }

  async function save() {
    if (!selectedName) return
    setBusy(true); setHint('保存中...')
    try {
      await saveTemplateMeta({
        name: selectedName,
        preprocessing: editPreproc,
        threshold: editThreshold > 0 ? editThreshold : undefined,
      })
      setHint(`已保存 ${selectedName}.yaml`)
      // 刷新列表 + 选中项
      const r = await fetchTemplateList()
      setItems(r.items)
      const found = r.items.find((x) => x.name === selectedName)
      if (found) {
        // 不重置 edit, 让用户继续微调
      }
    } catch (e: any) {
      setHint(`保存失败: ${e?.message || e}`)
    } finally { setBusy(false) }
  }

  // 按分类聚合左侧列表
  const grouped = useMemo(() => {
    const filt = filter.trim().toLowerCase()
    const g: Record<string, TemplateMeta[]> = {}
    for (const it of items) {
      if (filt && !it.name.toLowerCase().includes(filt)) continue
      ;(g[it.category] ||= []).push(it)
    }
    return Object.entries(g).sort((a, b) => a[0].localeCompare(b[0]))
  }, [items, filter])

  const cur = items.find((x) => x.name === selectedName)
  const dirty = cur && (
    JSON.stringify([...cur.preprocessing].sort()) !== JSON.stringify([...editPreproc].sort())
    || (cur.threshold || 0) !== editThreshold
  )

  return (
    <div className="grid h-full" style={{ gridTemplateColumns: '260px 1fr 320px' }}>
      {/* ─── 左: 模版列表 ─── */}
      <div className="border-r border-border overflow-auto">
        <div className="sticky top-0 z-10 bg-background border-b border-border p-2 space-y-2">
          <input
            type="text"
            placeholder="过滤模版名..."
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="w-full text-xs px-2 py-1 border border-border rounded bg-background"
          />
          <div className="text-[11px] text-muted-foreground">
            共 {items.length} 个模版 · ⚙ = 已配 preprocessing
          </div>
        </div>
        <div className="p-1">
          {grouped.map(([cat, list]) => (
            <div key={cat} className="mb-2">
              <div className="text-[10px] uppercase text-muted-foreground px-2 py-1 font-semibold">
                {cat} ({list.length})
              </div>
              {list.map((it) => {
                const active = it.name === selectedName
                const hasMeta = it.preprocessing.length > 0 || it.threshold > 0
                return (
                  <button
                    key={it.name}
                    onClick={() => selectTemplate(it)}
                    className={`w-full text-left px-2 py-1.5 rounded text-xs flex items-center justify-between ${
                      active ? 'bg-primary/15 text-foreground border border-primary/30' : 'hover:bg-muted'
                    }`}
                  >
                    <span className="truncate font-mono">{it.name}</span>
                    <span className="ml-2 shrink-0 text-[10px] text-muted-foreground">
                      {hasMeta && <span title="已配 preprocessing/threshold">⚙</span>}
                      {' '}{it.width}×{it.height}
                    </span>
                  </button>
                )
              })}
            </div>
          ))}
        </div>
      </div>

      {/* ─── 中: 模版预览 + 匹配结果 ─── */}
      <div className="overflow-auto p-3 space-y-3">
        <div>
          <div className="text-xs font-semibold mb-1">模版预览</div>
          <div className="grid gap-2" style={{ gridTemplateColumns: '1fr 1fr' }}>
            <div className="border border-border rounded p-2">
              <div className="text-[11px] text-muted-foreground mb-1">原图</div>
              {previewOrig
                ? <img src={previewOrig} alt="" className="max-w-full bg-checkered" style={{ imageRendering: 'pixelated' }} />
                : <div className="text-xs text-muted-foreground">—</div>}
            </div>
            <div className="border border-border rounded p-2">
              <div className="text-[11px] text-muted-foreground mb-1">
                应用后 ({editPreproc.join(' + ') || '无'})
              </div>
              {previewProc
                ? <img src={previewProc} alt="" className="max-w-full bg-checkered" style={{ imageRendering: 'pixelated' }} />
                : <div className="text-xs text-muted-foreground">—</div>}
            </div>
          </div>
        </div>

        <div>
          <div className="flex items-center justify-between mb-1">
            <div className="text-xs font-semibold">匹配结果</div>
            {matchResult && (
              <div className={`text-[11px] font-mono ${matchResult.hit ? 'text-success' : 'text-destructive'}`}>
                {matchResult.hit
                  ? `HIT  ${matchResult.score.toFixed(3)} @ (${matchResult.cx},${matchResult.cy})`
                  : 'MISS'}
                {' '}· {matchResult.duration_ms.toFixed(0)}ms
              </div>
            )}
          </div>
          <div className="border border-border rounded p-2 min-h-[200px] flex items-center justify-center bg-muted/30">
            {matchResult?.annotated_b64
              ? <img src={matchResult.annotated_b64} alt="" className="max-w-full" />
              : <div className="text-xs text-muted-foreground">点右侧"抓帧测试"</div>}
          </div>
          {matchResult?.note && (
            <div className="text-[11px] text-muted-foreground mt-1">{matchResult.note}</div>
          )}
        </div>
      </div>

      {/* ─── 右: preprocessing + threshold + 操作 ─── */}
      <div className="border-l border-border overflow-auto p-3 space-y-3">
        <div className="text-xs font-semibold">
          {selectedName || '未选模版'}
          {dirty && <span className="ml-2 text-warning">●未保存</span>}
        </div>

        {/* 来源 */}
        <div>
          <div className="text-[11px] font-semibold mb-1">抓帧来源</div>
          <div className="flex flex-wrap gap-1">
            {availInsts.length === 0 && (
              <div className="text-[11px] text-muted-foreground">没有运行中的实例</div>
            )}
            {availInsts.map((e) => (
              <button
                key={e.index}
                onClick={() => setSrc({ kind: 'instance', idx: e.index })}
                className={`text-[11px] px-2 py-1 rounded border ${
                  src?.kind === 'instance' && src.idx === e.index
                    ? 'border-primary bg-primary/15 text-foreground'
                    : 'border-border hover:bg-muted'
                }`}
              >
                #{e.index}
              </button>
            ))}
          </div>
        </div>

        {/* preprocessing 6 选 */}
        <div>
          <div className="text-[11px] font-semibold mb-1">Preprocessing (顺序应用)</div>
          <div className="space-y-1">
            {ALL_PREPROC.map((m) => {
              const idx = editPreproc.indexOf(m)
              const checked = idx >= 0
              return (
                <label
                  key={m}
                  className={`flex items-start gap-2 px-2 py-1 rounded text-[11px] cursor-pointer ${
                    checked ? 'bg-primary/10' : 'hover:bg-muted'
                  }`}
                >
                  <input type="checkbox" checked={checked} onChange={() => togglePreproc(m)} className="mt-0.5" />
                  <div className="flex-1">
                    <div className="font-medium">
                      {checked && <span className="text-primary mr-1">{idx + 1}.</span>}
                      {PREPROC_LABEL[m].label}
                    </div>
                    <div className="text-muted-foreground text-[10px]">
                      {PREPROC_LABEL[m].hint}
                    </div>
                  </div>
                </label>
              )
            })}
          </div>
        </div>

        {/* threshold */}
        <div>
          <div className="text-[11px] font-semibold mb-1 flex items-center justify-between">
            <span>阈值</span>
            <span className="font-mono text-[11px] text-muted-foreground">
              {editThreshold > 0 ? editThreshold.toFixed(2) : '默认 (0.80)'}
            </span>
          </div>
          <input
            type="range" min="0" max="0.95" step="0.05"
            value={editThreshold}
            onChange={(e) => setEditThreshold(Number(e.target.value))}
            className="w-full"
          />
          <div className="text-[10px] text-muted-foreground mt-0.5">
            0 = 用 ScreenMatcher 默认 (0.80). 调低 = 更宽松 (容易误匹配).
          </div>
        </div>

        {/* 操作 */}
        <div className="space-y-1.5 pt-2 border-t border-border">
          <Button onClick={runMatch} disabled={busy || !selectedName || !src} className="w-full" size="sm">
            {busy ? '...' : '抓帧 + 测试匹配'}
          </Button>
          <Button onClick={save} disabled={busy || !selectedName || !dirty} variant="outline" className="w-full" size="sm">
            保存到 yaml
          </Button>
          {cur && (
            <Button
              onClick={() => { setEditPreproc([...cur.preprocessing]); setEditThreshold(cur.threshold || 0) }}
              disabled={!dirty}
              variant="ghost" className="w-full text-[11px]" size="sm"
            >
              取消未保存修改
            </Button>
          )}
        </div>

        {hint && (
          <div className="text-[11px] p-2 rounded bg-muted text-muted-foreground break-words">
            {hint}
          </div>
        )}
      </div>
    </div>
  )
}
