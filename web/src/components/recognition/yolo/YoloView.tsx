/**
 * YoloView — YOLO 子页 (5 面板: capture / dataset / labeler / tester / model).
 * 接真后端: yoloApi (/api/yolo/info /test, /api/labeler/* dataset/labels/capture/model).
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { C } from '@/lib/design-tokens'
import { useAppStore } from '@/lib/store'
import {
  addLabelClass,
  captureFromInstance,
  datasetImgSrc,
  deleteDatasetImage,
  exportZipUrl,
  fetchDataset,
  fetchLabelClasses,
  fetchLabels,
  fetchYoloInfo,
  preannotateLabels,
  runYoloTest,
  saveLabels,
  uploadModel,
  type DatasetItem,
  type DatasetList,
  type LabelBox,
  type PerClassStat,
  type YoloDet,
  type YoloInfo,
  type YoloTestResult,
} from '@/lib/yoloApi'
import { GbSlider } from '@/components/ui/GbSlider'

// 8 个 class 的颜色 (跟 mock 同步; 多于 8 个的 class 走灰色)
const CLASS_COLOR = [
  '#dc2626', '#2563eb', '#a16207', '#7c3aed',
  '#059669', '#db2777', '#0891b2', '#ea580c',
  '#525252', '#78716c', '#374151', '#1e293b',
]

type YoloTab = 'capture' | 'dataset' | 'labeler' | 'tester' | 'model'

export function YoloView() {
  const [tab, setTab] = useState<YoloTab>('dataset')
  const [info, setInfo] = useState<YoloInfo | null>(null)

  // 共享: dataset stats (用于 rail badge + dataset panel)
  const [dataset, setDataset] = useState<DatasetList | null>(null)
  const reloadDataset = async () => {
    try {
      const r = await fetchDataset()
      // 过滤掉文件名含私用区字符 (PUA -) 的 — 后端 GET 这些会 400.
      // 多半是历史采集时含中文 IME 异常字, 跳过即可不影响整体.
      const BAD_RE = new RegExp('[\u0000-\u001F\uE000-\uF8FF\uFFFD]')
      const items = r.items.filter((it) => !BAD_RE.test(it.name))
      const dropped = r.items.length - items.length
      if (dropped > 0) {
        console.info(`[labeler] 过滤 ${dropped} 张文件名含异常字符的图`)
      }
      setDataset({ ...r, items })
    } catch {}
  }

  useEffect(() => {
    fetchYoloInfo().then(setInfo).catch(() => {})
    reloadDataset()
  }, [])

  const tabs: Array<[YoloTab, string, string, number | string | null]> = [
    ['capture', '采集', '01', null],
    ['dataset', '数据集', '02', dataset?.total ?? null],
    ['labeler', '标注', '03', dataset?.remaining ?? null],
    ['tester', '测试', '04', null],
    ['model', '模型', '05', info ? `${info.classes.length} 类` : null],
  ]

  return (
    <div
      style={{
        flex: 1,
        minHeight: 0,
        display: 'grid',
        gridTemplateColumns: '180px 1fr',
        background: C.bg,
      }}
    >
      <div
        style={{
          background: C.surface,
          borderRight: `1px solid ${C.border}`,
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        {tabs.map(([k, l, idx, badge]) => {
          const on = tab === k
          return (
            <button
              key={k}
              type="button"
              onClick={() => setTab(k)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                padding: '12px 16px',
                textAlign: 'left',
                background: on ? C.surface2 : 'transparent',
                borderTop: 'none',
                borderRight: 'none',
                borderBottom: `1px solid ${C.borderSoft}`,
                borderLeft: `3px solid ${on ? C.ink : 'transparent'}`,
                cursor: 'pointer',
                fontFamily: 'inherit',
              }}
            >
              <span
                className="gb-mono"
                style={{
                  fontSize: 10.5,
                  color: on ? C.ink : C.ink4,
                  fontWeight: 600,
                }}
              >
                {idx}
              </span>
              <span
                style={{
                  flex: 1,
                  fontSize: 13,
                  fontWeight: on ? 600 : 500,
                  color: on ? C.ink : C.ink2,
                }}
              >
                {l}
              </span>
              {badge != null && (
                <span
                  className="gb-mono"
                  style={{ fontSize: 10, color: on ? C.ink2 : C.ink4 }}
                >
                  {badge}
                </span>
              )}
            </button>
          )
        })}
        <div style={{ flex: 1 }} />
        {info && (
          <div style={{ padding: 12, borderTop: `1px solid ${C.borderSoft}` }}>
            <div style={{ fontSize: 10.5, color: C.ink3, marginBottom: 4 }}>
              当前模型
            </div>
            <div
              className="gb-mono"
              style={{ fontSize: 11, color: C.ink2, wordBreak: 'break-all' }}
            >
              {info.model_path?.split(/[\\/]/).pop() || '(未加载)'}
            </div>
            <div style={{ fontSize: 10, color: C.ink4, marginTop: 4 }}>
              {info.classes.length} 类 · 输入 {info.input_size}
            </div>
            {!info.available && (
              <div
                style={{
                  fontSize: 10,
                  color: C.error,
                  marginTop: 4,
                  wordBreak: 'break-word',
                }}
              >
                {info.error || '不可用'}
              </div>
            )}
          </div>
        )}
      </div>

      <div style={{ minHeight: 0, overflow: 'hidden' }}>
        {tab === 'capture' && <CapturePanel onCaptured={reloadDataset} />}
        {tab === 'dataset' && (
          <DatasetPanel dataset={dataset} reload={reloadDataset} />
        )}
        {tab === 'labeler' && (
          <LabelerPanel dataset={dataset} reload={reloadDataset} />
        )}
        {tab === 'tester' && <TesterPanel info={info} />}
        {tab === 'model' && (
          <ModelPanel
            info={info}
            dataset={dataset}
            onUploaded={async () => {
              const r = await fetchYoloInfo()
              setInfo(r)
            }}
          />
        )}
      </div>
    </div>
  )
}

// ─── 01 Capture ─────────────────────────────────────────────────────────
function CapturePanel({ onCaptured }: { onCaptured: () => void }) {
  const emulators = useAppStore((s) => s.emulators)
  const running = useMemo(() => emulators.filter((e) => e.running), [emulators])
  const [instanceIdx, setInstanceIdx] = useState<number | null>(null)
  const [tag, setTag] = useState('manual')
  const [recent, setRecent] = useState<
    Array<{ name: string; size: number; ts: number; w: number; h: number }>
  >([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    if (instanceIdx == null && running.length > 0) {
      setInstanceIdx(running[0].index)
    }
  }, [running, instanceIdx])

  const doCapture = async () => {
    if (instanceIdx == null) {
      setErr('选实例')
      return
    }
    setBusy(true)
    setErr(null)
    try {
      const r = await captureFromInstance(instanceIdx, tag)
      setRecent((cur) => [
        { name: r.name, size: r.size, ts: Date.now(), w: r.width, h: r.height },
        ...cur,
      ])
      onCaptured()
    } catch (e) {
      setErr(`采集失败: ${String(e).slice(0, 120)}`)
    } finally {
      setBusy(false)
    }
  }

  const livePreview =
    instanceIdx != null ? `/api/screenshot/${instanceIdx}?t=${Date.now()}` : ''

  return (
    <div
      style={{
        height: '100%',
        display: 'grid',
        gridTemplateColumns: '1fr 320px',
        minHeight: 0,
      }}
    >
      <div
        style={{
          padding: 18,
          display: 'flex',
          flexDirection: 'column',
          gap: 14,
          minHeight: 0,
        }}
      >
        <div
          style={{
            flex: 1,
            minHeight: 0,
            position: 'relative',
            background: '#0a0a08',
            borderRadius: 7,
            overflow: 'hidden',
            border: `1px solid ${C.border}`,
          }}
        >
          {livePreview ? (
            <img
              src={livePreview}
              alt=""
              style={{
                position: 'absolute',
                inset: 0,
                width: '100%',
                height: '100%',
                objectFit: 'contain',
                display: 'block',
              }}
            />
          ) : (
            <div
              style={{
                position: 'absolute',
                inset: 0,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: 'rgba(255,255,255,.5)',
                fontSize: 12,
                fontFamily: C.fontMono,
              }}
            >
              没运行的实例
            </div>
          )}
          <div
            style={{
              position: 'absolute',
              top: 10,
              left: 10,
              padding: '4px 8px',
              borderRadius: 4,
              background: 'rgba(20,20,18,.78)',
              backdropFilter: 'blur(6px)',
              color: 'rgba(255,255,255,.85)',
              fontFamily: C.fontMono,
              fontSize: 11,
              display: 'flex',
              gap: 8,
              alignItems: 'center',
            }}
          >
            <span
              className="gb-pulse"
              style={{
                width: 6,
                height: 6,
                borderRadius: '50%',
                background: instanceIdx != null ? '#16a34a' : '#a16207',
                display: 'inline-block',
              }}
            />
            <span>
              {instanceIdx != null
                ? `实例 #${instanceIdx} · 实时帧`
                : '请选实例'}
            </span>
          </div>
        </div>

        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ fontSize: 12, color: C.ink2 }}>实例</span>
          <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
            {running.length === 0 && (
              <span style={{ fontSize: 11, color: C.ink4 }}>没运行的实例</span>
            )}
            {running.map((e) => {
              const on = instanceIdx === e.index
              return (
                <button
                  key={e.index}
                  type="button"
                  onClick={() => setInstanceIdx(e.index)}
                  style={{
                    padding: '3px 8px',
                    borderRadius: 4,
                    fontSize: 11,
                    fontFamily: C.fontMono,
                    fontWeight: 600,
                    color: on ? C.ink : C.ink3,
                    background: on ? C.surface : C.surface2,
                    border: `1px solid ${on ? C.border : 'transparent'}`,
                    cursor: 'pointer',
                  }}
                >
                  #{e.index}
                </button>
              )
            })}
          </div>
          <span style={{ fontSize: 12, color: C.ink2, marginLeft: 12 }}>tag</span>
          <input
            value={tag}
            onChange={(e) => setTag(e.target.value)}
            className="gb-mono"
            style={{
              padding: '5px 8px',
              fontSize: 12,
              borderRadius: 5,
              background: C.surface2,
              border: `1px solid ${C.border}`,
              color: C.ink,
              outline: 'none',
              width: 120,
              fontFamily: 'inherit',
            }}
          />
          <span style={{ flex: 1 }} />
          <button
            type="button"
            onClick={doCapture}
            disabled={busy || instanceIdx == null}
            style={{
              padding: '7px 18px',
              borderRadius: 6,
              fontSize: 12.5,
              fontWeight: 600,
              background: instanceIdx != null ? C.ink : C.surface2,
              color: instanceIdx != null ? '#f0eee9' : C.ink4,
              border: 'none',
              cursor: busy || instanceIdx == null ? 'not-allowed' : 'pointer',
              fontFamily: 'inherit',
            }}
          >
            {busy ? '采集中…' : '截当前帧'}
          </button>
        </div>
        {err && (
          <div
            className="gb-mono"
            style={{ fontSize: 11, color: C.error, padding: '0 4px' }}
          >
            {err}
          </div>
        )}
      </div>

      <div
        style={{
          borderLeft: `1px solid ${C.border}`,
          background: C.surface,
          display: 'flex',
          flexDirection: 'column',
          minHeight: 0,
        }}
      >
        <div
          style={{
            padding: '12px 14px',
            borderBottom: `1px solid ${C.borderSoft}`,
            display: 'flex',
            alignItems: 'center',
            gap: 8,
          }}
        >
          <span style={{ fontSize: 12, fontWeight: 600 }}>本次会话采集</span>
          <span className="gb-mono" style={{ fontSize: 10.5, color: C.ink4 }}>
            {recent.length}
          </span>
        </div>
        <div
          className="gb-scroll"
          style={{ flex: 1, minHeight: 0, overflow: 'hidden auto' }}
        >
          {recent.length === 0 && (
            <div
              style={{
                padding: 24,
                textAlign: 'center',
                fontSize: 11.5,
                color: C.ink4,
              }}
            >
              点击「截当前帧」开始采集
            </div>
          )}
          {recent.map((r, i) => (
            <div
              key={i}
              style={{
                padding: '10px 14px',
                borderBottom: `1px solid ${C.borderSoft}`,
                display: 'flex',
                alignItems: 'center',
                gap: 10,
              }}
            >
              <img
                src={datasetImgSrc(r.name)}
                alt=""
                style={{
                  width: 56,
                  height: 32,
                  objectFit: 'cover',
                  borderRadius: 3,
                  background: '#1f2937',
                }}
              />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div
                  className="gb-mono"
                  style={{
                    fontSize: 11,
                    color: C.ink2,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {r.name}
                </div>
                <div style={{ fontSize: 10, color: C.ink4, marginTop: 2 }}>
                  {r.w}×{r.h} · {fmtBytes(r.size)} · {fmtAgo(r.ts)}
                </div>
              </div>
              <span
                className="gb-mono"
                style={{
                  fontSize: 9.5,
                  color: '#a16207',
                  background: C.warnSoft,
                  padding: '1px 5px',
                  borderRadius: 3,
                }}
              >
                未标注
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ─── 02 Dataset ─────────────────────────────────────────────────────────
function DatasetPanel({
  dataset,
  reload,
}: {
  dataset: DatasetList | null
  reload: () => Promise<void>
}) {
  const [labelFilter, setLabelFilter] = useState<'all' | 'labeled' | 'unlabeled' | 'skipped'>(
    'all',
  )
  const [classFilter, setClassFilter] = useState('all')
  const [sel, setSel] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)

  if (!dataset) return <LoadingBox label="加载数据集…" />

  const filtered = dataset.items.filter((d) => {
    if (labelFilter === 'labeled' && !d.labeled) return false
    if (labelFilter === 'unlabeled' && (d.labeled || d.skipped)) return false
    if (labelFilter === 'skipped' && !d.skipped) return false
    if (classFilter !== 'all' && !d.class_ids.includes(+classFilter)) return false
    return true
  })

  const toggleSel = (name: string) =>
    setSel((prev) => {
      const n = new Set(prev)
      if (n.has(name)) n.delete(name)
      else n.add(name)
      return n
    })

  const batchDelete = async () => {
    if (!confirm(`确认删除 ${sel.size} 张图?`)) return
    setBusy(true)
    try {
      for (const name of sel) {
        await deleteDatasetImage(name)
      }
      setSel(new Set())
      await reload()
    } catch (e) {
      alert(`删除失败: ${String(e).slice(0, 120)}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      style={{ height: '100%', display: 'flex', flexDirection: 'column', minHeight: 0 }}
    >
      <div
        style={{
          padding: '12px 22px',
          background: C.surface,
          borderBottom: `1px solid ${C.border}`,
          display: 'grid',
          gridTemplateColumns: 'repeat(4, 1fr) 2fr',
          gap: 22,
        }}
      >
        <Kpi label="总图数" value={dataset.total} mono />
        <Kpi label="已标注" value={dataset.labeled} mono color={C.live} />
        <Kpi label="待标注" value={dataset.remaining} mono color="#a16207" />
        <Kpi label="跳过" value={dataset.skipped} mono color={C.ink3} />
        <div>
          <div
            style={{
              fontSize: 9.5,
              color: C.ink3,
              fontWeight: 500,
              letterSpacing: '.06em',
              textTransform: 'uppercase',
              marginBottom: 6,
            }}
          >
            每类实例数
          </div>
          <ClassBars classes={dataset.per_class} />
        </div>
      </div>

      <div
        style={{
          padding: '10px 22px',
          background: C.surface,
          borderBottom: `1px solid ${C.borderSoft}`,
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          flexWrap: 'wrap',
        }}
      >
        <Segmented
          value={labelFilter}
          onChange={(v) => setLabelFilter(v as typeof labelFilter)}
          options={[
            ['all', `全部 ${dataset.total}`],
            ['labeled', `已标 ${dataset.labeled}`],
            ['unlabeled', `待标 ${dataset.remaining}`],
            ['skipped', `跳过 ${dataset.skipped}`],
          ]}
        />
        <select
          value={classFilter}
          onChange={(e) => setClassFilter(e.target.value)}
          style={{
            padding: '4px 8px',
            fontSize: 12,
            borderRadius: 5,
            background: C.surface,
            border: `1px solid ${C.border}`,
            color: C.ink2,
            outline: 'none',
            fontFamily: 'inherit',
          }}
        >
          <option value="all">全部类别</option>
          {dataset.per_class.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name} ({c.images})
            </option>
          ))}
        </select>
        <span style={{ flex: 1 }} />
        {sel.size > 0 && (
          <>
            <span style={{ fontSize: 11.5, color: C.ink2 }}>
              已选 <b className="gb-mono">{sel.size}</b>
            </span>
            <button
              type="button"
              onClick={batchDelete}
              disabled={busy}
              style={{
                padding: '5px 11px',
                borderRadius: 5,
                fontSize: 12,
                color: C.error,
                background: C.surface,
                border: `1px solid ${C.border}`,
                cursor: busy ? 'wait' : 'pointer',
                fontFamily: 'inherit',
              }}
            >
              批量删除
            </button>
          </>
        )}
        <a
          href={exportZipUrl()}
          download="dataset.zip"
          style={{
            padding: '5px 12px',
            borderRadius: 5,
            fontSize: 12,
            fontWeight: 500,
            color: C.ink2,
            background: C.surface,
            border: `1px solid ${C.border}`,
            textDecoration: 'none',
          }}
        >
          导出 ZIP (images/ + labels/)
        </a>
      </div>

      <div
        className="gb-scroll"
        style={{ flex: 1, minHeight: 0, overflow: 'hidden auto', padding: 16 }}
      >
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))',
            gap: 8,
          }}
        >
          {filtered.map((d) => (
            <DatasetCard
              key={d.name}
              item={d}
              selected={sel.has(d.name)}
              onToggle={() => toggleSel(d.name)}
            />
          ))}
        </div>
        <div
          style={{ textAlign: 'center', padding: 14, fontSize: 11, color: C.ink4 }}
        >
          {`共 ${filtered.length} 张 · 浏览器自动 lazy 加载视口内的图`}
        </div>
      </div>
    </div>
  )
}

function DatasetCard({
  item,
  selected,
  onToggle,
}: {
  item: DatasetItem
  selected: boolean
  onToggle: () => void
}) {
  return (
    <div
      onClick={onToggle}
      style={{
        position: 'relative',
        cursor: 'pointer',
        background: C.surface,
        borderRadius: 5,
        overflow: 'hidden',
        border: `1.5px solid ${selected ? C.ink : C.border}`,
      }}
    >
      <div style={{ position: 'relative', height: 80, background: '#1f2937' }}>
        <img
          src={datasetImgSrc(item.name)}
          alt=""
          loading="lazy"
          decoding="async"
          style={{
            width: '100%',
            height: '100%',
            objectFit: 'cover',
            display: 'block',
          }}
        />
        <div
          style={{
            position: 'absolute',
            top: 4,
            left: 4,
            padding: '1px 5px',
            borderRadius: 2,
            background: item.labeled
              ? 'rgba(22,163,74,.85)'
              : item.skipped
                ? 'rgba(113,113,122,.85)'
                : 'rgba(217,119,6,.85)',
            color: '#fff',
            fontFamily: C.fontMono,
            fontSize: 9,
            fontWeight: 600,
          }}
        >
          {item.labeled ? '已标' : item.skipped ? '跳过' : '待标'}
        </div>
        {selected && (
          <div
            style={{
              position: 'absolute',
              top: 4,
              right: 4,
              width: 16,
              height: 16,
              borderRadius: 3,
              background: C.ink,
              display: 'grid',
              placeItems: 'center',
              color: '#f0eee9',
              fontSize: 11,
            }}
          >
            ✓
          </div>
        )}
      </div>
      <div
        className="gb-mono"
        style={{
          padding: '4px 6px',
          fontSize: 9.5,
          color: C.ink3,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
      >
        {item.name.length > 24 ? item.name.slice(0, 22) + '…' : item.name}
      </div>
    </div>
  )
}

// ─── 03 Labeler ─────────────────────────────────────────────────────────
function LabelerPanel({
  dataset,
  reload,
}: {
  dataset: DatasetList | null
  reload: () => Promise<void>
}) {
  const [classes, setClasses] = useState<string[]>([])
  const [statusFilter, setStatusFilter] = useState<'all' | 'labeled' | 'unlabeled' | 'skipped'>(
    'unlabeled',
  )
  const [classFilter, setClassFilter] = useState('all')
  const [idx, setIdx] = useState(0)
  const [boxes, setBoxes] = useState<LabelBox[]>([])
  const [selectedBox, setSelectedBox] = useState<number | null>(null)
  const [drawClass, setDrawClass] = useState(0)
  const [drawing, setDrawing] = useState<{ start: { x: number; y: number }; end: { x: number; y: number } } | null>(null)
  const [zoom, setZoom] = useState(false)
  const [busy, setBusy] = useState(false)
  const [hint, setHint] = useState('')
  const canvasRef = useRef<HTMLDivElement | null>(null)
  const imgRef = useRef<HTMLImageElement | null>(null)
  // 真实图像在 canvas 内的渲染矩形 (相对 canvasRef 的 px 坐标).
  // bbox 用这个矩形换算位置, 拖拽 / mouse 也基于此矩形归一化, 避免 letterbox 偏移.
  const [imgRect, setImgRect] = useState<{ x: number; y: number; w: number; h: number } | null>(null)
  const measureImg = () => {
    const img = imgRef.current
    const canvas = canvasRef.current
    if (!img || !canvas) return
    const cr = canvas.getBoundingClientRect()
    const ir = img.getBoundingClientRect()
    if (ir.width === 0 || ir.height === 0) return
    // img 元素跟 canvas 同尺寸 (width/height: 100%), 真实图像 letterboxed 在元素内.
    // 用 naturalWidth/Height 算出 contain-fit 后的真实矩形.
    const nw = img.naturalWidth || 1280
    const nh = img.naturalHeight || 720
    const elAR = ir.width / ir.height
    const imgAR = nw / nh
    let w: number
    let h: number
    if (elAR > imgAR) {
      h = ir.height
      w = h * imgAR
    } else {
      w = ir.width
      h = w / imgAR
    }
    const xOff = (ir.width - w) / 2
    const yOff = (ir.height - h) / 2
    setImgRect({
      x: ir.left - cr.left + xOff,
      y: ir.top - cr.top + yOff,
      w,
      h,
    })
  }
  useEffect(() => {
    if (!canvasRef.current) return
    const ro = new ResizeObserver(measureImg)
    ro.observe(canvasRef.current)
    if (imgRef.current) ro.observe(imgRef.current)
    return () => ro.disconnect()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    fetchLabelClasses().then((r) => setClasses(r.classes)).catch(() => {})
  }, [])

  const filtered = useMemo(() => {
    if (!dataset) return []
    return dataset.items.filter((d) => {
      if (statusFilter === 'unlabeled' && (d.labeled || d.skipped)) return false
      if (statusFilter === 'labeled' && !d.labeled) return false
      if (statusFilter === 'skipped' && !d.skipped) return false
      if (classFilter !== 'all' && !d.class_ids.includes(+classFilter)) return false
      return true
    })
  }, [dataset, statusFilter, classFilter])

  const cur = filtered[idx % Math.max(1, filtered.length)] || null

  // load labels when cur changes
  useEffect(() => {
    if (!cur) {
      setBoxes([])
      return
    }
    fetchLabels(cur.name)
      .then((r) => setBoxes(r.boxes))
      .catch(() => setBoxes([]))
    setSelectedBox(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cur?.name])

  useEffect(() => {
    setIdx(0)
  }, [statusFilter, classFilter])

  const getPt = (e: { clientX: number; clientY: number }) => {
    const canvas = canvasRef.current
    if (!canvas || !imgRect) return { x: 0, y: 0 }
    const cr = canvas.getBoundingClientRect()
    return {
      x: Math.max(0, Math.min(1, (e.clientX - cr.left - imgRect.x) / imgRect.w)),
      y: Math.max(0, Math.min(1, (e.clientY - cr.top - imgRect.y) / imgRect.h)),
    }
  }

  const onDown = (e: React.MouseEvent) => {
    if (e.button !== 0) return
    const p = getPt(e)
    setDrawing({ start: p, end: p })
    setSelectedBox(null)
  }

  useEffect(() => {
    if (!drawing) return
    const move = (e: MouseEvent) => {
      const p = getPt(e)
      setDrawing((d) => (d ? { ...d, end: p } : null))
    }
    const up = () => {
      if (!drawing) return
      const { start, end } = drawing
      const x1 = Math.min(start.x, end.x)
      const x2 = Math.max(start.x, end.x)
      const y1 = Math.min(start.y, end.y)
      const y2 = Math.max(start.y, end.y)
      if (x2 - x1 > 0.005 && y2 - y1 > 0.005) {
        setBoxes((b) => [
          ...b,
          {
            class_id: drawClass,
            cx: (x1 + x2) / 2,
            cy: (y1 + y2) / 2,
            w: x2 - x1,
            h: y2 - y1,
          },
        ])
      }
      setDrawing(null)
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
    return () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drawing, drawClass])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
      const k = e.key
      if (k === 'ArrowRight' || k === 'd' || k === 'D') {
        e.preventDefault()
        saveAndNext()
      } else if (k === 'ArrowLeft' || k === 'a' || k === 'A') {
        e.preventDefault()
        setIdx((i) => Math.max(0, i - 1))
      } else if (k === 's' || k === 'S') {
        e.preventDefault()
        doSave(false)
      } else if (k === 'e' || k === 'E') {
        e.preventDefault()
        doPreann()
      } else if (k === 'Backspace' || k === 'Delete') {
        if (selectedBox != null) {
          e.preventDefault()
          setBoxes((b) => b.filter((_, i) => i !== selectedBox))
          setSelectedBox(null)
        }
      } else if (/^[1-9]$/.test(k)) {
        const n = +k - 1
        if (n < classes.length) setDrawClass(n)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedBox, classes.length, cur?.name, boxes])

  const doSave = async (advance = false) => {
    if (!cur) return
    setBusy(true)
    setHint('保存中…')
    try {
      await saveLabels(cur.name, boxes)
      setHint(`已保存 ${boxes.length} 框`)
      await reload()
      if (advance) setIdx((i) => i + 1)
    } catch (e) {
      setHint(`保存失败: ${String(e).slice(0, 100)}`)
    } finally {
      setBusy(false)
    }
  }

  const saveAndNext = async () => {
    await doSave(true)
  }

  const doPreann = async () => {
    if (!cur) return
    setBusy(true)
    setHint('预标注中…')
    try {
      const r = await preannotateLabels(cur.name)
      setBoxes(r.boxes)
      setHint(`预标注 ${r.count} 框 · ${r.duration_ms}ms`)
    } catch (e) {
      setHint(`预标注失败: ${String(e).slice(0, 100)}`)
    } finally {
      setBusy(false)
    }
  }

  const doAddClass = async () => {
    const name = prompt('新类别名:')
    if (!name) return
    try {
      const r = await addLabelClass(name)
      setClasses(r.classes)
    } catch (e) {
      alert(`添加失败: ${String(e).slice(0, 120)}`)
    }
  }

  if (!dataset) return <LoadingBox label="加载数据集…" />
  if (filtered.length === 0)
    return (
      <div
        style={{
          flex: 1,
          padding: 60,
          textAlign: 'center',
          color: C.ink4,
          fontSize: 13,
        }}
      >
        当前筛选无图片
      </div>
    )

  const seed = cur?.name.split('').reduce((a, c) => a + c.charCodeAt(0), 0) || 0

  return (
    <>
      <div
        style={{
          height: '100%',
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1fr) 280px',
          minHeight: 0,
          minWidth: 0,
        }}
      >
        <div
          style={{
            padding: 14,
            display: 'flex',
            flexDirection: 'column',
            gap: 10,
            minHeight: 0,
            minWidth: 0,
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              flexShrink: 0,
              minWidth: 0,
            }}
          >
            <span
              className="gb-mono"
              title={cur?.name}
              style={{
                fontSize: 12,
                color: C.ink2,
                fontWeight: 600,
                flex: '0 1 auto',
                minWidth: 0,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                maxWidth: 280,
              }}
            >
              {cur?.name || '—'}
            </span>
            <span
              style={{
                fontSize: 10.5,
                color: C.ink4,
                flexShrink: 0,
                fontFamily: C.fontMono,
              }}
            >
              {idx + 1} / {filtered.length}
            </span>
            <Segmented
              value={statusFilter}
              onChange={(v) => setStatusFilter(v as typeof statusFilter)}
              options={[
                ['unlabeled', `待标 ${dataset.remaining}`],
                ['labeled', `已标 ${dataset.labeled}`],
                ['skipped', `跳过 ${dataset.skipped}`],
                ['all', `全部 ${dataset.total}`],
              ]}
            />
            <select
              value={classFilter}
              onChange={(e) => setClassFilter(e.target.value)}
              style={{
                padding: '3px 7px',
                fontSize: 11,
                borderRadius: 4,
                background: C.surface,
                border: `1px solid ${C.border}`,
                color: C.ink2,
                outline: 'none',
                fontFamily: 'inherit',
                flexShrink: 0,
              }}
            >
              <option value="all">全部类别</option>
              {classes.map((name, id) => (
                <option key={id} value={id}>
                  {name}
                </option>
              ))}
            </select>
            <span style={{ flex: 1, minWidth: 8 }} />
            <button
              type="button"
              onClick={() => setIdx((i) => Math.max(0, i - 1))}
              title="A / ← 上一张"
              style={btnGhost}
            >
              ← 上一张
            </button>
            <button
              type="button"
              onClick={doPreann}
              disabled={busy || !cur}
              title="E AI 自动标注"
              style={{
                ...btnGhost,
                color: C.warn,
                borderColor: C.warn + '60',
                background: C.warnSoft,
                fontWeight: 600,
              }}
            >
              ✦ AI 自动标注 <kbd style={kbdMini}>E</kbd>
            </button>
            <button
              type="button"
              onClick={() => doSave(false)}
              disabled={busy}
              title="S 保存 (不前进)"
              style={btnGhost}
            >
              保存 <kbd style={kbdMini}>S</kbd>
            </button>
            <button
              type="button"
              onClick={() => setIdx((i) => i + 1)}
              title="跳过当前张, 不保存"
              style={btnGhost}
            >
              跳过
            </button>
            <button
              type="button"
              onClick={saveAndNext}
              disabled={busy}
              title="D / → 保存 + 下一张"
              style={{
                padding: '4px 12px',
                borderRadius: 5,
                fontSize: 11.5,
                fontWeight: 600,
                color: '#f0eee9',
                background: C.ink,
                border: 'none',
                cursor: busy ? 'wait' : 'pointer',
                fontFamily: 'inherit',
                flexShrink: 0,
              }}
            >
              保存 + 下一张 <kbd style={kbdMini}>D</kbd>
            </button>
          </div>

          <div
            ref={canvasRef}
            onDoubleClick={() => setZoom(true)}
            style={{
              flex: 1,
              minHeight: 0,
              position: 'relative',
              background: '#0a0a08',
              borderRadius: 7,
              overflow: 'hidden',
              border: `1px solid ${C.border}`,
            }}
          >
            {/* img: 占满 canvasRef, objectFit:contain letterbox */}
            {cur && (
              <img
                key={cur.name}
                ref={imgRef}
                src={datasetImgSrc(cur.name)}
                alt=""
                onLoad={measureImg}
                onError={(e) =>
                  console.warn('[labeler] image failed:', cur.name, e)
                }
                style={{
                  position: 'absolute',
                  top: 0,
                  left: 0,
                  width: '100%',
                  height: '100%',
                  objectFit: 'contain',
                  display: 'block',
                  pointerEvents: 'none',
                  userSelect: 'none',
                }}
              />
            )}
            {/* overlay: absolute 全覆盖, bbox 用 imgRect px 算位置 */}
            <div
              onMouseDown={onDown}
              style={{
                position: 'absolute',
                inset: 0,
                cursor: imgRect ? 'crosshair' : 'default',
              }}
            >
              {imgRect &&
                boxes.map((b, i) => {
                  const x1 = b.cx - b.w / 2
                  const y1 = b.cy - b.h / 2
                  const className =
                    classes[b.class_id] || `cls_${b.class_id}`
                  const color = CLASS_COLOR[b.class_id] || '#525252'
                  const sel = i === selectedBox
                  const left = imgRect.x + x1 * imgRect.w
                  const top = imgRect.y + y1 * imgRect.h
                  const w = b.w * imgRect.w
                  const h = b.h * imgRect.h
                  return (
                    <div
                      key={i}
                      onMouseDown={(e) => {
                        e.stopPropagation()
                        setSelectedBox(i)
                        setDrawing(null)
                      }}
                      style={{
                        position: 'absolute',
                        left,
                        top,
                        width: w,
                        height: h,
                        border: `2px solid ${color}`,
                        outline: sel ? '2px solid #f0eee9' : 'none',
                        outlineOffset: 1,
                        background: `${color}1a`,
                        cursor: 'pointer',
                      }}
                    >
                      <div
                        style={{
                          position: 'absolute',
                          top: -22,
                          left: -2,
                          background: color,
                          color: '#fff',
                          padding: '2px 6px',
                          fontFamily: C.fontMono,
                          fontSize: 10.5,
                          fontWeight: 600,
                          borderRadius: 3,
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {className}
                      </div>
                    </div>
                  )
                })}
              {imgRect && drawing &&
                (() => {
                  const x1 = Math.min(drawing.start.x, drawing.end.x)
                  const y1 = Math.min(drawing.start.y, drawing.end.y)
                  const w = Math.abs(drawing.end.x - drawing.start.x)
                  const h = Math.abs(drawing.end.y - drawing.start.y)
                  return (
                    <div
                      style={{
                        position: 'absolute',
                        pointerEvents: 'none',
                        left: imgRect.x + x1 * imgRect.w,
                        top: imgRect.y + y1 * imgRect.h,
                        width: w * imgRect.w,
                        height: h * imgRect.h,
                        border: `2px dashed ${CLASS_COLOR[drawClass] || '#525252'}`,
                        background: `${CLASS_COLOR[drawClass] || '#525252'}26`,
                      }}
                    />
                  )
                })()}
            </div>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation()
                setZoom(true)
              }}
              onMouseDown={(e) => e.stopPropagation()}
              style={{
                position: 'absolute',
                top: 10,
                right: 10,
                zIndex: 4,
                padding: '4px 9px',
                borderRadius: 4,
                background: 'rgba(20,20,18,.78)',
                backdropFilter: 'blur(6px)',
                color: 'rgba(255,255,255,.85)',
                fontFamily: C.fontMono,
                fontSize: 11,
                border: 'none',
                cursor: 'pointer',
              }}
            >
              放大
            </button>
            {/* hint overlays — anchored to canvas */}
            {boxes.length === 0 && !drawing && (
              <div
                style={{
                  position: 'absolute',
                  left: '50%',
                  top: 16,
                  transform: 'translateX(-50%)',
                  padding: '5px 12px',
                  borderRadius: 4,
                  background: 'rgba(20,20,18,.78)',
                  backdropFilter: 'blur(6px)',
                  fontFamily: C.fontMono,
                  fontSize: 11,
                  color: 'rgba(255,255,255,.78)',
                }}
              >
                拖拽画框 · 选 class 后标注 · 预标注按钮可一键填充
              </div>
            )}
            {hint && (
              <div
                style={{
                  position: 'absolute',
                  bottom: 10,
                  left: 10,
                  padding: '4px 9px',
                  borderRadius: 4,
                  background: 'rgba(20,20,18,.78)',
                  backdropFilter: 'blur(6px)',
                  color: 'rgba(255,255,255,.78)',
                  fontFamily: C.fontMono,
                  fontSize: 10.5,
                }}
              >
                {hint}
              </div>
            )}
          </div>

          <Filmstrip filtered={filtered} idx={idx} setIdx={setIdx} seed={seed} />

          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 14,
              fontSize: 11,
              color: C.ink4,
            }}
          >
            <span><kbd style={kbdSty}>A</kbd>/<kbd style={kbdSty}>←</kbd> 上一张</span>
            <span><kbd style={kbdSty}>D</kbd>/<kbd style={kbdSty}>→</kbd> 保存 + 下一张</span>
            <span><kbd style={kbdSty}>S</kbd> 保存 (不前进)</span>
            <span><kbd style={kbdSty}>E</kbd> AI 自动标注</span>
            <span><kbd style={kbdSty}>⌫</kbd> 删除选中框</span>
            <span><kbd style={kbdSty}>1-9</kbd> 切类别</span>
          </div>
        </div>

        <div
          style={{
            borderLeft: `1px solid ${C.border}`,
            background: C.surface,
            display: 'flex',
            flexDirection: 'column',
            minHeight: 0,
          }}
        >
          <div style={{ padding: 12, borderBottom: `1px solid ${C.borderSoft}` }}>
            <div
              style={{
                fontSize: 11,
                color: C.ink3,
                fontWeight: 500,
                letterSpacing: '.06em',
                textTransform: 'uppercase',
                marginBottom: 8,
              }}
            >
              类别 (画框时使用)
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              {classes.map((name, id) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => setDrawClass(id)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    padding: '5px 8px',
                    borderRadius: 4,
                    background: drawClass === id ? C.surface2 : 'transparent',
                    border: `1px solid ${drawClass === id ? C.border : 'transparent'}`,
                    textAlign: 'left',
                    cursor: 'pointer',
                    fontFamily: 'inherit',
                  }}
                >
                  <span
                    style={{
                      width: 10,
                      height: 10,
                      borderRadius: 2,
                      background: CLASS_COLOR[id] || '#525252',
                    }}
                  />
                  <span
                    className="gb-mono"
                    style={{
                      fontSize: 11.5,
                      color: drawClass === id ? C.ink : C.ink2,
                      fontWeight: drawClass === id ? 600 : 500,
                    }}
                  >
                    {name}
                  </span>
                  <span style={{ flex: 1 }} />
                  <span
                    className="gb-mono"
                    style={{ fontSize: 9.5, color: C.ink4 }}
                  >
                    {id}
                  </span>
                </button>
              ))}
              <button
                type="button"
                onClick={doAddClass}
                style={{
                  marginTop: 4,
                  padding: '5px 8px',
                  borderRadius: 4,
                  border: `1px dashed ${C.border}`,
                  fontSize: 11.5,
                  color: C.ink3,
                  textAlign: 'left',
                  background: 'transparent',
                  cursor: 'pointer',
                  fontFamily: 'inherit',
                }}
              >
                + 添加类别
              </button>
            </div>
          </div>

          <div style={{ padding: 12, borderBottom: `1px solid ${C.borderSoft}` }}>
            <button
              type="button"
              onClick={doPreann}
              disabled={busy}
              style={{
                width: '100%',
                padding: '7px 0',
                borderRadius: 5,
                fontSize: 12,
                fontWeight: 600,
                color: C.ink2,
                background: C.surface2,
                border: `1px solid ${C.border}`,
                cursor: busy ? 'wait' : 'pointer',
                fontFamily: 'inherit',
              }}
            >
              ✦ AI 自动标注 (用当前模型) <kbd style={kbdMini}>E</kbd>
            </button>
          </div>

          <div
            className="gb-scroll"
            style={{ flex: 1, minHeight: 0, overflow: 'hidden auto' }}
          >
            <div
              style={{
                padding: '10px 12px 4px',
                fontSize: 11,
                color: C.ink3,
                fontWeight: 500,
                letterSpacing: '.06em',
                textTransform: 'uppercase',
              }}
            >
              已标 {boxes.length} 个框
            </div>
            {boxes.map((b, i) => {
              const className = classes[b.class_id] || `cls_${b.class_id}`
              const sel = i === selectedBox
              return (
                <button
                  key={i}
                  type="button"
                  onClick={() => setSelectedBox(i)}
                  style={{
                    display: 'flex',
                    width: '100%',
                    textAlign: 'left',
                    padding: '6px 12px',
                    gap: 8,
                    alignItems: 'center',
                    background: sel ? C.surface2 : 'transparent',
                    border: 'none',
                    cursor: 'pointer',
                    fontFamily: 'inherit',
                  }}
                >
                  <span
                    style={{
                      width: 8,
                      height: 8,
                      borderRadius: 2,
                      background: CLASS_COLOR[b.class_id] || '#525252',
                      flexShrink: 0,
                    }}
                  />
                  <span
                    className="gb-mono"
                    style={{ fontSize: 11, color: C.ink2, flex: 1 }}
                  >
                    {className}
                  </span>
                  <span
                    className="gb-mono"
                    style={{ fontSize: 9.5, color: C.ink4 }}
                  >
                    {(b.w * 100).toFixed(0)}×{(b.h * 100).toFixed(0)}%
                  </span>
                </button>
              )
            })}
          </div>
          <div
            style={{
              padding: 10,
              borderTop: `1px solid ${C.borderSoft}`,
              display: 'flex',
              gap: 8,
            }}
          >
            <button
              type="button"
              onClick={() => doSave(false)}
              disabled={busy}
              style={{
                flex: 1,
                padding: '6px 0',
                borderRadius: 5,
                fontSize: 11.5,
                fontWeight: 600,
                color: '#f0eee9',
                background: C.ink,
                border: 'none',
                cursor: busy ? 'wait' : 'pointer',
                fontFamily: 'inherit',
              }}
            >
              {busy ? '…' : '保存'}
            </button>
          </div>
        </div>
      </div>
      {zoom && cur && (
        <div
          onClick={() => setZoom(false)}
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 50,
            background: 'rgba(10,10,8,.86)',
            backdropFilter: 'blur(4px)',
            display: 'grid',
            placeItems: 'center',
            padding: 32,
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              width: 'min(1400px, 100%)',
              background: '#0a0a08',
              borderRadius: 10,
              overflow: 'hidden',
              border: '1px solid #2a2a25',
            }}
          >
            <div
              style={{
                padding: '10px 16px',
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                borderBottom: '1px solid #2a2a25',
                color: 'rgba(255,255,255,.85)',
              }}
            >
              <span
                className="gb-mono"
                style={{ fontSize: 12, fontWeight: 600 }}
              >
                {cur.name}
              </span>
              <span style={{ flex: 1 }} />
              <button
                type="button"
                onClick={() => setZoom(false)}
                style={{
                  padding: '4px 10px',
                  borderRadius: 5,
                  fontSize: 12,
                  color: '#1a1a17',
                  background: '#f0eee9',
                  fontWeight: 600,
                  border: 'none',
                  cursor: 'pointer',
                  fontFamily: 'inherit',
                }}
              >
                关闭
              </button>
            </div>
            <div style={{ position: 'relative', aspectRatio: '16/9' }}>
              <img
                src={datasetImgSrc(cur.name)}
                alt=""
                style={{
                  width: '100%',
                  height: '100%',
                  objectFit: 'contain',
                  display: 'block',
                }}
              />
              {boxes.map((b, i) => {
                const x1 = b.cx - b.w / 2
                const y1 = b.cy - b.h / 2
                const color = CLASS_COLOR[b.class_id] || '#525252'
                const className = classes[b.class_id] || `cls_${b.class_id}`
                return (
                  <div
                    key={i}
                    style={{
                      position: 'absolute',
                      left: `${x1 * 100}%`,
                      top: `${y1 * 100}%`,
                      width: `${b.w * 100}%`,
                      height: `${b.h * 100}%`,
                      border: `2px solid ${color}`,
                      background: `${color}1a`,
                    }}
                  >
                    <div
                      style={{
                        position: 'absolute',
                        top: -22,
                        left: -2,
                        background: color,
                        color: '#fff',
                        padding: '2px 7px',
                        fontFamily: C.fontMono,
                        fontSize: 11,
                        fontWeight: 600,
                        borderRadius: 3,
                      }}
                    >
                      {className}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      )}
    </>
  )
}

function Filmstrip({
  filtered,
  idx,
  setIdx,
  seed: _seed,
}: {
  filtered: DatasetItem[]
  idx: number
  setIdx: (n: number) => void
  seed: number
}) {
  const ref = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const child = el.children[idx] as HTMLElement | undefined
    if (child) child.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' })
  }, [idx])

  const total = filtered.length
  const pct = total > 1 ? (idx / (total - 1)) * 100 : 0
  // scrubber click → jump idx
  const scrubberRef = useRef<HTMLDivElement | null>(null)
  const onScrubberClick = (e: React.MouseEvent) => {
    if (!scrubberRef.current || total === 0) return
    const r = scrubberRef.current.getBoundingClientRect()
    const p = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width))
    setIdx(Math.round(p * (total - 1)))
  }

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 4,
        padding: '6px 8px',
        borderRadius: 6,
        background: '#fafaf7',
        border: `1px solid ${C.borderSoft}`,
        minWidth: 0,
      }}
    >
      {/* scrubber: 全集位置指示 + 点击跳转 */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          fontSize: 10,
          color: C.ink3,
          fontFamily: C.fontMono,
        }}
      >
        <span style={{ fontWeight: 600, color: C.ink2, minWidth: 64 }}>
          FILMSTRIP
        </span>
        <span style={{ minWidth: 60 }}>
          {idx + 1}/{total}
        </span>
        <div
          ref={scrubberRef}
          onMouseDown={onScrubberClick}
          title={`点击跳转 · 当前 ${idx + 1}/${total}`}
          style={{
            flex: 1,
            height: 14,
            position: 'relative',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
          }}
        >
          {/* track */}
          <div
            style={{
              position: 'absolute',
              left: 0,
              right: 0,
              top: 6,
              height: 2,
              background: C.border,
              borderRadius: 1,
            }}
          />
          {/* fill (已访问) */}
          <div
            style={{
              position: 'absolute',
              left: 0,
              top: 6,
              height: 2,
              width: `${pct}%`,
              background: C.ink2,
              borderRadius: 1,
            }}
          />
          {/* thumb */}
          <div
            style={{
              position: 'absolute',
              left: `calc(${pct}% - 5px)`,
              top: 2,
              width: 10,
              height: 10,
              borderRadius: '50%',
              background: '#fff',
              border: `1.5px solid ${C.ink}`,
              boxShadow: '0 1px 2px rgba(0,0,0,.2)',
              pointerEvents: 'none',
            }}
          />
        </div>
        <button
          type="button"
          onClick={() => {
            const v = prompt(`跳转到 (1-${total}):`, String(idx + 1))
            if (!v) return
            const n = parseInt(v.trim(), 10)
            if (!isNaN(n) && n >= 1 && n <= total) setIdx(n - 1)
          }}
          title="精确跳转"
          style={{
            padding: '2px 7px',
            borderRadius: 3,
            fontSize: 10,
            color: C.ink3,
            background: 'transparent',
            border: `1px solid ${C.border}`,
            cursor: 'pointer',
            fontFamily: 'inherit',
            flexShrink: 0,
          }}
        >
          跳转
        </button>
        <span
          style={{
            display: 'inline-flex',
            gap: 8,
            color: C.ink4,
            flexShrink: 0,
          }}
        >
          <span>
            <span
              style={{
                display: 'inline-block',
                width: 6,
                height: 6,
                borderRadius: '50%',
                background: C.warn,
                marginRight: 4,
              }}
            />
            待标
          </span>
          <span>
            <span
              style={{
                display: 'inline-block',
                width: 6,
                height: 6,
                borderRadius: '50%',
                background: C.live,
                marginRight: 4,
              }}
            />
            已标
          </span>
        </span>
      </div>
      {/* thumb row — 全部 thumb, 浏览器 lazy load 视口内的图 */}
      <div
        ref={ref}
        className="gb-scroll"
        style={{
          flex: 1,
          minWidth: 0,
          overflow: 'auto hidden',
          display: 'flex',
          gap: 4,
          paddingBottom: 2,
          scrollbarWidth: 'thin',
        }}
      >
        {filtered.map((d, i) => {
          const on = i === idx
          const dim = i < idx
          return (
            <button
              key={d.name}
              type="button"
              onClick={() => setIdx(i)}
              title={`${i + 1}. ${d.name} · ${d.labeled ? '已标' : d.skipped ? '跳过' : '待标'}`}
              style={{
                flex: '0 0 auto',
                width: 56,
                height: 36,
                borderRadius: 3,
                padding: 0,
                cursor: 'pointer',
                border: on ? `2px solid ${C.ink}` : `1px solid ${C.border}`,
                background: '#0a0a08',
                position: 'relative',
                overflow: 'hidden',
                opacity: dim ? 0.55 : 1,
                outline: 'none',
              }}
            >
              <img
                src={datasetImgSrc(d.name)}
                alt=""
                loading="lazy"
                decoding="async"
                style={{
                  position: 'absolute',
                  inset: 0,
                  width: '100%',
                  height: '100%',
                  objectFit: 'cover',
                }}
              />
              <span
                style={{
                  position: 'absolute',
                  top: 2,
                  right: 2,
                  width: 5,
                  height: 5,
                  borderRadius: '50%',
                  background: d.labeled ? C.live : d.skipped ? C.ink4 : C.warn,
                }}
              />
            </button>
          )
        })}
      </div>
    </div>
  )
}

const kbdSty: React.CSSProperties = {
  fontFamily: '"IBM Plex Mono", monospace',
  fontSize: 10,
  padding: '1px 5px',
  background: '#f1efe9',
  border: '1px solid #e7e3da',
  borderRadius: 3,
  color: '#4a463e',
  lineHeight: 1.4,
}

const kbdMini: React.CSSProperties = {
  fontFamily: '"IBM Plex Mono", monospace',
  fontSize: 9.5,
  padding: '0 4px',
  marginLeft: 5,
  background: 'rgba(255,255,255,.4)',
  border: '1px solid rgba(0,0,0,.15)',
  borderRadius: 3,
  color: 'inherit',
  opacity: 0.8,
  lineHeight: '14px',
  display: 'inline-block',
  verticalAlign: 'middle',
}

const btnGhost: React.CSSProperties = {
  padding: '4px 10px',
  borderRadius: 5,
  fontSize: 11.5,
  color: '#4a463e',
  background: '#ffffff',
  border: '1px solid #e7e3da',
  cursor: 'pointer',
  fontFamily: 'inherit',
  flexShrink: 0,
  display: 'inline-flex',
  alignItems: 'center',
}

// ─── 04 Tester ──────────────────────────────────────────────────────────
function TesterPanel({ info }: { info: YoloInfo | null }) {
  const emulators = useAppStore((s) => s.emulators)
  const running = useMemo(() => emulators.filter((e) => e.running), [emulators])
  const [instanceIdx, setInstanceIdx] = useState<number | null>(null)
  const [conf, setConf] = useState(0.4)
  const [enabledClasses, setEnabledClasses] = useState<Set<number>>(new Set())
  const [result, setResult] = useState<YoloTestResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    if (info) setEnabledClasses(new Set(info.classes.map((_, i) => i)))
  }, [info])

  useEffect(() => {
    if (instanceIdx == null && running.length > 0) {
      setInstanceIdx(running[0].index)
    }
  }, [running, instanceIdx])

  const toggleClass = (id: number) =>
    setEnabledClasses((prev) => {
      const n = new Set(prev)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })

  const runTest = async () => {
    if (instanceIdx == null) {
      setErr('选实例')
      return
    }
    setBusy(true)
    setErr(null)
    try {
      const r = await runYoloTest({
        instance: instanceIdx,
        conf_thr: conf,
        classes: info ? info.classes.filter((_, i) => enabledClasses.has(i)) : undefined,
      })
      setResult(r)
    } catch (e) {
      setErr(`测试失败: ${String(e).slice(0, 120)}`)
    } finally {
      setBusy(false)
    }
  }

  const visibleDets = result
    ? result.detections.filter((d) => enabledClasses.has(d.class_id))
    : []

  return (
    <div
      style={{
        height: '100%',
        display: 'grid',
        gridTemplateColumns: '300px 1fr',
        minHeight: 0,
      }}
    >
      <div
        style={{
          borderRight: `1px solid ${C.border}`,
          background: C.surface,
          display: 'flex',
          flexDirection: 'column',
          minHeight: 0,
        }}
      >
        <div style={{ padding: 14, borderBottom: `1px solid ${C.borderSoft}` }}>
          <Label>测试源</Label>
          <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
            {running.length === 0 && (
              <span style={{ fontSize: 11, color: C.ink4 }}>没运行的实例</span>
            )}
            {running.map((e) => {
              const on = instanceIdx === e.index
              return (
                <button
                  key={e.index}
                  type="button"
                  onClick={() => setInstanceIdx(e.index)}
                  style={{
                    padding: '3px 8px',
                    borderRadius: 4,
                    fontSize: 11,
                    fontFamily: C.fontMono,
                    fontWeight: 600,
                    color: on ? C.ink : C.ink3,
                    background: on ? C.surface2 : 'transparent',
                    border: `1px solid ${on ? C.border : C.borderSoft}`,
                    cursor: 'pointer',
                  }}
                >
                  #{e.index}
                </button>
              )
            })}
          </div>
          <button
            type="button"
            onClick={runTest}
            disabled={busy || instanceIdx == null}
            style={{
              marginTop: 10,
              width: '100%',
              padding: '7px 0',
              borderRadius: 5,
              fontSize: 12,
              fontWeight: 600,
              color: instanceIdx != null ? '#f0eee9' : C.ink4,
              background: instanceIdx != null ? C.ink : C.surface2,
              border: `1px solid ${instanceIdx != null ? C.ink : C.border}`,
              cursor: busy || instanceIdx == null ? 'not-allowed' : 'pointer',
              fontFamily: 'inherit',
            }}
          >
            {busy ? '测试中…' : '跑 YOLO'}
          </button>
          {err && (
            <div
              className="gb-mono"
              style={{ marginTop: 8, fontSize: 11, color: C.error }}
            >
              {err}
            </div>
          )}
        </div>
        <div style={{ padding: 14, borderBottom: `1px solid ${C.borderSoft}` }}>
          <Label>
            置信度阈值{' '}
            <span className="gb-mono" style={{ color: C.ink }}>
              {conf.toFixed(2)}
            </span>
          </Label>
          <div style={{ marginTop: 6 }}>
            <GbSlider
              value={conf}
              onChange={setConf}
              min={0.1}
              max={0.95}
              step={0.01}
            />
          </div>
        </div>
        <div
          style={{
            padding: 14,
            borderBottom: `1px solid ${C.borderSoft}`,
            flex: 1,
            minHeight: 0,
            display: 'flex',
            flexDirection: 'column',
          }}
        >
          <Label>启用类别</Label>
          <div
            className="gb-scroll"
            style={{ flex: 1, minHeight: 0, overflow: 'hidden auto', marginTop: 6 }}
          >
            {info?.classes.map((name, id) => (
              <label
                key={id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  padding: '4px 0',
                  cursor: 'pointer',
                }}
              >
                <input
                  type="checkbox"
                  checked={enabledClasses.has(id)}
                  onChange={() => toggleClass(id)}
                />
                <span
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: 2,
                    background: CLASS_COLOR[id] || '#525252',
                  }}
                />
                <span
                  className="gb-mono"
                  style={{ fontSize: 11.5, color: C.ink2 }}
                >
                  {name}
                </span>
              </label>
            ))}
          </div>
        </div>
      </div>

      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          minHeight: 0,
          background: C.bg,
        }}
      >
        <div style={{ padding: 16, flex: 1, minHeight: 0, display: 'flex' }}>
          <div
            style={{
              flex: 1,
              position: 'relative',
              background: '#0a0a08',
              borderRadius: 7,
              overflow: 'hidden',
              border: `1px solid ${C.border}`,
            }}
          >
            {result?.annotated_b64 ? (
              <img
                src={result.annotated_b64}
                alt=""
                style={{
                  width: '100%',
                  height: '100%',
                  objectFit: 'contain',
                  display: 'block',
                }}
              />
            ) : (
              <div
                style={{
                  position: 'absolute',
                  inset: 0,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: 'rgba(255,255,255,.5)',
                  fontSize: 12,
                  fontFamily: C.fontMono,
                }}
              >
                {busy ? '检测中…' : '点击「跑 YOLO」开始检测'}
              </div>
            )}
            {result && (
              <div
                style={{
                  position: 'absolute',
                  top: 10,
                  right: 10,
                  padding: '4px 9px',
                  background: 'rgba(20,20,18,.78)',
                  backdropFilter: 'blur(6px)',
                  borderRadius: 4,
                  fontFamily: C.fontMono,
                  fontSize: 10.5,
                  color: 'rgba(255,255,255,.78)',
                }}
              >
                {visibleDets.length} 个检测 · {result.duration_ms}ms
              </div>
            )}
          </div>
        </div>

        {result && (
          <div
            style={{
              background: C.surface,
              borderTop: `1px solid ${C.border}`,
              maxHeight: 220,
            }}
          >
            <div
              className="gb-scroll"
              style={{ overflow: 'hidden auto', maxHeight: 220 }}
            >
              <table
                style={{
                  width: '100%',
                  fontSize: 11.5,
                  borderCollapse: 'collapse',
                  fontFamily: C.fontMono,
                }}
              >
                <thead>
                  <tr style={{ background: C.surface2 }}>
                    {['#', '类别', '得分', 'bbox', '中心点'].map((h, i) => (
                      <th
                        key={i}
                        style={{
                          textAlign: 'left',
                          padding: '6px 14px',
                          fontWeight: 600,
                          color: C.ink3,
                          fontSize: 10.5,
                          letterSpacing: '.04em',
                          textTransform: 'uppercase',
                          borderBottom: `1px solid ${C.border}`,
                        }}
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {visibleDets.map((d: YoloDet, i: number) => (
                    <tr
                      key={i}
                      style={{ borderBottom: `1px solid ${C.borderSoft}` }}
                    >
                      <td style={{ padding: '6px 14px', color: C.ink4 }}>{i + 1}</td>
                      <td style={{ padding: '6px 14px' }}>
                        <span
                          style={{
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: 6,
                          }}
                        >
                          <span
                            style={{
                              width: 8,
                              height: 8,
                              borderRadius: 2,
                              background: CLASS_COLOR[d.class_id] || '#525252',
                            }}
                          />
                          <span style={{ color: C.ink }}>{d.name}</span>
                        </span>
                      </td>
                      <td style={{ padding: '6px 14px', color: C.ink2 }}>
                        {d.score.toFixed(3)}
                      </td>
                      <td style={{ padding: '6px 14px', color: C.ink3 }}>
                        [{d.x1}, {d.y1}, {d.x2}, {d.y2}]
                      </td>
                      <td style={{ padding: '6px 14px', color: C.ink3 }}>
                        ({d.cx}, {d.cy})
                      </td>
                    </tr>
                  ))}
                  {visibleDets.length === 0 && (
                    <tr>
                      <td
                        colSpan={5}
                        style={{
                          padding: 18,
                          textAlign: 'center',
                          color: C.ink4,
                        }}
                      >
                        当前阈值 + 类别筛选下没有检测
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ─── 05 Model ───────────────────────────────────────────────────────────
function ModelPanel({
  info,
  dataset,
  onUploaded,
}: {
  info: YoloInfo | null
  dataset: DatasetList | null
  onUploaded: () => Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const handleUpload = async (file: File) => {
    setBusy(true)
    setErr(null)
    try {
      await uploadModel(file)
      await onUploaded()
    } catch (e) {
      setErr(`上传失败: ${String(e).slice(0, 200)}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      style={{
        padding: 22,
        display: 'grid',
        gridTemplateColumns: '1fr 360px',
        gap: 22,
        minHeight: 0,
        overflow: 'auto',
      }}
      className="gb-scroll"
    >
      <div
        style={{
          background: C.surface,
          borderRadius: 8,
          border: `1px solid ${C.border}`,
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            padding: '14px 18px',
            borderBottom: `1px solid ${C.borderSoft}`,
            display: 'flex',
            alignItems: 'center',
            gap: 10,
          }}
        >
          <span style={{ fontSize: 13, fontWeight: 600 }}>当前模型</span>
          {info?.available ? (
            <span
              style={{
                fontFamily: C.fontMono,
                fontSize: 10,
                color: '#15803d',
                background: C.liveSoft,
                padding: '1px 6px',
                borderRadius: 3,
                fontWeight: 600,
              }}
            >
              LOADED
            </span>
          ) : (
            <span
              style={{
                fontFamily: C.fontMono,
                fontSize: 10,
                color: C.error,
                background: C.errorSoft,
                padding: '1px 6px',
                borderRadius: 3,
                fontWeight: 600,
              }}
            >
              UNAVAILABLE
            </span>
          )}
        </div>
        <div
          style={{
            padding: '16px 18px',
            borderBottom: `1px solid ${C.borderSoft}`,
          }}
        >
          <div className="gb-mono" style={{ fontSize: 14, fontWeight: 600 }}>
            {info?.model_path || '(未加载)'}
          </div>
          <div
            style={{
              fontSize: 11.5,
              color: C.ink3,
              marginTop: 6,
              display: 'flex',
              gap: 14,
              flexWrap: 'wrap',
            }}
          >
            <span>
              输入分辨率{' '}
              <span className="gb-mono">{info?.input_size || '?'}</span>
            </span>
            <span>
              · <span className="gb-mono">{info?.classes.length || 0}</span> 类
            </span>
          </div>
          {info?.error && (
            <div
              className="gb-mono"
              style={{
                marginTop: 8,
                fontSize: 11,
                color: C.error,
                wordBreak: 'break-word',
              }}
            >
              {info.error}
            </div>
          )}
        </div>
        {info && info.classes.length > 0 && (
          <div style={{ padding: '16px 18px' }}>
            <div
              style={{
                fontSize: 11,
                color: C.ink3,
                fontWeight: 500,
                letterSpacing: '.06em',
                textTransform: 'uppercase',
                marginBottom: 10,
              }}
            >
              类别 ({info.classes.length})
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {info.classes.map((name, id) => (
                <span
                  key={name}
                  className="gb-mono"
                  style={{
                    padding: '3px 8px',
                    fontSize: 11,
                    fontWeight: 500,
                    background: C.surface2,
                    border: `1px solid ${C.border}`,
                    borderRadius: 4,
                    color: C.ink2,
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 5,
                  }}
                >
                  <span
                    style={{
                      width: 8,
                      height: 8,
                      borderRadius: 2,
                      background: CLASS_COLOR[id] || '#525252',
                    }}
                  />
                  {name}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div
          style={{
            background: C.surface,
            borderRadius: 8,
            border: `1px solid ${C.border}`,
            padding: 16,
          }}
        >
          <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 10 }}>
            上传新模型
          </div>
          <label
            style={{
              padding: 24,
              textAlign: 'center',
              borderRadius: 6,
              border: `1.5px dashed ${C.border}`,
              background: C.surface2,
              fontSize: 12,
              color: C.ink3,
              cursor: 'pointer',
              display: 'block',
            }}
          >
            {busy ? '上传中…' : (
              <>
                点击选择{' '}
                <span
                  className="gb-mono"
                  style={{
                    padding: '1px 5px',
                    background: C.surface,
                    borderRadius: 3,
                  }}
                >
                  .onnx
                </span>{' '}
                文件
              </>
            )}
            <input
              type="file"
              accept=".onnx"
              style={{ display: 'none' }}
              onChange={(e) => {
                const f = e.target.files?.[0]
                if (f) handleUpload(f)
              }}
            />
          </label>
          <div style={{ fontSize: 11, color: C.ink4, marginTop: 8 }}>
            上传成功后会立即生效, 无历史归档
          </div>
          {err && (
            <div
              className="gb-mono"
              style={{
                marginTop: 8,
                fontSize: 11,
                color: C.error,
                wordBreak: 'break-word',
              }}
            >
              {err}
            </div>
          )}
        </div>

        {dataset && (
          <div
            style={{
              background: C.surface,
              borderRadius: 8,
              border: `1px solid ${C.border}`,
              padding: 16,
            }}
          >
            <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 10 }}>
              训练数据
            </div>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: '1fr 1fr',
                gap: 12,
              }}
            >
              <Kpi label="图片总数" value={dataset.total} mono />
              <Kpi
                label="已标注"
                value={dataset.labeled}
                sub={
                  dataset.total > 0
                    ? `${((dataset.labeled / dataset.total) * 100).toFixed(0)}%`
                    : '0%'
                }
                mono
                color={C.live}
              />
            </div>
            <div style={{ marginTop: 12, fontSize: 11, color: C.ink3 }}>
              训练流程: 数据集 → 导出 ZIP → 离线 yolov8 训练 → 上传 .onnx
            </div>
            <a
              href={exportZipUrl()}
              download="dataset.zip"
              style={{
                display: 'block',
                marginTop: 12,
                padding: '7px 0',
                borderRadius: 5,
                fontSize: 12,
                fontWeight: 600,
                textAlign: 'center',
                color: C.ink2,
                background: C.surface,
                border: `1px solid ${C.border}`,
                textDecoration: 'none',
              }}
            >
              导出 ZIP
            </a>
          </div>
        )}
      </div>
    </div>
  )
}

// ─── shared ─────────────────────────────────────────────────────────────
function Kpi({
  label,
  value,
  sub,
  mono,
  color,
}: {
  label: string
  value: number | string
  sub?: string
  mono?: boolean
  color?: string
}) {
  return (
    <div>
      <div
        style={{
          fontSize: 9.5,
          color: C.ink3,
          fontWeight: 500,
          letterSpacing: '.06em',
          textTransform: 'uppercase',
        }}
      >
        {label}
      </div>
      <div
        className={mono ? 'gb-mono' : ''}
        style={{
          marginTop: 3,
          fontSize: 18,
          fontWeight: 600,
          color: color || C.ink,
          display: 'flex',
          alignItems: 'baseline',
          gap: 4,
          lineHeight: 1.1,
        }}
      >
        {value}
        {sub && (
          <span style={{ fontSize: 11, color: C.ink3, fontWeight: 400 }}>
            {sub}
          </span>
        )}
      </div>
    </div>
  )
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        fontSize: 11,
        color: C.ink3,
        fontWeight: 500,
        letterSpacing: '.06em',
        textTransform: 'uppercase',
        marginBottom: 4,
      }}
    >
      {children}
    </div>
  )
}

function Segmented({
  value,
  onChange,
  options,
}: {
  value: string
  onChange: (v: string) => void
  options: Array<[string, string]>
}) {
  return (
    <div
      style={{
        display: 'inline-flex',
        background: C.surface2,
        padding: 2,
        borderRadius: 5,
        gap: 1,
        flexShrink: 0,
      }}
    >
      {options.map(([k, l]) => {
        const on = value === k
        return (
          <button
            key={k}
            type="button"
            onClick={() => onChange(k)}
            style={{
              padding: '4px 10px',
              borderRadius: 3,
              fontSize: 11.5,
              fontWeight: on ? 600 : 500,
              color: on ? C.ink : C.ink3,
              background: on ? C.surface : 'transparent',
              border: 'none',
              cursor: 'pointer',
              fontFamily: 'inherit',
              whiteSpace: 'nowrap',
              flexShrink: 0,
            }}
          >
            {l}
          </button>
        )
      })}
    </div>
  )
}

function ClassBars({ classes }: { classes: PerClassStat[] }) {
  if (classes.length === 0)
    return (
      <div style={{ fontSize: 11, color: C.ink4 }}>—</div>
    )
  const max = Math.max(...classes.map((c) => c.instances), 1)
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'flex-end',
        gap: 4,
        height: 32,
      }}
    >
      {classes.map((c) => (
        <div
          key={c.id}
          title={`${c.name}: ${c.instances}`}
          style={{
            flex: 1,
            height: `${(c.instances / max) * 100}%`,
            background: CLASS_COLOR[c.id] || '#525252',
            borderRadius: '2px 2px 0 0',
            minHeight: 3,
          }}
        />
      ))}
    </div>
  )
}

function fmtBytes(n: number) {
  if (n < 1024) return n + ' B'
  if (n < 1024 * 1024) return (n / 1024).toFixed(0) + ' KB'
  return (n / 1024 / 1024).toFixed(1) + ' MB'
}
function fmtAgo(ts: number) {
  const m = Math.floor((Date.now() - ts) / 60000)
  if (m < 60) return m + ' 分前'
  if (m < 1440) return Math.floor(m / 60) + ' 时前'
  return Math.floor(m / 1440) + ' 天前'
}

function LoadingBox({ label }: { label: string }) {
  return (
    <div
      style={{
        flex: 1,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        color: C.ink4,
        fontSize: 13,
      }}
    >
      {label}
    </div>
  )
}
