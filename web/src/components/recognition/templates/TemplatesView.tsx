/**
 * TemplatesView — 模版库 (左 grid + 右 detail drawer + UploadCropperModal).
 * 接真后端: /api/templates/list /detail/{name} /stats/{name} /file/{name} /original/{name}
 *           /upload (multipart) /api/templates/{name} (DELETE)
 */

import { Fragment, useEffect, useMemo, useState } from 'react'
import { C } from '@/lib/design-tokens'
import { useAppStore } from '@/lib/store'
import {
  ALL_PREPROC,
  deleteTemplate,
  fetchTemplateDetail,
  fetchTemplateList,
  fetchTemplateStats,
  templateImgSrc,
  templateOriginalSrc,
  uploadTemplate,
  type Preprocessing,
  type TemplateDetail as TplDetail,
  type TemplateMeta,
  type TemplateStats,
} from '@/lib/templatesApi'
import { RecogRectEditor } from '../canvas/RecogRectEditor'

const PREPROC_LABEL: Record<Preprocessing, string> = {
  grayscale: '灰度',
  clahe: 'CLAHE',
  binarize: '二值化',
  sharpen: '锐化',
  invert: '反色',
  edge: '边缘',
}

function fmtBytes(n: number) {
  if (n < 1024) return n + ' B'
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB'
  return (n / 1024 / 1024).toFixed(2) + ' MB'
}
function fmtAgo(ts: number) {
  const m = Math.floor((Date.now() - ts * 1000) / 60000)
  if (m < 60) return m + ' 分前'
  if (m < 1440) return Math.floor(m / 60) + ' 时前'
  return Math.floor(m / 1440) + ' 天前'
}

// 真模版小预览: 直接 <img> 加载后端文件
export function TemplateThumb({
  tpl,
  w = 96,
  h = 64,
}: {
  tpl: { name: string; width: number; height: number }
  w?: number
  h?: number
}) {
  return (
    <div
      style={{
        position: 'relative',
        width: w,
        height: h,
        background: '#f1efe9',
        borderRadius: 4,
        overflow: 'hidden',
        flexShrink: 0,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <img
        src={templateImgSrc(tpl.name)}
        alt={tpl.name}
        style={{
          maxWidth: '100%',
          maxHeight: '100%',
          objectFit: 'contain',
          display: 'block',
        }}
        onError={(e) => {
          ;(e.currentTarget as HTMLImageElement).style.opacity = '0'
        }}
      />
    </div>
  )
}

export function TemplatesView() {
  const [list, setList] = useState<TemplateMeta[] | null>(null)
  const [categories, setCategories] = useState<{ name: string; count: number }[]>([])
  const [tplDir, setTplDir] = useState('')
  const [err, setErr] = useState<string | null>(null)

  const [q, setQ] = useState('')
  const [cat, setCat] = useState('all')
  const [sortBy, setSortBy] = useState<'mtime' | 'hit' | 'name' | 'size'>('mtime')
  const [activeName, setActiveName] = useState<string | null>(null)
  const [showUpload, setShowUpload] = useState(false)

  const reload = async () => {
    setErr(null)
    try {
      const r = await fetchTemplateList()
      setList(r.items)
      setCategories(r.categories)
      setTplDir(r.template_dir)
      if (!activeName && r.items.length > 0) setActiveName(r.items[0].name)
    } catch (e) {
      setErr(String(e))
    }
  }

  useEffect(() => {
    reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 命中率排序需要 stats; lazy fetch all stats 一次
  const [stats, setStats] = useState<Record<string, TemplateStats>>({})
  useEffect(() => {
    if (!list) return
    let alive = true
    const queue = [...list]
    const concurrency = 4
    let active = 0
    const run = () => {
      if (!alive) return
      while (active < concurrency && queue.length > 0) {
        const t = queue.shift()!
        if (stats[t.name]) continue
        active++
        fetchTemplateStats(t.name)
          .then((s) => {
            if (!alive) return
            setStats((m) => ({ ...m, [t.name]: s }))
          })
          .catch(() => {})
          .finally(() => {
            active--
            if (alive) run()
          })
      }
    }
    run()
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [list])

  const filtered = useMemo(() => {
    if (!list) return []
    let arr = list.slice()
    if (q) arr = arr.filter((t) => t.name.toLowerCase().includes(q.toLowerCase()))
    if (cat !== 'all') arr = arr.filter((t) => t.category === cat)
    if (sortBy === 'mtime') arr.sort((a, b) => b.mtime - a.mtime)
    if (sortBy === 'hit') arr.sort((a, b) => (stats[b.name]?.hit_rate || 0) - (stats[a.name]?.hit_rate || 0))
    if (sortBy === 'name') arr.sort((a, b) => a.name.localeCompare(b.name))
    if (sortBy === 'size') arr.sort((a, b) => b.size_bytes - a.size_bytes)
    return arr
  }, [list, q, cat, sortBy, stats])

  const active = list?.find((t) => t.name === activeName) || null

  if (err) return <ErrorBox title="无法加载模版列表" detail={err} />
  if (!list) return <LoadingBox label="加载模版列表…" />

  return (
    <div
      style={{
        flex: 1,
        minHeight: 0,
        display: 'grid',
        gridTemplateColumns: '1fr 380px',
        gap: 0,
      }}
    >
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          minHeight: 0,
          borderRight: `1px solid ${C.border}`,
        }}
      >
        <div
          style={{
            padding: '12px 22px',
            borderBottom: `1px solid ${C.borderSoft}`,
            background: C.surface,
            display: 'flex',
            flexDirection: 'column',
            gap: 10,
          }}
        >
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <div style={{ flex: 1, position: 'relative', maxWidth: 380 }}>
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="搜索模版名 (e.g. lobby_btn_start)…"
                className="gb-mono"
                style={{
                  width: '100%',
                  padding: '6px 10px 6px 26px',
                  background: C.surface2,
                  border: `1px solid ${C.border}`,
                  borderRadius: 6,
                  fontSize: 12.5,
                  color: C.ink,
                  outline: 'none',
                  boxSizing: 'border-box',
                  fontFamily: 'inherit',
                }}
              />
              <span
                style={{
                  position: 'absolute',
                  left: 8,
                  top: '50%',
                  transform: 'translateY(-50%)',
                  color: C.ink4,
                  fontSize: 12,
                }}
              >
                ⌕
              </span>
            </div>
            <span className="gb-mono" style={{ fontSize: 11, color: C.ink3 }}>
              {filtered.length} / {list.length}
            </span>
            <span style={{ flex: 1 }} />
            <span style={{ fontSize: 11, color: C.ink3 }}>排序</span>
            <select
              value={sortBy}
              onChange={(e) =>
                setSortBy(e.target.value as 'mtime' | 'hit' | 'name' | 'size')
              }
              style={{
                fontSize: 12,
                padding: '4px 8px',
                borderRadius: 5,
                background: C.surface,
                border: `1px solid ${C.border}`,
                color: C.ink,
                fontFamily: 'inherit',
                outline: 'none',
              }}
            >
              <option value="mtime">按修改时间</option>
              <option value="hit">按命中率</option>
              <option value="name">按名称</option>
              <option value="size">按大小</option>
            </select>
            <button
              type="button"
              onClick={() => setShowUpload(true)}
              style={{
                padding: '5px 12px',
                borderRadius: 6,
                fontSize: 12,
                fontWeight: 600,
                background: C.ink,
                color: '#f0eee9',
                border: 'none',
                cursor: 'pointer',
                fontFamily: 'inherit',
              }}
            >
              + 上传 / 裁切
            </button>
          </div>

          <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
            <CategoryChip k="all" l="全部" n={list.length} active={cat} onClick={setCat} />
            {categories.map((c) => (
              <CategoryChip
                key={c.name}
                k={c.name}
                l={c.name}
                n={c.count}
                active={cat}
                onClick={setCat}
              />
            ))}
          </div>
        </div>

        <div
          className="gb-scroll"
          style={{ flex: 1, minHeight: 0, overflow: 'hidden auto', padding: 18 }}
        >
          <div
            style={{
              display: 'grid',
              gap: 10,
              gridTemplateColumns: 'repeat(auto-fill, minmax(186px, 1fr))',
            }}
          >
            {filtered.map((t) => (
              <TemplateCard
                key={t.name}
                tpl={t}
                stats={stats[t.name]}
                active={t.name === activeName}
                onPick={() => setActiveName(t.name)}
              />
            ))}
            {filtered.length === 0 && (
              <div
                style={{
                  gridColumn: '1 / -1',
                  padding: 40,
                  textAlign: 'center',
                  color: C.ink3,
                  fontSize: 13,
                }}
              >
                {list.length === 0 ? '模版库为空' : '无匹配模版'}
              </div>
            )}
          </div>
          <div
            className="gb-mono"
            style={{ marginTop: 14, fontSize: 10, color: C.ink4 }}
          >
            {tplDir}
          </div>
        </div>
      </div>

      <TemplateDetailPanel
        tpl={active}
        stats={active ? stats[active.name] : undefined}
        onDeleted={async () => {
          setActiveName(null)
          await reload()
        }}
      />

      {showUpload && (
        <UploadCropperModal
          onClose={() => setShowUpload(false)}
          onUploaded={async (name) => {
            setShowUpload(false)
            await reload()
            setActiveName(name)
          }}
        />
      )}
    </div>
  )
}

function CategoryChip({
  k, l, n, active, onClick,
}: { k: string; l: string; n: number; active: string; onClick: (k: string) => void }) {
  const on = active === k
  return (
    <button
      type="button"
      onClick={() => onClick(k)}
      style={{
        padding: '3px 9px',
        fontSize: 11.5,
        borderRadius: 11,
        fontWeight: on ? 600 : 500,
        color: on ? '#f0eee9' : C.ink2,
        background: on ? C.ink : C.surface2,
        border: `1px solid ${on ? C.ink : C.border}`,
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        cursor: 'pointer',
        fontFamily: 'inherit',
      }}
    >
      {l}
      <span
        className="gb-mono"
        style={{
          fontSize: 10,
          fontWeight: 500,
          color: on ? '#f0eee9' : C.ink4,
          opacity: on ? 0.7 : 1,
        }}
      >
        {n}
      </span>
    </button>
  )
}

function TemplateCard({
  tpl, stats, active, onPick,
}: {
  tpl: TemplateMeta
  stats?: TemplateStats
  active: boolean
  onPick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onPick}
      style={{
        textAlign: 'left',
        padding: 10,
        background: C.surface,
        border: `1px solid ${active ? C.ink : C.border}`,
        outline: active ? `2px solid ${C.ink}` : 'none',
        outlineOffset: -1,
        borderRadius: 7,
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        cursor: 'pointer',
        fontFamily: 'inherit',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          height: 96,
          background: C.surface2,
          borderRadius: 4,
        }}
      >
        <TemplateThumb tpl={tpl} w={tpl.width > tpl.height ? 140 : 80} h={80} />
      </div>
      <div
        className="gb-mono"
        style={{
          fontSize: 11.5,
          fontWeight: 600,
          color: C.ink,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
      >
        {tpl.name}
      </div>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          fontSize: 10.5,
          color: C.ink3,
        }}
      >
        <span className="gb-mono">{tpl.width}×{tpl.height}</span>
        <span>·</span>
        <span className="gb-mono">
          {stats ? `${(stats.hit_rate * 100).toFixed(0)}%` : '—'}
        </span>
        <span style={{ flex: 1 }} />
        {tpl.preprocessing.length > 0 && (
          <span className="gb-mono" style={{ fontSize: 9.5, color: C.ink4 }}>
            +{tpl.preprocessing.length}
          </span>
        )}
      </div>
    </button>
  )
}

function TemplateDetailPanel({
  tpl, stats, onDeleted,
}: {
  tpl: TemplateMeta | null
  stats?: TemplateStats
  onDeleted: () => Promise<void>
}) {
  const [detail, setDetail] = useState<TplDetail | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!tpl) {
      setDetail(null)
      return
    }
    let alive = true
    fetchTemplateDetail(tpl.name)
      .then((d) => {
        if (alive) setDetail(d)
      })
      .catch(() => {
        if (alive) setDetail(null)
      })
    return () => {
      alive = false
    }
  }, [tpl])

  if (!tpl) {
    return (
      <div
        style={{
          background: C.surface,
          padding: 30,
          color: C.ink4,
          fontSize: 12,
          textAlign: 'center',
        }}
      >
        选择左侧模版查看详情
      </div>
    )
  }

  const handleDelete = async () => {
    if (!confirm(`确认删除模版 ${tpl.name}?`)) return
    setBusy(true)
    try {
      await deleteTemplate(tpl.name)
      await onDeleted()
    } catch (e) {
      alert(`删除失败: ${String(e).slice(0, 200)}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      className="gb-scroll"
      style={{
        overflow: 'hidden auto',
        background: C.surface,
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <div style={{ padding: '14px 18px', borderBottom: `1px solid ${C.borderSoft}` }}>
        <div
          className="gb-mono"
          style={{ fontSize: 14, fontWeight: 600, color: C.ink, marginBottom: 4 }}
        >
          {tpl.name}
        </div>
        <div
          style={{
            fontSize: 11.5,
            color: C.ink3,
            display: 'flex',
            gap: 8,
            alignItems: 'center',
            flexWrap: 'wrap',
          }}
        >
          <span
            style={{
              padding: '1px 6px',
              borderRadius: 3,
              background: C.surface2,
              border: `1px solid ${C.border}`,
            }}
          >
            {tpl.category}
          </span>
          <span>·</span>
          <span className="gb-mono">{tpl.width}×{tpl.height}</span>
          <span>·</span>
          <span>{fmtBytes(tpl.size_bytes)}</span>
          <span>·</span>
          <span>{fmtAgo(tpl.mtime)}</span>
        </div>
      </div>

      <div style={{ padding: 18, borderBottom: `1px solid ${C.borderSoft}` }}>
        <div
          style={{
            height: 180,
            background: '#0a0a08',
            borderRadius: 6,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            position: 'relative',
            overflow: 'hidden',
          }}
        >
          <img
            src={templateImgSrc(tpl.name)}
            alt={tpl.name}
            style={{
              maxWidth: '100%',
              maxHeight: '100%',
              objectFit: 'contain',
              display: 'block',
            }}
          />
          <div
            style={{
              position: 'absolute',
              top: 8,
              left: 8,
              padding: '2px 6px',
              borderRadius: 3,
              background: 'rgba(20,20,18,.78)',
              color: 'rgba(255,255,255,.78)',
              fontFamily: C.fontMono,
              fontSize: 10,
            }}
          >
            crop preview
          </div>
        </div>
        {detail?.has_original && detail.crop_bbox && (
          <div
            className="gb-mono"
            style={{ fontSize: 10.5, color: C.ink3, marginTop: 6 }}
          >
            原图坐标 [{detail.crop_bbox.join(', ')}] · {tpl.source_w}×{tpl.source_h}
          </div>
        )}
      </div>

      <div
        style={{
          padding: '14px 18px',
          borderBottom: `1px solid ${C.borderSoft}`,
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: 12,
        }}
      >
        <Stat
          label="命中次数"
          value={stats?.hit_count ?? '—'}
          sub={stats ? `/ ${stats.match_count} 次` : undefined}
        />
        <Stat
          label="命中率"
          value={stats ? `${(stats.hit_rate * 100).toFixed(1)}%` : '—'}
          mono
        />
        <Stat
          label="平均得分"
          value={stats ? stats.avg_score : '—'}
          mono
        />
        <Stat
          label="阈值"
          value={tpl.threshold || '默认 0.80'}
          mono
          color={tpl.threshold ? C.ink : C.ink3}
        />
      </div>

      <div style={{ padding: '14px 18px', borderBottom: `1px solid ${C.borderSoft}` }}>
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
          预处理顺序
        </div>
        {tpl.preprocessing.length === 0 ? (
          <div style={{ fontSize: 12, color: C.ink4 }}>无 (使用原图)</div>
        ) : (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              flexWrap: 'wrap',
            }}
          >
            {tpl.preprocessing.map((p, i) => (
              <Fragment key={p}>
                {i > 0 && <span style={{ color: C.ink4, fontSize: 11 }}>→</span>}
                <span
                  className="gb-mono"
                  style={{
                    padding: '3px 8px',
                    fontSize: 11,
                    fontWeight: 600,
                    background: C.surface2,
                    border: `1px solid ${C.border}`,
                    borderRadius: 4,
                    color: C.ink2,
                  }}
                >
                  {PREPROC_LABEL[p] || p}
                </span>
              </Fragment>
            ))}
          </div>
        )}
      </div>

      <div style={{ padding: '14px 18px', flex: 1 }}>
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
          元数据
        </div>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'auto 1fr',
            gap: '6px 12px',
            fontSize: 11.5,
          }}
        >
          <span style={{ color: C.ink3 }}>路径</span>
          <span
            className="gb-mono"
            style={{ color: C.ink2, wordBreak: 'break-all' }}
          >
            {tpl.path}
          </span>
          <span style={{ color: C.ink3 }}>pHash</span>
          <span className="gb-mono" style={{ color: C.ink2 }}>
            {tpl.phash}
          </span>
          {detail && (
            <>
              <span style={{ color: C.ink3 }}>来源</span>
              <span
                className="gb-mono"
                style={{ color: C.ink2, wordBreak: 'break-all' }}
              >
                {detail.source || '—'}
              </span>
              <span style={{ color: C.ink3 }}>保存时间</span>
              <span className="gb-mono" style={{ color: C.ink2 }}>
                {detail.saved_at ? fmtAgo(detail.saved_at) : '—'}
              </span>
            </>
          )}
          {stats && (
            <>
              <span style={{ color: C.ink3 }}>扫描会话</span>
              <span className="gb-mono" style={{ color: C.ink2 }}>
                {stats.sessions_scanned}
              </span>
            </>
          )}
        </div>
      </div>

      <div
        style={{
          padding: '12px 18px',
          borderTop: `1px solid ${C.borderSoft}`,
          display: 'flex',
          gap: 8,
        }}
      >
        <a
          href={templateOriginalSrc(tpl.name)}
          target="_blank"
          rel="noreferrer"
          style={{
            padding: '7px 12px',
            borderRadius: 6,
            fontSize: 12,
            fontWeight: 500,
            color: C.ink2,
            background: C.surface,
            border: `1px solid ${C.border}`,
            textDecoration: 'none',
            textAlign: 'center',
          }}
        >
          查看原图
        </a>
        <span style={{ flex: 1 }} />
        <a
          href={templateImgSrc(tpl.name)}
          download={`${tpl.name}.png`}
          style={{
            padding: '7px 12px',
            borderRadius: 6,
            fontSize: 12,
            fontWeight: 500,
            color: C.ink2,
            background: C.surface,
            border: `1px solid ${C.border}`,
            textDecoration: 'none',
            textAlign: 'center',
          }}
        >
          导出 PNG
        </a>
        <button
          type="button"
          onClick={handleDelete}
          disabled={busy}
          style={{
            padding: '7px 12px',
            borderRadius: 6,
            fontSize: 12,
            fontWeight: 500,
            color: C.error,
            background: C.surface,
            border: `1px solid ${C.border}`,
            cursor: 'pointer',
            fontFamily: 'inherit',
          }}
        >
          {busy ? '…' : '删除'}
        </button>
      </div>
    </div>
  )
}

function Stat({
  label, value, sub, mono, color,
}: {
  label: string
  value: string | number
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
          fontSize: 17,
          fontWeight: 600,
          color: color || C.ink,
          display: 'flex',
          alignItems: 'baseline',
          gap: 4,
        }}
      >
        {value}
        {sub && (
          <span style={{ fontSize: 11, color: C.ink3, fontWeight: 400 }}>{sub}</span>
        )}
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

// ─── Upload + cropper modal ────────────────────────────────────────────
function UploadCropperModal({
  onClose,
  onUploaded,
}: {
  onClose: () => void
  onUploaded: (name: string) => void
}) {
  const emulators = useAppStore((s) => s.emulators)
  const runningInstances = useMemo(() => emulators.filter((e) => e.running), [emulators])

  const [name, setName] = useState('')
  const [overwrite, setOverwrite] = useState(false)
  const [src, setSrc] = useState<{ kind: 'instance' | 'upload'; instance?: number; file?: File }>(
    { kind: runningInstances.length > 0 ? 'instance' : 'upload', instance: runningInstances[0]?.index },
  )
  const [rect, setRect] = useState<[number, number, number, number]>([0.45, 0.62, 0.62, 0.72])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [imgSize, setImgSize] = useState<[number, number]>([1280, 720])

  // upload mode: load file as data URL preview
  useEffect(() => {
    if (src.kind !== 'upload' || !src.file) return
    const reader = new FileReader()
    reader.onload = () => setPreviewUrl(String(reader.result))
    reader.readAsDataURL(src.file)
  }, [src.kind, src.file])

  // instance mode: fetch a screenshot via /api/screenshot
  useEffect(() => {
    if (src.kind !== 'instance' || src.instance == null) {
      setPreviewUrl(null)
      return
    }
    setPreviewUrl(`/api/screenshot/${src.instance}?t=${Date.now()}`)
    setImgSize([1280, 720])
  }, [src.kind, src.instance])

  const handleSubmit = async () => {
    if (!name.trim()) {
      setErr('请填模版 ID')
      return
    }
    let blob: Blob | undefined
    let imgW = imgSize[0]
    let imgH = imgSize[1]
    if (src.kind === 'upload') {
      if (!src.file) {
        setErr('请选择文件')
        return
      }
      blob = src.file
    } else {
      // 抓帧 via /api/screenshot
      if (src.instance == null) {
        setErr('请选实例')
        return
      }
      try {
        const r = await fetch(`/api/screenshot/${src.instance}`)
        if (!r.ok) throw new Error(`screenshot ${r.status}`)
        blob = await r.blob()
      } catch (e) {
        setErr(`抓帧失败: ${String(e).slice(0, 120)}`)
        return
      }
    }

    // 计算 crop bbox (像素)
    const crop = {
      x: Math.round(rect[0] * imgW),
      y: Math.round(rect[1] * imgH),
      w: Math.round((rect[2] - rect[0]) * imgW),
      h: Math.round((rect[3] - rect[1]) * imgH),
    }

    setBusy(true)
    setErr(null)
    try {
      const r = await uploadTemplate({
        name: name.trim(),
        file: blob!,
        overwrite,
        crop,
      })
      onUploaded(r.name)
    } catch (e) {
      setErr(`上传失败: ${String(e).slice(0, 200)}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      onClick={onClose}
      style={{
        position: 'absolute',
        inset: 0,
        background: 'rgba(20,20,18,.55)',
        backdropFilter: 'blur(6px)',
        zIndex: 100,
        display: 'grid',
        placeItems: 'center',
        padding: 40,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 'min(960px, 100%)',
          maxHeight: '100%',
          background: C.surface,
          borderRadius: 10,
          overflow: 'hidden',
          border: `1px solid ${C.border}`,
          boxShadow: '0 20px 50px rgba(0,0,0,.25)',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <div
          style={{
            padding: '12px 18px',
            borderBottom: `1px solid ${C.borderSoft}`,
            display: 'flex',
            alignItems: 'center',
            gap: 10,
          }}
        >
          <span style={{ fontSize: 13, fontWeight: 600 }}>新增模版 · 上传裁切</span>
          <span style={{ flex: 1 }} />
          <button
            type="button"
            onClick={onClose}
            style={{
              fontSize: 13,
              color: C.ink3,
              padding: '4px 8px',
              borderRadius: 5,
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              fontFamily: 'inherit',
            }}
          >
            ✕
          </button>
        </div>

        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 280px',
            minHeight: 460,
          }}
        >
          <div style={{ background: '#0a0a08', position: 'relative' }}>
            {previewUrl ? (
              <img
                src={previewUrl}
                alt=""
                onLoad={(e) => {
                  const img = e.currentTarget as HTMLImageElement
                  setImgSize([img.naturalWidth, img.naturalHeight])
                }}
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
                  color: 'rgba(255,255,255,.4)',
                  fontSize: 11,
                  fontFamily: C.fontMono,
                }}
              >
                请先选实例 / 选文件
              </div>
            )}
            <RecogRectEditor
              rect={rect}
              onChange={setRect}
              color="#f59e0b"
              label={`crop ${Math.round((rect[2] - rect[0]) * imgSize[0])}×${Math.round(
                (rect[3] - rect[1]) * imgSize[1],
              )}`}
              fillOpacity={0.18}
            />
          </div>

          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              borderLeft: `1px solid ${C.borderSoft}`,
            }}
          >
            <div style={{ padding: 14, borderBottom: `1px solid ${C.borderSoft}` }}>
              <SectionLabel>来源</SectionLabel>
              <div
                style={{
                  display: 'flex',
                  background: C.surface2,
                  padding: 2,
                  borderRadius: 5,
                  gap: 1,
                }}
              >
                {(
                  [
                    ['instance', '实例'],
                    ['upload', '上传'],
                  ] as const
                ).map(([k, l]) => (
                  <button
                    key={k}
                    type="button"
                    onClick={() => setSrc((p) => ({ ...p, kind: k }))}
                    style={{
                      flex: 1,
                      padding: '4px 0',
                      borderRadius: 3,
                      fontSize: 11.5,
                      fontWeight: src.kind === k ? 600 : 500,
                      color: src.kind === k ? C.ink : C.ink3,
                      background: src.kind === k ? C.surface : 'transparent',
                      border: 'none',
                      cursor: 'pointer',
                      fontFamily: 'inherit',
                    }}
                  >
                    {l}
                  </button>
                ))}
              </div>
              {src.kind === 'instance' && (
                <div style={{ marginTop: 8, display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                  {runningInstances.length === 0 && (
                    <div style={{ fontSize: 11, color: C.ink4 }}>没运行中的实例</div>
                  )}
                  {runningInstances.map((e) => {
                    const on = src.instance === e.index
                    return (
                      <button
                        key={e.index}
                        type="button"
                        onClick={() => setSrc({ kind: 'instance', instance: e.index })}
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
              )}
              {src.kind === 'upload' && (
                <label
                  style={{
                    marginTop: 8,
                    padding: 16,
                    textAlign: 'center',
                    border: `1.5px dashed ${C.border}`,
                    borderRadius: 6,
                    fontSize: 11.5,
                    color: C.ink3,
                    cursor: 'pointer',
                    display: 'block',
                  }}
                >
                  {src.file ? src.file.name : '点击选择 PNG / JPG'}
                  <input
                    type="file"
                    accept="image/*"
                    style={{ display: 'none' }}
                    onChange={(e) => {
                      const f = e.target.files?.[0]
                      if (f) setSrc((p) => ({ ...p, kind: 'upload', file: f }))
                    }}
                  />
                </label>
              )}
            </div>

            <div style={{ padding: 14, borderBottom: `1px solid ${C.borderSoft}` }}>
              <SectionLabel>裁切框 (归一化)</SectionLabel>
              <div
                className="gb-mono"
                style={{ fontSize: 11, color: C.ink2, lineHeight: 1.6 }}
              >
                <div>
                  x1, y1 = ({rect[0].toFixed(3)}, {rect[1].toFixed(3)})
                </div>
                <div>
                  x2, y2 = ({rect[2].toFixed(3)}, {rect[3].toFixed(3)})
                </div>
                <div style={{ color: C.ink3, marginTop: 4 }}>
                  ≈ {Math.round((rect[2] - rect[0]) * imgSize[0])}×
                  {Math.round((rect[3] - rect[1]) * imgSize[1])} px
                </div>
              </div>
            </div>

            <div style={{ padding: 14, flex: 1 }}>
              <SectionLabel>模版 ID</SectionLabel>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="my_new_template"
                className="gb-mono"
                style={{
                  width: '100%',
                  padding: '6px 9px',
                  fontSize: 12,
                  borderRadius: 5,
                  background: C.surface2,
                  border: `1px solid ${C.border}`,
                  color: C.ink,
                  outline: 'none',
                  boxSizing: 'border-box',
                  fontFamily: 'inherit',
                }}
              />
              <label
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  marginTop: 10,
                  fontSize: 11.5,
                  color: C.ink2,
                  cursor: 'pointer',
                }}
              >
                <input
                  type="checkbox"
                  checked={overwrite}
                  onChange={(e) => setOverwrite(e.target.checked)}
                />
                若同名存在, 覆盖
              </label>
              {err && (
                <div
                  className="gb-mono"
                  style={{
                    marginTop: 10,
                    fontSize: 11,
                    color: C.error,
                    wordBreak: 'break-word',
                  }}
                >
                  {err}
                </div>
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
                onClick={onClose}
                style={{
                  flex: 1,
                  padding: '7px 0',
                  borderRadius: 5,
                  fontSize: 12,
                  color: C.ink2,
                  background: C.surface,
                  border: `1px solid ${C.border}`,
                  cursor: 'pointer',
                  fontFamily: 'inherit',
                }}
              >
                取消
              </button>
              <button
                type="button"
                onClick={handleSubmit}
                disabled={busy}
                style={{
                  flex: 1,
                  padding: '7px 0',
                  borderRadius: 5,
                  fontSize: 12,
                  fontWeight: 600,
                  color: '#f0eee9',
                  background: C.ink,
                  border: 'none',
                  cursor: busy ? 'wait' : 'pointer',
                  fontFamily: 'inherit',
                  opacity: busy ? 0.7 : 1,
                }}
              >
                {busy ? '上传中…' : '保存'}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
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
      {children}
    </div>
  )
}

// 通用 ALL_PREPROC import to avoid TS unused warning if config changes
void ALL_PREPROC
