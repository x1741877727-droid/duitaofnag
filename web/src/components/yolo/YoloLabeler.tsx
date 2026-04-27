/**
 * YOLO 标注 tab — 单图标注.
 *
 * 左侧: 图片列表 (按已标/未标过滤, 点切换)
 * 中: 画布 (img + 鼠标拖框 overlay) — 拖出新框默认用当前选中的 class_id
 * 右: 当前类按钮 + 当前框列表 (可删) + 保存 / 跳过 (空标记)
 *
 * YOLO label 格式: 0-1 normalized (cx cy w h).
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import {
  fetchDataset, datasetImgSrc, fetchLabels, saveLabels, type DatasetList,
  type LabelBox,
} from '@/lib/yoloApi'

const CLASS_COLORS = ['#ef4444', '#22c55e', '#a855f7', '#f59e0b']

export function YoloLabeler({
  initialName, onChangeName,
}: {
  initialName?: string
  onChangeName?: (n: string) => void
}) {
  const [data, setData] = useState<DatasetList | null>(null)
  const [classes, setClasses] = useState<string[]>([])
  const [filter, setFilter] = useState<'all' | 'remaining' | 'labeled'>('remaining')
  const [name, setName] = useState<string>(initialName || '')
  const [boxes, setBoxes] = useState<LabelBox[]>([])
  const [activeClass, setActiveClass] = useState<number>(0)
  const [naturalSize, setNaturalSize] = useState<{ w: number; h: number } | null>(null)
  const [saving, setSaving] = useState(false)
  const [drag, setDrag] = useState<{ startNx: number; startNy: number; box: LabelBox } | null>(null)

  const imgRef = useRef<HTMLImageElement | null>(null)
  const wrapRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    fetchDataset().then((d) => {
      setData(d)
      setClasses(d.classes)
      if (!name && d.items.length > 0) {
        // 默认选第一张未标的
        const first = d.items.find((x) => !x.labeled && !x.skipped) || d.items[0]
        setName(first.name)
        onChangeName?.(first.name)
      }
    }).catch(() => {})
  }, [])

  useEffect(() => {
    if (!name) return
    setBoxes([])
    fetchLabels(name).then((r) => setBoxes(r.boxes)).catch(() => {})
  }, [name])

  const fileList = useMemo(() => {
    if (!data) return []
    let list = data.items
    if (filter === 'remaining') list = list.filter((x) => !x.labeled && !x.skipped)
    else if (filter === 'labeled') list = list.filter((x) => x.labeled)
    return list
  }, [data, filter])

  // ─── 鼠标交互 (画框) ───
  function clientToNorm(clientX: number, clientY: number): { nx: number; ny: number } | null {
    const img = imgRef.current
    if (!img) return null
    const r = img.getBoundingClientRect()
    const nx = (clientX - r.left) / r.width
    const ny = (clientY - r.top) / r.height
    return {
      nx: Math.max(0, Math.min(1, nx)),
      ny: Math.max(0, Math.min(1, ny)),
    }
  }

  function onMouseDown(e: React.MouseEvent) {
    if (!naturalSize) return
    const p = clientToNorm(e.clientX, e.clientY)
    if (!p) return
    const newBox: LabelBox = {
      class_id: activeClass,
      cx: p.nx, cy: p.ny, w: 0, h: 0,
    }
    setDrag({ startNx: p.nx, startNy: p.ny, box: newBox })
  }
  function onMouseMove(e: React.MouseEvent) {
    if (!drag) return
    const p = clientToNorm(e.clientX, e.clientY)
    if (!p) return
    const x1 = Math.min(drag.startNx, p.nx)
    const y1 = Math.min(drag.startNy, p.ny)
    const x2 = Math.max(drag.startNx, p.nx)
    const y2 = Math.max(drag.startNy, p.ny)
    setDrag({
      ...drag,
      box: {
        class_id: activeClass,
        cx: (x1 + x2) / 2,
        cy: (y1 + y2) / 2,
        w: x2 - x1,
        h: y2 - y1,
      },
    })
  }
  function onMouseUp() {
    if (drag && drag.box.w > 0.005 && drag.box.h > 0.005) {
      setBoxes((bs) => [...bs, drag.box])
    }
    setDrag(null)
  }

  function removeBox(idx: number) {
    setBoxes((bs) => bs.filter((_, i) => i !== idx))
  }

  async function save() {
    setSaving(true)
    try {
      await saveLabels(name, boxes)
      // 刷新文件列表
      const d = await fetchDataset()
      setData(d)
    } finally {
      setSaving(false)
    }
  }

  async function skip() {
    setBoxes([])
    setSaving(true)
    try {
      await saveLabels(name, [])
      const d = await fetchDataset()
      setData(d)
      // 自动跳下一张未标
      if (d) {
        const next = d.items.find(
          (x) => x.name !== name && !x.labeled && !x.skipped,
        )
        if (next) {
          setName(next.name)
          onChangeName?.(next.name)
        }
      }
    } finally {
      setSaving(false)
    }
  }

  function nextImg() {
    if (!data) return
    const next = data.items.find(
      (x) => x.name !== name && !x.labeled && !x.skipped,
    )
    if (next) {
      setName(next.name)
      onChangeName?.(next.name)
    }
  }

  // 显示用 (图片渲染坐标 → 框 px)
  function renderBoxStyle(b: LabelBox): React.CSSProperties | null {
    const img = imgRef.current
    if (!img) return null
    const r = img.getBoundingClientRect()
    const wrap = wrapRef.current?.getBoundingClientRect()
    if (!wrap) return null
    const left = (b.cx - b.w / 2) * r.width + (r.left - wrap.left)
    const top = (b.cy - b.h / 2) * r.height + (r.top - wrap.top)
    const wpx = b.w * r.width
    const hpx = b.h * r.height
    return {
      position: 'absolute', left, top, width: wpx, height: hpx,
      border: `2px solid ${CLASS_COLORS[b.class_id % CLASS_COLORS.length]}`,
      background: `${CLASS_COLORS[b.class_id % CLASS_COLORS.length]}22`,
      pointerEvents: 'none',
    }
  }

  return (
    <div className="grid gap-3 h-full min-h-0" style={{ gridTemplateColumns: '200px 1fr 240px' }}>
      {/* 左: 文件列表 */}
      <div className="rounded-lg border border-border bg-card overflow-y-auto">
        <div className="sticky top-0 bg-card z-10 p-2 border-b border-border space-y-1">
          <div className="text-xs font-semibold">{fileList.length} / {data?.items.length ?? 0} 图</div>
          <div className="flex gap-1">
            {(['remaining', 'labeled', 'all'] as const).map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`flex-1 text-[10px] px-1 py-0.5 rounded border ${
                  filter === f ? 'bg-blue-50 border-blue-300 text-blue-700' : 'border-border'
                }`}
              >
                {({ remaining: '未标', labeled: '已标', all: '全部' } as const)[f]}
              </button>
            ))}
          </div>
        </div>
        <ul className="divide-y divide-border text-[11px]">
          {fileList.map((it) => (
            <li key={it.name}>
              <button
                onClick={() => { setName(it.name); onChangeName?.(it.name) }}
                className={`w-full text-left px-2 py-1.5 ${
                  name === it.name ? 'bg-blue-50 text-blue-700 font-semibold' : ''
                }`}
              >
                <div className="flex items-center gap-2">
                  <span className={`inline-block w-1.5 h-1.5 rounded-full ${
                    it.labeled ? 'bg-emerald-500' : it.skipped ? 'bg-amber-400' : 'bg-zinc-300'
                  }`} />
                  <span className="truncate flex-1 font-mono">{it.name}</span>
                </div>
              </button>
            </li>
          ))}
        </ul>
      </div>

      {/* 中: 画布 */}
      <div
        ref={wrapRef}
        className="relative bg-zinc-900 rounded-lg overflow-hidden flex items-center justify-center"
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseUp}
        style={{ cursor: name ? 'crosshair' : 'default' }}
      >
        {!name ? (
          <div className="text-sm text-zinc-400">从左侧选一张图开始标注</div>
        ) : (
          <img
            ref={imgRef}
            src={datasetImgSrc(name)}
            alt={name}
            draggable={false}
            className="max-w-full max-h-full object-contain"
            onLoad={(e) => {
              const img = e.currentTarget
              setNaturalSize({ w: img.naturalWidth, h: img.naturalHeight })
            }}
          />
        )}
        {boxes.map((b, i) => {
          const style = renderBoxStyle(b)
          if (!style) return null
          return (
            <div key={i} style={style}>
              <div
                className="absolute -top-5 left-0 px-1 text-[10px] text-white rounded"
                style={{ background: CLASS_COLORS[b.class_id % CLASS_COLORS.length] }}
              >
                {classes[b.class_id] || `class_${b.class_id}`}
              </div>
            </div>
          )
        })}
        {drag && (() => {
          const style = renderBoxStyle(drag.box)
          if (!style) return null
          return <div style={{ ...style, borderStyle: 'dashed' }} />
        })()}
      </div>

      {/* 右: 控制 */}
      <div className="rounded-lg border border-border bg-card p-3 space-y-3 overflow-y-auto">
        <div>
          <div className="text-xs font-semibold mb-1">类别 (画框时用此类)</div>
          {classes.map((c, idx) => (
            <button
              key={c}
              onClick={() => setActiveClass(idx)}
              className={`block w-full text-left mb-1 px-2 py-1.5 rounded border-2 text-xs ${
                activeClass === idx ? 'font-bold' : 'opacity-70'
              }`}
              style={{
                borderColor: CLASS_COLORS[idx % CLASS_COLORS.length],
                background: activeClass === idx ? `${CLASS_COLORS[idx % CLASS_COLORS.length]}22` : 'transparent',
              }}
            >
              <span className="font-mono">[{idx}] {c}</span>
            </button>
          ))}
          <div className="text-[10px] text-muted-foreground mt-1">
            数字键 1-{classes.length} 切换类别
          </div>
        </div>

        <div>
          <div className="text-xs font-semibold mb-1">已画框 ({boxes.length})</div>
          <ul className="space-y-1 max-h-[200px] overflow-y-auto">
            {boxes.map((b, i) => (
              <li key={i} className="flex items-center gap-2 text-[11px] font-mono">
                <span
                  className="inline-block w-2 h-2 rounded"
                  style={{ background: CLASS_COLORS[b.class_id % CLASS_COLORS.length] }}
                />
                <span className="flex-1">
                  {classes[b.class_id] || `c${b.class_id}`}
                </span>
                <span className="text-muted-foreground">
                  {(b.w * 100).toFixed(0)}%×{(b.h * 100).toFixed(0)}%
                </span>
                <button
                  onClick={() => removeBox(i)}
                  className="text-red-600 text-[10px]"
                >
                  ✕
                </button>
              </li>
            ))}
            {boxes.length === 0 && (
              <li className="text-[11px] text-muted-foreground">还没画框</li>
            )}
          </ul>
        </div>

        <div className="space-y-1">
          <Button
            size="sm"
            disabled={!name || saving}
            onClick={save}
            className="w-full"
          >
            {saving ? '保存中…' : `保存 (${boxes.length} 框)`}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={!name || saving}
            onClick={skip}
            className="w-full"
          >
            标为「跳过 / 背景」
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={!name}
            onClick={nextImg}
            className="w-full"
          >
            下一张未标 →
          </Button>
        </div>

        <div className="text-[10px] text-muted-foreground">
          提示：左键拖框画框；标注完点保存；不需要 / 没目标 → 标「跳过」 (背景图，对训练有用)
        </div>
      </div>
    </div>
  )
}
