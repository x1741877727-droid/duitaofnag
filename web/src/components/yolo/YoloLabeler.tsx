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
  fetchDataset, datasetImgSrc, fetchLabels, saveLabels, preannotateLabels,
  fetchYoloInfo,
  type DatasetList, type LabelBox, type YoloInfo,
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
  const [aiBusy, setAiBusy] = useState(false)
  const [aiHint, setAiHint] = useState<string>('')
  const [yoloInfo, setYoloInfo] = useState<YoloInfo | null>(null)
  const [drag, setDrag] = useState<{ startNx: number; startNy: number; box: LabelBox } | null>(null)

  // 缩放 / 平移 (transform 套在 <img> 上, 不影响 normalized 坐标计算)
  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })   // 平移 px (transform translate)
  const [pannning, setPanning] = useState<{ startClientX: number; startClientY: number; startPanX: number; startPanY: number } | null>(null)

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
    // 拿模型状态: 决定 AI 预标注按钮可不可用 / 哪些类已训
    fetchYoloInfo().then(setYoloInfo).catch(() => setYoloInfo(null))
  }, [])

  // 模型已训的类名集合 (用来判断当前 activeClass 是否能被 AI 预标)
  const trainedClassSet = useMemo(
    () => new Set((yoloInfo?.classes || []).map((c) => c.toLowerCase())),
    [yoloInfo],
  )
  const modelOk = !!yoloInfo?.available
  const currentClassTrained = classes[activeClass]
    ? trainedClassSet.has(classes[activeClass].toLowerCase())
    : false

  useEffect(() => {
    if (!name) return
    setBoxes([])
    // 切图时复位缩放/平移
    setZoom(1)
    setPan({ x: 0, y: 0 })
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
    // 中键 (button=1) 或 Shift+左键 → 平移
    if (e.button === 1 || (e.button === 0 && e.shiftKey)) {
      e.preventDefault()
      setPanning({
        startClientX: e.clientX,
        startClientY: e.clientY,
        startPanX: pan.x,
        startPanY: pan.y,
      })
      return
    }
    if (e.button !== 0) return
    const p = clientToNorm(e.clientX, e.clientY)
    if (!p) return
    const newBox: LabelBox = {
      class_id: activeClass,
      cx: p.nx, cy: p.ny, w: 0, h: 0,
    }
    setDrag({ startNx: p.nx, startNy: p.ny, box: newBox })
  }
  function onMouseMove(e: React.MouseEvent) {
    if (pannning) {
      setPan({
        x: pannning.startPanX + (e.clientX - pannning.startClientX),
        y: pannning.startPanY + (e.clientY - pannning.startClientY),
      })
      return
    }
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
    if (pannning) {
      setPanning(null)
      return
    }
    if (drag && drag.box.w > 0.005 && drag.box.h > 0.005) {
      setBoxes((bs) => [...bs, drag.box])
    }
    setDrag(null)
  }
  function onWheel(e: React.WheelEvent) {
    if (!naturalSize) return
    // 滚轮 / 中键滚动 → 缩放. 不阻塞页面滚动除非真在画布上.
    e.preventDefault()
    const delta = e.deltaY < 0 ? 1.15 : 1 / 1.15
    const newZoom = Math.max(0.3, Math.min(8, zoom * delta))
    // 以鼠标光标为缩放中心: 调整 pan 让光标下的图位置不变
    const wrap = wrapRef.current?.getBoundingClientRect()
    if (wrap) {
      const mx = e.clientX - wrap.left
      const my = e.clientY - wrap.top
      const cx = wrap.width / 2 + pan.x
      const cy = wrap.height / 2 + pan.y
      const dx = mx - cx
      const dy = my - cy
      const ratio = newZoom / zoom
      setPan({
        x: pan.x - dx * (ratio - 1),
        y: pan.y - dy * (ratio - 1),
      })
    }
    setZoom(newZoom)
  }
  function resetZoom() {
    setZoom(1)
    setPan({ x: 0, y: 0 })
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
    // 优先在当前过滤列表里找下一张; 找不到再 fallback 到全局未标
    const list = fileList
    const i = list.findIndex((x) => x.name === name)
    if (i >= 0 && i < list.length - 1) {
      const nxt = list[i + 1]
      setName(nxt.name)
      onChangeName?.(nxt.name)
      return
    }
    const next = data.items.find(
      (x) => x.name !== name && !x.labeled && !x.skipped,
    )
    if (next) {
      setName(next.name)
      onChangeName?.(next.name)
    }
  }

  function prevImg() {
    if (!data) return
    const list = fileList
    const i = list.findIndex((x) => x.name === name)
    if (i > 0) {
      const prev = list[i - 1]
      setName(prev.name)
      onChangeName?.(prev.name)
    }
  }

  // ─── AI 预标注: 调用 latest.onnx 推理, 把结果加到 boxes 里, 用户修补即可 ───
  async function aiPreannotate() {
    if (!name || aiBusy) return
    setAiBusy(true)
    setAiHint('')
    try {
      const r = await preannotateLabels(name)
      if (r.count === 0) {
        setAiHint('AI 没检到任何目标 (新类需先手标种子集)')
      } else {
        // 合并: 已有手标的不动, 把 AI 预测的加进去
        setBoxes((bs) => [
          ...bs,
          ...r.boxes.map((b) => ({
            class_id: b.class_id, cx: b.cx, cy: b.cy, w: b.w, h: b.h,
          })),
        ])
        setAiHint(`AI 加了 ${r.count} 框 (${r.duration_ms}ms), 检查并修补`)
      }
    } catch (e: any) {
      setAiHint(`AI 失败: ${(e?.message || e).toString().slice(0, 80)}`)
    } finally {
      setAiBusy(false)
    }
  }

  // ─── 键盘快捷键 ───
  // S: 保存 / D|→|Space: 下一张 / A|←: 上一张 / B: 标负样本
  // 1-9: 切类别 / Backspace|Delete: 删最后一个框 / E: AI 预标注 / Esc: 取消画框
  useEffect(() => {
    function isTypingTarget(t: EventTarget | null): boolean {
      if (!t || !(t instanceof HTMLElement)) return false
      const tag = t.tagName
      return tag === 'INPUT' || tag === 'TEXTAREA' || t.isContentEditable
    }
    function onKey(e: KeyboardEvent) {
      if (isTypingTarget(e.target)) return
      // Ctrl+S 保存
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        e.preventDefault()
        if (name && !saving) save()
        return
      }
      // 单键 (无修饰)
      if (e.ctrlKey || e.metaKey || e.altKey) return
      const k = e.key.toLowerCase()
      // 数字 1-9 切类
      if (/^[1-9]$/.test(e.key)) {
        const idx = parseInt(e.key, 10) - 1
        if (idx < classes.length) {
          setActiveClass(idx)
          e.preventDefault()
        }
        return
      }
      switch (k) {
        case 's':
          if (name && !saving) save()
          e.preventDefault()
          break
        case 'd':
        case 'arrowright':
        case ' ':
          if (name) nextImg()
          e.preventDefault()
          break
        case 'a':
        case 'arrowleft':
          if (name) prevImg()
          e.preventDefault()
          break
        case 'b':
          if (name && !saving) skip()
          e.preventDefault()
          break
        case 'e':
          if (name && !aiBusy) aiPreannotate()
          e.preventDefault()
          break
        case 'backspace':
        case 'delete':
          if (boxes.length > 0) {
            setBoxes((bs) => bs.slice(0, -1))
            e.preventDefault()
          }
          break
        case 'escape':
          if (drag) {
            setDrag(null)
            e.preventDefault()
          }
          break
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // 依赖项故意省略 nextImg/prevImg/save/skip/aiPreannotate (函数每次渲染都新, 但闭包里读 state 都是最新的, useEffect 只挂一次也行) —
    // 但为安全起见, 把所有 state 列出来让 effect 重挂, 闭包内函数引用始终最新
  }, [name, saving, aiBusy, boxes, drag, classes.length, fileList, data])

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
                  filter === f ? 'bg-info/10 border-info/30 text-info' : 'border-border'
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
                  name === it.name ? 'bg-info/10 text-info font-semibold' : ''
                }`}
              >
                <div className="flex items-center gap-2">
                  <span className={`inline-block w-1.5 h-1.5 rounded-full ${
                    it.labeled ? 'bg-success' : it.skipped ? 'bg-warning' : 'bg-muted-foreground/30'
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
        className="relative bg-foreground rounded-lg overflow-hidden flex items-center justify-center"
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseUp}
        onWheel={onWheel}
        onContextMenu={(e) => e.preventDefault()}
        style={{ cursor: pannning ? 'grabbing' : (name ? 'crosshair' : 'default') }}
      >
        {!name ? (
          <div className="text-sm text-muted-foreground">从左侧选一张图开始标注</div>
        ) : (
          <img
            ref={imgRef}
            src={datasetImgSrc(name)}
            alt={name}
            draggable={false}
            className="max-w-full max-h-full object-contain"
            style={{
              transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
              transformOrigin: 'center center',
              transition: pannning ? 'none' : 'transform 0.05s',
            }}
            onLoad={(e) => {
              const img = e.currentTarget
              setNaturalSize({ w: img.naturalWidth, h: img.naturalHeight })
            }}
          />
        )}
        {/* 缩放指示 + 重置 */}
        {name && (
          <div className="absolute top-2 left-2 flex items-center gap-2 bg-black/60 text-white text-[11px] px-2 py-1 rounded">
            <span className="font-mono">{Math.round(zoom * 100)}%</span>
            <button
              onClick={resetZoom}
              className="text-info hover:text-info"
              title="重置 (1:1)"
            >
              ⟲
            </button>
          </div>
        )}
        {name && (
          <div className="absolute bottom-2 left-2 bg-black/60 text-white/80 text-[10px] px-2 py-1 rounded font-mono">
            滚轮 缩放 · 中键拖 / Shift+左键拖 平移 · 左键 画框
          </div>
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
          {classes.map((c, idx) => {
            const trained = trainedClassSet.has(c.toLowerCase())
            return (
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
                title={trained
                  ? '当前模型已训过此类, 可被 AI 预标'
                  : '当前模型未训过此类 — 需先手标 50+ 张并训 baseline, AI 预标才会出此类的框'
                }
              >
                <div className="flex items-center gap-1.5">
                  <span className="font-mono flex-1 truncate">[{idx}] {c}</span>
                  {!trained && yoloInfo && (
                    <span className="text-[9px] px-1 py-0.5 rounded bg-muted-foreground/20 text-muted-foreground font-mono">
                      未训
                    </span>
                  )}
                </div>
              </button>
            )
          })}
          <div className="text-[10px] text-muted-foreground mt-1">
            数字键 1-{Math.min(9, classes.length)} 切换类别
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
                  className="text-destructive text-[10px]"
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
            disabled={!name || aiBusy || !modelOk}
            onClick={aiPreannotate}
            variant="secondary"
            className="w-full"
            title={
              !modelOk
                ? 'YOLO 模型未加载 (latest.onnx 不存在). 上传训好的模型才能用 AI 预标注'
                : !currentClassTrained
                  ? `当前类 "${classes[activeClass] || ''}" 模型还没训过 — AI 预标只会出已训类 (${yoloInfo?.classes.join('/')}) 的框, 当前类要先手标 50+ 张并重训`
                  : '用 latest.onnx 推理预填框, 你只需修补 (E)'
            }
          >
            {aiBusy ? 'AI 推理中…' : (
              !modelOk ? 'AI 预标注 (模型未上传)' : 'AI 预标注 (E)'
            )}
          </Button>
          {aiHint && (
            <div className="text-[10px] text-muted-foreground px-1">{aiHint}</div>
          )}
          {modelOk && !currentClassTrained && (
            <div className="text-[10px] text-warning/90 leading-snug px-1">
              当前类 <span className="font-mono">{classes[activeClass]}</span> 模型未训, AI 不会预填此类框 (会预填: <span className="font-mono">{yoloInfo?.classes.join(', ')}</span>)
            </div>
          )}

          <Button
            size="sm"
            disabled={!name || saving}
            onClick={save}
            className="w-full"
          >
            {saving ? '保存中…' : `保存 (${boxes.length} 框) (S)`}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={!name || saving}
            onClick={skip}
            className="w-full"
            title="此图无任何 dialog/close_x/action_btn 等目标 — 训练时当背景图用 (不影响其他已标注图)"
          >
            标为负样本 (无目标) (B)
          </Button>
          <div className="grid grid-cols-2 gap-1">
            <Button
              size="sm"
              variant="ghost"
              disabled={!name}
              onClick={prevImg}
            >
              ← 上一张 (A)
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={!name}
              onClick={nextImg}
            >
              下一张 (D) →
            </Button>
          </div>
        </div>

        <div className="text-[10px] text-muted-foreground space-y-0.5 leading-relaxed">
          <div className="font-semibold text-foreground">键盘快捷键</div>
          <div><span className="font-mono">S</span> 保存 · <span className="font-mono">D</span>/→ 下张 · <span className="font-mono">A</span>/← 上张</div>
          <div><span className="font-mono">B</span> 负样本 · <span className="font-mono">E</span> AI 预标 · <span className="font-mono">Del</span> 删最后框</div>
          <div><span className="font-mono">1-9</span> 切类别 · <span className="font-mono">Esc</span> 取消画框</div>
          <div className="pt-1.5 text-foreground/70 border-t border-border mt-1.5">
            <div className="font-semibold mb-0.5">负样本是什么</div>
            <div>
              "干净大厅 / 战斗中 / loading / 直播 banner" 这种 <strong>整张图都没目标</strong> 的截图,
              点 "标为负样本" 就行 — 系统会写一个空 .txt 标记 "这张我看过了, 但确实没东西",
              训练时按 1:3 比例自动混进去当背景。
            </div>
            <div className="mt-1 text-foreground/60">
              不会影响其他已标注的图。负样本只挑容易混淆的 (大厅/banner/loading)
              30-50 张就够, <strong>不需要全标</strong>。
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
