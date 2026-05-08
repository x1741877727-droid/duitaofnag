/**
 * OcrView — OCR / ROI 子页 (左 ROI 列表 / 中 canvas + RecogRectEditor / 右 crop preview + OCR 结果).
 *
 * 真实数据源:
 *   /api/roi/list      — ROI yaml 持久化的列表
 *   /api/roi/save      — 保存 (新增/更新)
 *   /api/roi/test_ocr  — 抓帧 + 跑 OCR (返回 full_image_b64 + cropped_image_b64 + ocr_results)
 *
 * 注: 后端 Preprocessing 类型只有 5 个 (grayscale/clahe/binarize/sharpen/invert),
 *     设计原型的 'edge' 后端不支持, 故移除.
 */

import { useEffect, useMemo, useState } from 'react'
import { C } from '@/lib/design-tokens'
import { useAppStore } from '@/lib/store'
import {
  fetchRoiList,
  saveRoi,
  testRoiOcr,
  type RoiItem,
  type Preprocessing,
  type OcrHit,
} from '@/lib/roiApi'
import { RecogRectEditor } from '../canvas/RecogRectEditor'

// 后端只接受 5 个 (无 edge)
const OCR_PREPROC: Preprocessing[] = ['grayscale', 'clahe', 'binarize', 'sharpen', 'invert']
const PREPROC_LABEL: Record<Preprocessing, string> = {
  grayscale: '灰度',
  clahe: 'CLAHE',
  binarize: '二值化',
  sharpen: '锐化',
  invert: '反色',
}

type SrcKind = 'instance' | 'decision'
interface Src {
  kind: SrcKind
  instance?: number
  decision_id?: string
  session?: string
}

export function OcrView() {
  const emulators = useAppStore((s) => s.emulators)
  const runningInstances = useMemo(
    () => emulators.filter((e) => e.running),
    [emulators],
  )

  const [items, setItems] = useState<RoiItem[]>([])
  const [yamlPath, setYamlPath] = useState('')
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const [selectedIdx, setSelectedIdx] = useState(0)

  // local edits per ROI (drift from server until 保存)
  const [editRect, setEditRect] = useState<[number, number, number, number]>([0.4, 0.4, 0.6, 0.5])
  const [editScale, setEditScale] = useState(2)
  const [editDesc, setEditDesc] = useState('')
  const [editPreproc, setEditPreproc] = useState<Preprocessing[]>([])

  const [src, setSrc] = useState<Src>({ kind: 'instance', instance: undefined })
  const [fullImg, setFullImg] = useState('')
  const [croppedImg, setCroppedImg] = useState('')
  const [imgSize, setImgSize] = useState<[number, number]>([1280, 720])
  const [ocrHits, setOcrHits] = useState<OcrHit[]>([])
  const [busy, setBusy] = useState(false)
  const [hint, setHint] = useState('')
  const [showAll, setShowAll] = useState(false)
  const [zoom, setZoom] = useState(false)

  const cur = items[selectedIdx]

  // initial load
  const reload = async () => {
    setLoadErr(null)
    try {
      const r = await fetchRoiList()
      setItems(r.items)
      setYamlPath(r.yaml_path)
    } catch (e) {
      setLoadErr(String(e))
    }
  }
  useEffect(() => {
    reload()
  }, [])

  // when server list updates: pick first; reset edits to current cur
  useEffect(() => {
    if (!cur) return
    setEditRect(cur.rect)
    setEditScale(cur.scale)
    setEditDesc(cur.desc)
    setEditPreproc(
      (cur.preprocessing || []).filter((p): p is Preprocessing =>
        OCR_PREPROC.includes(p as Preprocessing),
      ),
    )
    setOcrHits([])
    setCroppedImg('')
    setHint('')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedIdx, cur?.name])

  // default source: first running instance
  useEffect(() => {
    if (src.instance == null && runningInstances.length > 0) {
      setSrc({ kind: 'instance', instance: runningInstances[0].index })
    }
  }, [runningInstances, src.instance])

  const togglePreproc = (key: Preprocessing) => {
    setEditPreproc((cur) =>
      cur.includes(key) ? cur.filter((p) => p !== key) : [...cur, key],
    )
  }

  const dirty =
    cur != null &&
    (JSON.stringify(editRect) !== JSON.stringify(cur.rect) ||
      editScale !== cur.scale ||
      editDesc !== cur.desc ||
      JSON.stringify(editPreproc) !==
        JSON.stringify(
          (cur.preprocessing || []).filter((p) => OCR_PREPROC.includes(p as Preprocessing)),
        ))

  const runOcr = async () => {
    // src.instance === 0 时 !0 是 true → 误判没选, 用 == null 避坑
    if (src.instance == null && !src.decision_id) {
      setHint('先选实例 (没有运行中的实例)')
      return
    }
    setBusy(true)
    setHint('抓帧 + OCR …')
    try {
      const r = await testRoiOcr({
        instance: src.kind === 'instance' ? src.instance : undefined,
        decision_id: src.kind === 'decision' ? src.decision_id : undefined,
        session: src.kind === 'decision' ? src.session : undefined,
        rect: editRect,
        scale: editScale,
        preprocessing: editPreproc.length ? editPreproc : undefined,
      })
      setFullImg(r.full_image_b64)
      setCroppedImg(r.cropped_image_b64)
      setImgSize(r.source_size)
      setOcrHits(r.ocr_results)
      setHint(`OK · ${r.source_size[0]}×${r.source_size[1]} · ${r.n_texts} 个文字`)
    } catch (e) {
      setHint(`失败: ${String(e).slice(0, 120)}`)
    } finally {
      setBusy(false)
    }
  }

  const doSave = async () => {
    if (!cur) return
    setBusy(true)
    setHint('保存中…')
    try {
      const r = await saveRoi({
        name: cur.name,
        rect: editRect,
        scale: editScale,
        desc: editDesc,
        preprocessing: editPreproc,
      })
      setHint(`已保存, 备份 ${r.backup}`)
      await reload()
    } catch (e) {
      setHint(`保存失败: ${String(e).slice(0, 120)}`)
    } finally {
      setBusy(false)
    }
  }

  const reset = () => {
    if (!cur) return
    setEditRect(cur.rect)
    setEditScale(cur.scale)
    setEditDesc(cur.desc)
    setEditPreproc(
      (cur.preprocessing || []).filter((p): p is Preprocessing =>
        OCR_PREPROC.includes(p as Preprocessing),
      ),
    )
  }

  const newRoi = async () => {
    const name = prompt('新 ROI 名 (例 my_target):')
    if (!name) return
    if (items.some((it) => it.name === name)) {
      setHint('名字已存在')
      return
    }
    setBusy(true)
    try {
      await saveRoi({
        name,
        rect: [0.45, 0.45, 0.55, 0.52],
        scale: 2,
        desc: '',
        preprocessing: [],
      })
      await reload()
      const newIdx = items.findIndex((it) => it.name === name)
      if (newIdx >= 0) setSelectedIdx(newIdx)
      setHint(`已创建 ${name}`)
    } catch (e) {
      setHint(`创建失败: ${String(e).slice(0, 120)}`)
    } finally {
      setBusy(false)
    }
  }

  if (loadErr) {
    return (
      <div
        style={{
          flex: 1,
          padding: 30,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <div
          style={{
            background: C.surface,
            border: `1px solid ${C.error}30`,
            borderRadius: 8,
            padding: '16px 20px',
            maxWidth: 480,
          }}
        >
          <div style={{ fontSize: 13, fontWeight: 600, color: C.error, marginBottom: 6 }}>
            无法加载 ROI 列表
          </div>
          <div
            className="gb-mono"
            style={{ fontSize: 11.5, color: C.ink3, wordBreak: 'break-word' }}
          >
            {loadErr}
          </div>
          <div style={{ fontSize: 11, color: C.ink4, marginTop: 6 }}>
            确认后端 /api/roi/list 在线
          </div>
        </div>
      </div>
    )
  }

  if (items.length === 0) {
    return (
      <div
        style={{
          flex: 1,
          padding: 30,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          flexDirection: 'column',
          gap: 10,
        }}
      >
        <div style={{ fontSize: 13, color: C.ink3 }}>加载 ROI 列表中…</div>
        <div className="gb-mono" style={{ fontSize: 11, color: C.ink4 }}>
          {yamlPath || '/api/roi/list'}
        </div>
      </div>
    )
  }

  return (
    <>
      {zoom && cur && (
        <ZoomModal
          cur={cur}
          editRect={editRect}
          onChange={setEditRect}
          fullImg={fullImg}
          onClose={() => setZoom(false)}
        />
      )}
      <div
        style={{
          flex: 1,
          minHeight: 0,
          display: 'grid',
          gridTemplateColumns: '240px 1fr 320px',
          background: C.bg,
        }}
      >
        {/* ROI list */}
        <div
          style={{
            background: C.surface,
            borderRight: `1px solid ${C.border}`,
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
            <span style={{ fontSize: 12, fontWeight: 600 }}>ROI 列表</span>
            <span className="gb-mono" style={{ fontSize: 10.5, color: C.ink4 }}>
              {items.length}
            </span>
            <span style={{ flex: 1 }} />
            <button
              type="button"
              onClick={newRoi}
              style={{
                padding: '3px 8px',
                borderRadius: 4,
                fontSize: 11,
                color: C.ink2,
                background: C.surface2,
                border: `1px solid ${C.border}`,
                cursor: 'pointer',
                fontFamily: 'inherit',
              }}
            >
              + 新建
            </button>
          </div>
          <label
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              padding: '8px 14px',
              borderBottom: `1px solid ${C.borderSoft}`,
              fontSize: 11,
              color: C.ink2,
              cursor: 'pointer',
            }}
          >
            <input
              type="checkbox"
              checked={showAll}
              onChange={(e) => setShowAll(e.target.checked)}
            />
            显示全部 ROI 框
          </label>
          <div
            className="gb-scroll"
            style={{ flex: 1, minHeight: 0, overflow: 'hidden auto' }}
          >
            {items.map((roi, i) => {
              const on = i === selectedIdx
              return (
                <button
                  key={roi.name}
                  type="button"
                  onClick={() => setSelectedIdx(i)}
                  style={{
                    display: 'block',
                    width: '100%',
                    textAlign: 'left',
                    padding: '9px 14px',
                    background: on ? C.surface2 : 'transparent',
                    borderTop: 'none',
                    borderRight: 'none',
                    borderBottom: `1px solid ${C.borderSoft}`,
                    borderLeft: `3px solid ${on ? C.ink : 'transparent'}`,
                    cursor: 'pointer',
                    fontFamily: 'inherit',
                  }}
                >
                  <div
                    className="gb-mono"
                    style={{
                      fontSize: 12,
                      fontWeight: on ? 600 : 500,
                      color: on ? C.ink : C.ink2,
                    }}
                  >
                    {roi.name}
                  </div>
                  <div style={{ fontSize: 10.5, color: C.ink4, marginTop: 2 }}>
                    {roi.desc || '—'}
                  </div>
                  <div style={{ marginTop: 4, display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                    {(roi.preprocessing || []).map((p) => (
                      <span
                        key={p}
                        className="gb-mono"
                        style={{
                          fontSize: 9,
                          color: C.ink3,
                          padding: '0 4px',
                          borderRadius: 2,
                          background: C.surface,
                        }}
                      >
                        {p}
                      </span>
                    ))}
                  </div>
                </button>
              )
            })}
          </div>
        </div>

        {/* canvas + preproc */}
        <div
          style={{
            padding: 16,
            display: 'flex',
            flexDirection: 'column',
            gap: 12,
            minHeight: 0,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 12, color: C.ink2 }}>测试源</span>
            <div
              style={{
                display: 'flex',
                background: C.surface2,
                padding: 2,
                borderRadius: 5,
                gap: 1,
              }}
            >
              {(['instance', 'decision'] as const).map((k) => {
                const on = src.kind === k
                return (
                  <button
                    key={k}
                    type="button"
                    onClick={() =>
                      setSrc((p) => ({ ...p, kind: k }))
                    }
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
                    }}
                  >
                    {k === 'instance' ? '实例' : '决策'}
                  </button>
                )
              })}
            </div>
            {src.kind === 'instance' ? (
              <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                {runningInstances.length === 0 && (
                  <span style={{ fontSize: 11, color: C.ink4 }}>没运行的实例</span>
                )}
                {runningInstances.map((e) => {
                  const on = src.instance === e.index
                  return (
                    <button
                      key={e.index}
                      type="button"
                      onClick={() =>
                        setSrc({ kind: 'instance', instance: e.index })
                      }
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
            ) : (
              <input
                value={src.decision_id || ''}
                onChange={(e) =>
                  setSrc({ kind: 'decision', decision_id: e.target.value, session: src.session })
                }
                placeholder="决策 id (例 013757_826_inst0_P5_R1)"
                className="gb-mono"
                style={{
                  flex: 1,
                  maxWidth: 320,
                  padding: '4px 8px',
                  fontSize: 11.5,
                  borderRadius: 4,
                  background: C.surface2,
                  border: `1px solid ${C.border}`,
                  color: C.ink,
                  outline: 'none',
                  fontFamily: 'inherit',
                }}
              />
            )}
            <span style={{ flex: 1 }} />
            {cur && (
              <span className="gb-mono" style={{ fontSize: 11, color: C.ink3 }}>
                正在编辑:{' '}
                <span style={{ color: C.ink, fontWeight: 600 }}>{cur.name}</span>
              </span>
            )}
          </div>

          <div
            style={{
              flex: 1,
              minHeight: 0,
              position: 'relative',
              background: '#0a0a08',
              borderRadius: 7,
              overflow: 'hidden',
              border: `1px solid ${C.border}`,
              // 容器自身按图比例 (默认 16:9 = 960/540), 让 <img objectFit:cover> 全填,
              // 不留 letterbox → RecogRectEditor 跟 ocrHits 的 ${x*100}% 落到图上不落黑边
              aspectRatio: `${imgSize[0]} / ${imgSize[1]}`,
              maxHeight: '100%',
            }}
          >
            {fullImg ? (
              <img
                src={fullImg}
                alt=""
                style={{
                  position: 'absolute',
                  inset: 0,
                  width: '100%',
                  height: '100%',
                  objectFit: 'fill',
                  display: 'block',
                  background: '#000',
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
                {busy ? '抓帧中…' : '点击右上「跑 OCR」抓帧并显示'}
              </div>
            )}

            {showAll &&
              items.map((roi, i) => {
                if (i === selectedIdx) return null
                const [x1, y1, x2, y2] = roi.rect
                return (
                  <div
                    key={i}
                    style={{
                      position: 'absolute',
                      left: `${x1 * 100}%`,
                      top: `${y1 * 100}%`,
                      width: `${(x2 - x1) * 100}%`,
                      height: `${(y2 - y1) * 100}%`,
                      border: '1.5px dashed rgba(217,119,6,.6)',
                      background: 'rgba(217,119,6,.06)',
                      pointerEvents: 'none',
                    }}
                  >
                    <div
                      className="gb-mono"
                      style={{
                        position: 'absolute',
                        top: -16,
                        left: 0,
                        fontSize: 9,
                        background: 'rgba(20,20,18,.78)',
                        color: 'rgba(255,255,255,.7)',
                        padding: '0 4px',
                        borderRadius: 2,
                      }}
                    >
                      {roi.name}
                    </div>
                  </div>
                )
              })}

            {cur && (
              <RecogRectEditor
                rect={editRect}
                onChange={setEditRect}
                color="#d97757"
                label={cur.name}
                allowCreate={true}
              />
            )}

            {/* OCR 结果 bbox 叠在图上 (选中 ROI 内的命中) */}
            {ocrHits.map((h, i) => {
              const xs = h.box.map((p) => p[0])
              const ys = h.box.map((p) => p[1])
              const x1 = Math.min(...xs)
              const y1 = Math.min(...ys)
              const x2 = Math.max(...xs)
              const y2 = Math.max(...ys)
              const left = (x1 / imgSize[0]) * 100
              const top = (y1 / imgSize[1]) * 100
              const w = ((x2 - x1) / imgSize[0]) * 100
              const ht = ((y2 - y1) / imgSize[1]) * 100
              return (
                <div
                  key={i}
                  style={{
                    position: 'absolute',
                    left: `${left}%`,
                    top: `${top}%`,
                    width: `${w}%`,
                    height: `${ht}%`,
                    border: '1.5px solid #16a34a',
                    background: 'rgba(22,163,74,.10)',
                    pointerEvents: 'none',
                  }}
                />
              )
            })}

            <button
              type="button"
              onClick={() => setZoom(true)}
              disabled={!fullImg}
              style={{
                position: 'absolute',
                top: 10,
                right: 10,
                padding: '4px 9px',
                borderRadius: 4,
                background: 'rgba(20,20,18,.78)',
                backdropFilter: 'blur(6px)',
                color: 'rgba(255,255,255,.85)',
                fontFamily: C.fontMono,
                fontSize: 11,
                border: 'none',
                cursor: fullImg ? 'pointer' : 'not-allowed',
                opacity: fullImg ? 1 : 0.5,
              }}
            >
              放大调整
            </button>
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

          {cur && (
            <div
              style={{
                background: C.surface,
                borderRadius: 6,
                border: `1px solid ${C.border}`,
                padding: 12,
              }}
            >
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  marginBottom: 8,
                }}
              >
                <span
                  style={{
                    fontSize: 11,
                    color: C.ink3,
                    fontWeight: 500,
                    letterSpacing: '.06em',
                    textTransform: 'uppercase',
                  }}
                >
                  预处理流水线
                </span>
                <span className="gb-mono" style={{ fontSize: 10.5, color: C.ink4 }}>
                  ({editPreproc.length} 步)
                </span>
                <span style={{ flex: 1 }} />
                <span style={{ fontSize: 11, color: C.ink2 }}>scale</span>
                <select
                  value={editScale}
                  onChange={(e) => setEditScale(+e.target.value)}
                  className="gb-mono"
                  style={{
                    padding: '2px 6px',
                    fontSize: 11,
                    borderRadius: 4,
                    background: C.surface2,
                    border: `1px solid ${C.border}`,
                    color: C.ink,
                    outline: 'none',
                  }}
                >
                  {[1, 1.5, 2, 2.5, 3, 4].map((s) => (
                    <option key={s} value={s}>
                      ×{s}
                    </option>
                  ))}
                </select>
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {OCR_PREPROC.map((key) => {
                  const on = editPreproc.includes(key)
                  const order = on ? editPreproc.indexOf(key) + 1 : null
                  return (
                    <button
                      key={key}
                      type="button"
                      onClick={() => togglePreproc(key)}
                      style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: 5,
                        padding: '4px 9px',
                        borderRadius: 4,
                        fontSize: 11,
                        fontFamily: C.fontMono,
                        fontWeight: on ? 600 : 500,
                        color: on ? C.ink : C.ink3,
                        background: on ? '#fce7d4' : C.surface2,
                        border: `1px solid ${on ? '#d97757' : C.border}`,
                        cursor: 'pointer',
                      }}
                    >
                      {on && (
                        <span
                          style={{
                            width: 14,
                            height: 14,
                            borderRadius: '50%',
                            background: '#d97757',
                            color: '#fff',
                            fontSize: 9,
                            fontWeight: 700,
                            display: 'grid',
                            placeItems: 'center',
                          }}
                        >
                          {order}
                        </span>
                      )}
                      <PreprocGlyph kind={key} />
                      {PREPROC_LABEL[key]}
                    </button>
                  )
                })}
              </div>
            </div>
          )}
        </div>

        {/* result */}
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
            }}
          >
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
              裁切预览
            </div>
            {croppedImg ? (
              <>
                <img
                  src={croppedImg}
                  alt=""
                  style={{
                    maxWidth: '100%',
                    maxHeight: 160,
                    objectFit: 'contain',
                    display: 'block',
                    background: '#0a0a08',
                    borderRadius: 4,
                    border: `1px solid ${C.border}`,
                  }}
                />
                <div
                  className="gb-mono"
                  style={{ fontSize: 10, color: C.ink4, marginTop: 6 }}
                >
                  {Math.round(editRect[0] * imgSize[0])},{' '}
                  {Math.round(editRect[1] * imgSize[1])} →{' '}
                  {Math.round(editRect[2] * imgSize[0])},{' '}
                  {Math.round(editRect[3] * imgSize[1])} (
                  {Math.round((editRect[2] - editRect[0]) * imgSize[0])}×
                  {Math.round((editRect[3] - editRect[1]) * imgSize[1])})
                </div>
              </>
            ) : (
              <div
                style={{
                  fontSize: 11.5,
                  color: C.ink4,
                  padding: 14,
                  textAlign: 'center',
                  border: `1px dashed ${C.border}`,
                  borderRadius: 4,
                }}
              >
                跑过 OCR 后显示
              </div>
            )}
          </div>

          <div
            style={{
              padding: '12px 14px',
              borderBottom: `1px solid ${C.borderSoft}`,
            }}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                marginBottom: 8,
              }}
            >
              <span
                style={{
                  fontSize: 11,
                  color: C.ink3,
                  fontWeight: 500,
                  letterSpacing: '.06em',
                  textTransform: 'uppercase',
                }}
              >
                OCR 结果
              </span>
              <span className="gb-mono" style={{ fontSize: 10.5, color: C.ink4 }}>
                {ocrHits.length}
              </span>
              <span style={{ flex: 1 }} />
              <button
                type="button"
                onClick={runOcr}
                disabled={busy || (src.kind === 'instance' && src.instance == null)}
                style={{
                  padding: '4px 11px',
                  borderRadius: 4,
                  fontSize: 11.5,
                  fontWeight: 600,
                  color: '#f0eee9',
                  background: C.ink,
                  border: 'none',
                  cursor: busy ? 'wait' : 'pointer',
                  fontFamily: 'inherit',
                  opacity: busy ? 0.7 : 1,
                }}
              >
                {busy ? '跑中…' : '跑 OCR'}
              </button>
            </div>
            {ocrHits.length === 0 ? (
              <div
                style={{
                  fontSize: 11.5,
                  color: C.ink4,
                  padding: 8,
                  textAlign: 'center',
                }}
              >
                未跑过 · 点击上面按钮
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {ocrHits.map((h, i) => (
                  <div
                    key={i}
                    style={{
                      padding: '8px 10px',
                      borderRadius: 5,
                      background: C.surface2,
                      border: `1px solid ${C.border}`,
                      display: 'flex',
                      alignItems: 'baseline',
                      gap: 8,
                    }}
                  >
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div
                        className="gb-mono"
                        style={{
                          fontSize: 14,
                          color: C.ink,
                          fontWeight: 600,
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        "{h.text}"
                      </div>
                      <ConfBar value={h.conf} />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div
            style={{ padding: '12px 14px', flex: 1, minHeight: 0 }}
            className="gb-scroll"
          >
            <div
              style={{
                fontSize: 11,
                color: C.ink3,
                fontWeight: 500,
                letterSpacing: '.06em',
                textTransform: 'uppercase',
                marginBottom: 6,
              }}
            >
              属性
            </div>
            {cur && (
              <>
                <Field label="name">
                  <span
                    className="gb-mono"
                    style={{ fontSize: 11.5, color: C.ink2 }}
                  >
                    {cur.name}
                  </span>
                </Field>
                <Field label="desc">
                  <input
                    value={editDesc}
                    onChange={(e) => setEditDesc(e.target.value)}
                    style={{ ...inputSty, fontFamily: 'inherit' }}
                  />
                </Field>
                <Field label="used_in">
                  <span
                    className="gb-mono"
                    style={{ fontSize: 11.5, color: C.ink2 }}
                  >
                    {cur.used_in || '—'}
                  </span>
                </Field>
                <Field label="rect (归一化)">
                  <div
                    className="gb-mono"
                    style={{ fontSize: 10.5, color: C.ink2, lineHeight: 1.6 }}
                  >
                    <div>
                      x1, y1 = ({editRect[0].toFixed(3)}, {editRect[1].toFixed(3)})
                    </div>
                    <div>
                      x2, y2 = ({editRect[2].toFixed(3)}, {editRect[3].toFixed(3)})
                    </div>
                  </div>
                </Field>
              </>
            )}
            <div
              style={{
                marginTop: 12,
                paddingTop: 10,
                borderTop: `1px solid ${C.borderSoft}`,
                display: 'flex',
                gap: 6,
              }}
            >
              <button
                type="button"
                onClick={doSave}
                disabled={busy || !dirty}
                style={{
                  flex: 1,
                  padding: '7px 0',
                  borderRadius: 5,
                  fontSize: 12,
                  fontWeight: 600,
                  color: dirty ? '#f0eee9' : C.ink4,
                  background: dirty ? C.ink : C.surface2,
                  border: `1px solid ${dirty ? C.ink : C.border}`,
                  cursor: dirty && !busy ? 'pointer' : 'not-allowed',
                  fontFamily: 'inherit',
                }}
              >
                {busy ? '…' : dirty ? '保存修改' : '已保存'}
              </button>
              <button
                type="button"
                onClick={reset}
                disabled={busy || !dirty}
                style={{
                  padding: '7px 12px',
                  borderRadius: 5,
                  fontSize: 12,
                  color: C.ink2,
                  background: C.surface,
                  border: `1px solid ${C.border}`,
                  cursor: dirty && !busy ? 'pointer' : 'not-allowed',
                  fontFamily: 'inherit',
                  opacity: dirty ? 1 : 0.5,
                }}
              >
                重置
              </button>
            </div>
            <div
              className="gb-mono"
              style={{ fontSize: 10, color: C.ink4, marginTop: 8, wordBreak: 'break-all' }}
              title={yamlPath}
            >
              {yamlPath}
            </div>
          </div>
        </div>
      </div>
    </>
  )
}

function ZoomModal({
  cur,
  editRect,
  onChange,
  fullImg,
  onClose,
}: {
  cur: RoiItem
  editRect: [number, number, number, number]
  onChange: (r: [number, number, number, number]) => void
  fullImg: string
  onClose: () => void
}) {
  return (
    <div
      onClick={onClose}
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
          maxHeight: '100%',
          background: '#0a0a08',
          borderRadius: 10,
          overflow: 'hidden',
          border: '1px solid #2a2a25',
          display: 'flex',
          flexDirection: 'column',
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
          <span className="gb-mono" style={{ fontSize: 12, fontWeight: 600 }}>
            {cur.name}
          </span>
          <span style={{ fontSize: 11, color: 'rgba(255,255,255,.5)' }}>
            {cur.desc}
          </span>
          <span style={{ flex: 1 }} />
          <span style={{ fontSize: 11, color: 'rgba(255,255,255,.5)' }}>
            拖拽框边可改大小 · 拖拽框内可移动
          </span>
          <button
            type="button"
            onClick={onClose}
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
        <div
          style={{
            flex: 1,
            position: 'relative',
            minHeight: 0,
            aspectRatio: '16/9',
          }}
        >
          {fullImg && (
            <img
              src={fullImg}
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
          )}
          <RecogRectEditor
            rect={editRect}
            onChange={onChange}
            color="#d97757"
            label={cur.name}
            allowCreate={true}
          />
        </div>
      </div>
    </div>
  )
}

const inputSty: React.CSSProperties = {
  width: '100%',
  padding: '4px 7px',
  fontSize: 11.5,
  borderRadius: 4,
  background: '#fff',
  border: '1px solid #e7e3da',
  color: '#1a1a17',
  outline: 'none',
  boxSizing: 'border-box',
  display: 'block',
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 8 }}>
      <div
        style={{
          fontSize: 10,
          color: '#6b675e',
          fontWeight: 500,
          marginBottom: 3,
          fontFamily: '"IBM Plex Mono", monospace',
        }}
      >
        {label}
      </div>
      {children}
    </div>
  )
}

function ConfBar({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(1, value))
  const high = pct >= 0.9
  const mid = pct >= 0.75
  const color = high ? '#16a34a' : mid ? '#d97757' : '#a16207'
  return (
    <div style={{ marginTop: 5, display: 'flex', alignItems: 'center', gap: 7 }}>
      <div
        style={{
          flex: 1,
          height: 5,
          borderRadius: 3,
          background: 'linear-gradient(90deg, #e7e3da 0%, #f1efe9 100%)',
          overflow: 'hidden',
          position: 'relative',
        }}
      >
        <div
          style={{
            position: 'absolute',
            inset: 0,
            width: `${pct * 100}%`,
            background: `linear-gradient(90deg, ${color}cc 0%, ${color} 100%)`,
            borderRadius: 3,
          }}
        />
      </div>
      <span
        className="gb-mono"
        style={{
          fontSize: 10.5,
          color,
          fontWeight: 600,
          width: 38,
          textAlign: 'right',
        }}
      >
        {(pct * 100).toFixed(1)}%
      </span>
    </div>
  )
}

function PreprocGlyph({ kind }: { kind: Preprocessing }) {
  const styles: Record<Preprocessing, React.CSSProperties> = {
    grayscale: { background: 'linear-gradient(90deg, #1a1a17, #d6d3cc)' },
    clahe: { background: 'linear-gradient(90deg, #5a5a55, #1a1a17, #c9c6bf)' },
    binarize: { background: 'linear-gradient(90deg, #1a1a17 50%, #fff 50%)' },
    sharpen: {
      background:
        'repeating-linear-gradient(90deg, #1a1a17 0 1px, #fff 1px 2px)',
    },
    invert: { background: 'linear-gradient(90deg, #fff, #1a1a17)' },
  }
  return (
    <span
      style={{
        width: 11,
        height: 11,
        borderRadius: 2,
        display: 'inline-block',
        border: '1px solid rgba(0,0,0,.12)',
        ...styles[kind],
      }}
    />
  )
}
