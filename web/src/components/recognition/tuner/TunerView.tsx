/**
 * TunerView — 模版调试 (3-pane: list / controls / dual preview).
 * 接真后端: templates/list /preview /test /save_meta + screenshot
 */

import { Fragment, useEffect, useMemo, useState } from 'react'
import { C } from '@/lib/design-tokens'
import { useAppStore } from '@/lib/store'
import {
  ALL_PREPROC,
  fetchTemplateList,
  fetchTemplatePreview,
  saveTemplateMeta,
  templateImgSrc,
  testTemplate,
  type Preprocessing,
  type TemplateMeta,
  type TemplateTestResult,
} from '@/lib/templatesApi'
import { GbSlider } from '@/components/ui/GbSlider'

const PREPROC_LABEL: Record<Preprocessing, string> = {
  grayscale: '灰度',
  clahe: 'CLAHE',
  binarize: '二值化',
  sharpen: '锐化',
  invert: '反色',
  edge: '边缘',
}

export function TunerView() {
  const emulators = useAppStore((s) => s.emulators)
  const runningInstances = useMemo(() => emulators.filter((e) => e.running), [emulators])

  const [list, setList] = useState<TemplateMeta[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [filter, setFilter] = useState('')
  const [selectedName, setSelectedName] = useState<string | null>(null)

  const [editPreproc, setEditPreproc] = useState<Preprocessing[]>([])
  const [editThr, setEditThr] = useState(0.80)
  const [src, setSrc] = useState<{ kind: 'instance'; instance?: number }>({
    kind: 'instance',
  })

  const [previewProcessed, setPreviewProcessed] = useState<string>('')
  const [result, setResult] = useState<TemplateTestResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [hint, setHint] = useState('')
  const [savedSrcUrl, setSavedSrcUrl] = useState<string | null>(null)

  // initial load
  useEffect(() => {
    let alive = true
    fetchTemplateList()
      .then((r) => {
        if (!alive) return
        setList(r.items)
        if (r.items.length > 0 && !selectedName) {
          setSelectedName(r.items[0].name)
        }
      })
      .catch((e) => alive && setErr(String(e)))
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // default source
  useEffect(() => {
    if (src.instance == null && runningInstances.length > 0) {
      setSrc({ kind: 'instance', instance: runningInstances[0].index })
    }
  }, [runningInstances, src.instance])

  const tpl = list?.find((t) => t.name === selectedName) || null

  // sync edit fields when selectedName changes
  useEffect(() => {
    if (!tpl) return
    setEditPreproc([...tpl.preprocessing])
    setEditThr(tpl.threshold || 0.80)
    setResult(null)
    setHint('')
    setPreviewProcessed('')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedName])

  // fetch preview when preproc changes
  useEffect(() => {
    if (!tpl) return
    let alive = true
    fetchTemplatePreview(tpl.name, editPreproc)
      .then((r) => {
        if (!alive) return
        setPreviewProcessed(r.processed_b64)
      })
      .catch(() => {})
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedName, editPreproc.join(',')])

  const dirty =
    tpl != null &&
    (JSON.stringify(editPreproc) !== JSON.stringify(tpl.preprocessing) ||
      editThr !== (tpl.threshold || 0.80))

  const filtered = useMemo(() => {
    if (!list) return []
    return list.filter((t) => !filter || t.name.toLowerCase().includes(filter.toLowerCase()))
  }, [list, filter])

  const runTest = async () => {
    if (!tpl) return
    if (src.instance == null) {
      setHint('先选实例 (没有运行中的实例)')
      return
    }
    setBusy(true)
    setHint('测试中…')
    try {
      const r = await testTemplate({
        name: tpl.name,
        instance: src.instance,
        threshold: editThr,
        preprocessing: editPreproc,
      })
      setResult(r)
      // 同时拿源截图保存为 dataURL 显示原图
      try {
        const sr = await fetch(`/api/screenshot/${src.instance}`)
        if (sr.ok) {
          const b = await sr.blob()
          const url = URL.createObjectURL(b)
          if (savedSrcUrl) URL.revokeObjectURL(savedSrcUrl)
          setSavedSrcUrl(url)
        }
      } catch {}
      setHint(r.hit ? `命中 · 得分 ${r.score.toFixed(3)}` : `未命中 · 得分 ${r.score.toFixed(3)}`)
    } catch (e) {
      setHint(`测试失败: ${String(e).slice(0, 120)}`)
    } finally {
      setBusy(false)
    }
  }

  const doSave = async () => {
    if (!tpl) return
    setBusy(true)
    setHint('保存中…')
    try {
      await saveTemplateMeta({
        name: tpl.name,
        preprocessing: editPreproc,
        threshold: editThr,
      })
      // refresh list to get updated meta
      const r = await fetchTemplateList()
      setList(r.items)
      setHint('已保存')
    } catch (e) {
      setHint(`保存失败: ${String(e).slice(0, 120)}`)
    } finally {
      setBusy(false)
    }
  }

  if (err) return <ErrorBox title="无法加载模版列表" detail={err} />
  if (!list) return <LoadingBox label="加载模版列表…" />
  if (list.length === 0)
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
        模版库为空 · 先到「模版库」上传或裁切
      </div>
    )

  return (
    <div
      style={{
        flex: 1,
        minHeight: 0,
        display: 'grid',
        gridTemplateColumns: '260px 320px 1fr',
      }}
    >
      {/* LEFT — picker */}
      <div
        style={{
          borderRight: `1px solid ${C.border}`,
          background: C.surface,
          display: 'flex',
          flexDirection: 'column',
          minHeight: 0,
        }}
      >
        <div style={{ padding: 12, borderBottom: `1px solid ${C.borderSoft}` }}>
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="筛选模版…"
            className="gb-mono"
            style={{
              width: '100%',
              padding: '5px 9px',
              fontSize: 12,
              background: C.surface2,
              border: `1px solid ${C.border}`,
              borderRadius: 5,
              color: C.ink,
              outline: 'none',
              boxSizing: 'border-box',
              fontFamily: 'inherit',
            }}
          />
        </div>
        <div className="gb-scroll" style={{ flex: 1, minHeight: 0, overflow: 'hidden auto' }}>
          {filtered.map((t) => {
            const on = t.name === selectedName
            return (
              <button
                key={t.name}
                type="button"
                onClick={() => setSelectedName(t.name)}
                style={{
                  display: 'flex',
                  width: '100%',
                  textAlign: 'left',
                  padding: '8px 12px',
                  gap: 10,
                  alignItems: 'center',
                  background: on ? C.surface2 : 'transparent',
                  borderTop: 'none',
                  borderRight: 'none',
                  borderBottom: `1px solid ${C.borderSoft}`,
                  borderLeft: `2px solid ${on ? C.ink : 'transparent'}`,
                  cursor: 'pointer',
                  fontFamily: 'inherit',
                }}
              >
                <div
                  style={{
                    width: 40,
                    height: 28,
                    background: '#f1efe9',
                    borderRadius: 3,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    overflow: 'hidden',
                    flexShrink: 0,
                  }}
                >
                  <img
                    src={templateImgSrc(t.name)}
                    alt=""
                    style={{
                      maxWidth: '100%',
                      maxHeight: '100%',
                      objectFit: 'contain',
                    }}
                  />
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div
                    className="gb-mono"
                    style={{
                      fontSize: 11.5,
                      fontWeight: on ? 600 : 500,
                      color: on ? C.ink : C.ink2,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {t.name}
                  </div>
                  <div
                    style={{
                      fontSize: 10,
                      color: C.ink4,
                      marginTop: 1,
                      display: 'flex',
                      gap: 5,
                    }}
                  >
                    <span>{t.category}</span>
                    {t.preprocessing.length > 0 && (
                      <>
                        <span>·</span>
                        <span className="gb-mono">+{t.preprocessing.length} ops</span>
                      </>
                    )}
                  </div>
                </div>
              </button>
            )
          })}
        </div>
      </div>

      {/* MIDDLE — controls */}
      <div
        style={{
          borderRight: `1px solid ${C.border}`,
          background: C.surface,
          display: 'flex',
          flexDirection: 'column',
          minHeight: 0,
        }}
      >
        <div className="gb-scroll" style={{ flex: 1, minHeight: 0, overflow: 'hidden auto' }}>
          {tpl && (
            <>
              <Section label="正在调试">
                <div className="gb-mono" style={{ fontSize: 13, fontWeight: 600 }}>
                  {tpl.name}
                </div>
                <div style={{ fontSize: 11, color: C.ink3, marginTop: 3 }}>
                  {tpl.category} · {tpl.width}×{tpl.height}
                </div>
                {dirty && (
                  <div
                    style={{
                      marginTop: 8,
                      padding: '6px 8px',
                      borderRadius: 5,
                      background: C.warnSoft,
                      color: '#a16207',
                      fontSize: 11,
                      display: 'flex',
                      alignItems: 'center',
                      gap: 6,
                    }}
                  >
                    <span
                      style={{
                        width: 6,
                        height: 6,
                        borderRadius: '50%',
                        background: '#a16207',
                      }}
                    />
                    未保存的更改
                  </div>
                )}
              </Section>

              <Section label="测试源">
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                  {runningInstances.length === 0 && (
                    <span style={{ fontSize: 11, color: C.ink4 }}>没运行中的实例</span>
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
              </Section>

              <Section label="预处理顺序" hint="顺序敏感, 自上而下应用">
                <PreprocChips value={editPreproc} onChange={setEditPreproc} />
              </Section>

              <Section label={`阈值 ${editThr.toFixed(2)}`} hint="低于阈值视为未命中">
                <GbSlider
                  value={editThr}
                  onChange={setEditThr}
                  min={0.3}
                  max={0.99}
                  step={0.01}
                  defaultMark={0.80}
                />
                <div
                  className="gb-mono"
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    fontSize: 10,
                    color: C.ink4,
                    marginTop: 6,
                  }}
                >
                  <span>0.30</span>
                  <span>默认 0.80</span>
                  <span>0.99</span>
                </div>
              </Section>

              <Section label="操作">
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  <button
                    type="button"
                    onClick={runTest}
                    disabled={busy || src.instance == null}
                    style={{
                      padding: '7px 0',
                      borderRadius: 5,
                      fontSize: 12,
                      fontWeight: 600,
                      color: src.instance != null ? '#f0eee9' : C.ink4,
                      background: src.instance != null ? C.ink : C.surface2,
                      border: `1px solid ${src.instance != null ? C.ink : C.border}`,
                      cursor:
                        busy || src.instance == null ? 'not-allowed' : 'pointer',
                      fontFamily: 'inherit',
                    }}
                  >
                    {busy ? '测试中…' : '跑测试'}
                  </button>
                  {hint && (
                    <div
                      className="gb-mono"
                      style={{
                        fontSize: 11,
                        color: C.ink3,
                        background: C.surface2,
                        padding: '6px 8px',
                        borderRadius: 4,
                      }}
                    >
                      {hint}
                    </div>
                  )}
                </div>
              </Section>
            </>
          )}
        </div>

        <div
          style={{
            padding: 12,
            borderTop: `1px solid ${C.borderSoft}`,
            display: 'flex',
            gap: 8,
          }}
        >
          <button
            type="button"
            onClick={doSave}
            disabled={!dirty || busy}
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
            保存到模版
          </button>
          <button
            type="button"
            onClick={() => {
              if (!tpl) return
              setEditPreproc([...tpl.preprocessing])
              setEditThr(tpl.threshold || 0.80)
            }}
            disabled={!dirty || busy}
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
      </div>

      {/* RIGHT — dual preview */}
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          minHeight: 0,
          background: C.bg,
        }}
      >
        <div
          style={{
            padding: 14,
            display: 'grid',
            gridTemplateColumns: '1fr 1fr',
            gap: 12,
            flex: 1,
            minHeight: 0,
          }}
        >
          <PreviewPane title="原图模版" subtitle="未应用预处理">
            {tpl && (
              <img
                src={templateImgSrc(tpl.name)}
                alt={tpl.name}
                style={{
                  width: '100%',
                  height: '100%',
                  objectFit: 'contain',
                  display: 'block',
                }}
              />
            )}
          </PreviewPane>
          <PreviewPane
            title="处理后"
            subtitle={
              editPreproc.length === 0
                ? '无变化'
                : editPreproc.map((p) => PREPROC_LABEL[p]).join(' → ')
            }
          >
            {previewProcessed ? (
              <img
                src={previewProcessed}
                alt="processed"
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
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  height: '100%',
                  color: 'rgba(255,255,255,.5)',
                  fontSize: 11,
                  fontFamily: C.fontMono,
                }}
              >
                加载预览中…
              </div>
            )}
          </PreviewPane>
        </div>

        {/* test result on game frame */}
        {result && tpl && (
          <div
            style={{
              padding: 14,
              borderTop: `1px solid ${C.border}`,
              background: C.surface,
            }}
          >
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
              测试结果
            </div>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: '1fr 320px',
                gap: 14,
                alignItems: 'start',
              }}
            >
              <div
                style={{
                  position: 'relative',
                  background: '#0a0a08',
                  borderRadius: 6,
                  overflow: 'hidden',
                  border: `1px solid ${C.border}`,
                  aspectRatio: result.source_image_size
                    ? `${result.source_image_size[0]} / ${result.source_image_size[1]}`
                    : '16 / 9',
                }}
              >
                {result.annotated_b64 ? (
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
                ) : savedSrcUrl ? (
                  <img
                    src={savedSrcUrl}
                    alt=""
                    style={{
                      width: '100%',
                      height: '100%',
                      objectFit: 'contain',
                      display: 'block',
                    }}
                  />
                ) : null}
              </div>
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: '1fr 1fr',
                  gap: 14,
                }}
              >
                <Stat
                  label="结果"
                  value={result.hit ? '命中' : '未命中'}
                  tone={result.hit ? 'live' : 'error'}
                />
                <Stat
                  label="得分 / 阈值"
                  value={`${result.score.toFixed(3)} / ${editThr.toFixed(2)}`}
                  mono
                />
                <Stat
                  label="命中中心"
                  value={`(${result.cx}, ${result.cy})`}
                  mono
                />
                <Stat
                  label="命中尺寸"
                  value={`${result.w}×${result.h}`}
                  mono
                />
                <Stat label="耗时" value={result.duration_ms} sub="ms" mono />
                <Stat
                  label="源尺寸"
                  value={
                    result.source_image_size
                      ? `${result.source_image_size[0]}×${result.source_image_size[1]}`
                      : '—'
                  }
                  mono
                />
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function Section({
  label, hint, children,
}: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div style={{ padding: '14px 14px', borderBottom: `1px solid ${C.borderSoft}` }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 8 }}>
        <span
          style={{
            fontSize: 11,
            color: C.ink3,
            fontWeight: 500,
            letterSpacing: '.06em',
            textTransform: 'uppercase',
          }}
        >
          {label}
        </span>
        {hint && <span style={{ fontSize: 10.5, color: C.ink4 }}>{hint}</span>}
      </div>
      {children}
    </div>
  )
}

function Stat({
  label, value, sub, mono, tone,
}: {
  label: string
  value: string | number
  sub?: string
  mono?: boolean
  tone?: 'live' | 'error'
}) {
  const color = tone === 'live' ? C.live : tone === 'error' ? C.error : C.ink
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
          fontSize: 16,
          fontWeight: 600,
          color,
          display: 'flex',
          alignItems: 'baseline',
          gap: 4,
          lineHeight: 1.1,
        }}
      >
        {value}
        {sub && <span style={{ fontSize: 11, color: C.ink3, fontWeight: 400 }}>{sub}</span>}
      </div>
    </div>
  )
}

function PreviewPane({
  title, subtitle, children,
}: { title: string; subtitle: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 6 }}>
        <span style={{ fontSize: 11.5, fontWeight: 600, color: C.ink2 }}>{title}</span>
        <span className="gb-mono" style={{ fontSize: 10.5, color: C.ink4 }}>
          {subtitle}
        </span>
      </div>
      <div
        style={{
          position: 'relative',
          flex: 1,
          minHeight: 0,
          background: '#0a0a08',
          borderRadius: 6,
          overflow: 'hidden',
          border: `1px solid ${C.border}`,
        }}
      >
        {children}
      </div>
    </div>
  )
}

function PreprocChips({
  value,
  onChange,
}: { value: Preprocessing[]; onChange: (v: Preprocessing[]) => void }) {
  const [drag, setDrag] = useState<Preprocessing | null>(null)
  const enabled = value
  const disabled = ALL_PREPROC.filter((p) => !enabled.includes(p))

  const toggle = (k: Preprocessing) => {
    if (enabled.includes(k)) onChange(enabled.filter((x) => x !== k))
    else onChange([...enabled, k])
  }

  const moveTo = (idx: number, k: Preprocessing) => {
    const list = enabled.filter((x) => x !== k)
    list.splice(idx, 0, k)
    onChange(list)
  }

  return (
    <div>
      <div style={{ fontSize: 10, color: C.ink4, marginBottom: 6 }}>
        启用 · 自上而下顺序应用
      </div>
      <div
        style={{
          minHeight: 36,
          padding: 6,
          borderRadius: 5,
          background: C.surface2,
          border: `1px dashed ${C.border}`,
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: 4,
        }}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault()
          if (drag) moveTo(enabled.length, drag)
          setDrag(null)
        }}
      >
        {enabled.length === 0 && (
          <span style={{ fontSize: 11, color: C.ink4, padding: '2px 6px' }}>
            (无 — 拖拽下方算子加入)
          </span>
        )}
        {enabled.map((k, i) => (
          <Fragment key={k}>
            {i > 0 && <span style={{ color: C.ink4, fontSize: 10 }}>→</span>}
            <span
              draggable
              onDragStart={() => setDrag(k)}
              onDragOver={(e) => {
                e.preventDefault()
                if (drag && drag !== k) moveTo(i, drag)
              }}
              className="gb-mono"
              style={{
                padding: '4px 8px',
                fontSize: 11.5,
                fontWeight: 600,
                background: C.ink,
                color: '#f0eee9',
                borderRadius: 4,
                cursor: 'grab',
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
              }}
            >
              <span style={{ opacity: 0.6, fontSize: 9 }}>{i + 1}</span>
              {PREPROC_LABEL[k]}
              <button
                type="button"
                onClick={() => toggle(k)}
                style={{
                  color: 'rgba(255,255,255,.7)',
                  fontSize: 11,
                  padding: '0 0 0 2px',
                  cursor: 'pointer',
                  background: 'transparent',
                  border: 'none',
                  fontFamily: 'inherit',
                }}
              >
                ×
              </button>
            </span>
          </Fragment>
        ))}
      </div>
      <div style={{ fontSize: 10, color: C.ink4, margin: '10px 0 6px' }}>未启用</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
        {disabled.map((k) => (
          <button
            key={k}
            type="button"
            onClick={() => toggle(k)}
            draggable
            onDragStart={() => setDrag(k)}
            style={{
              padding: '4px 8px',
              fontSize: 11.5,
              fontWeight: 500,
              background: C.surface,
              border: `1px solid ${C.border}`,
              color: C.ink2,
              borderRadius: 4,
              fontFamily: C.fontMono,
              cursor: 'grab',
            }}
          >
            + {PREPROC_LABEL[k]}
          </button>
        ))}
      </div>
    </div>
  )
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

function ErrorBox({ title, detail }: { title: string; detail: string }) {
  return (
    <div
      style={{
        flex: 1,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 30,
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
          {title}
        </div>
        <div
          className="gb-mono"
          style={{ fontSize: 11.5, color: C.ink3, wordBreak: 'break-word' }}
        >
          {detail}
        </div>
      </div>
    </div>
  )
}
