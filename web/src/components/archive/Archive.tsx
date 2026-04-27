/**
 * 决策档案 — 历史会话浏览 + 决策详情.
 *
 * 两层导航:
 *   1. 会话列表 (默认进入): 当前会话 + 历史会话, 点进去
 *   2. 会话内: 左边决策列表 (按实例分组) + 右边决策详情 (3 联截图 + 5 层 Tier)
 */
import { useEffect, useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import {
  fetchSessions,
  fetchDecisions,
  fetchDecisionData,
  decisionImgSrc,
  type SessionItem,
  type DecisionListItem,
  type DecisionData,
  type DecisionTier,
} from '@/lib/archiveApi'

const PHASE_LABEL: Record<string, string> = {
  P0: '加速器校验', P1: '启动游戏', P2: '清弹窗',
  P3a: '队长创建', P3b: '队员加入', P4: '选地图开打',
}

function fmtTime(ts: number): string {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false })
}
function fmtTimeShort(ts: number): string {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour12: false })
}
function outcomeText(o: string): string {
  if (!o) return '—'
  if (o === 'tapped') return '已点击'
  if (o === 'phase_next') return '阶段完成'
  if (o === 'phase_done') return '全流程完成'
  if (o === 'phase_fail') return '阶段失败'
  if (o === 'no_target') return '无目标'
  if (o === 'loop_blocked') return '同坐标连击拦下'
  if (o === 'lobby_confirmed_quad') return '大厅确认 (四元)'
  if (o === 'lobby_confirmed_legacy') return '大厅 (兜底)'
  if (o.startsWith('lobby_pending')) return o.replace('lobby_pending_', '大厅判定 ')
  if (o === 'login_timeout_fail') return '登录超时'
  if (o === 'dead_screen') return '死屏'
  if (o === 'game_restart') return '请求重启'
  if (o === 'vpn_already_connected') return 'VPN 已连'
  if (o === 'vpn_broadcast_ok') return 'VPN 广播成功'
  if (o === 'vpn_ui_ok') return 'VPN UI 启动'
  if (o === 'vpn_all_failed') return 'VPN 失败'
  if (o.includes('hit')) return '模板命中'
  return o
}

export function Archive() {
  const [sessions, setSessions] = useState<SessionItem[]>([])
  const [activeSession, setActiveSession] = useState<string | null>(null)   // null = 还没选
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    setLoading(true)
    fetchSessions()
      .then((r) => {
        setSessions(r.sessions)
        // 自动进入当前会话
        if (r.current_session) setActiveSession(r.current_session)
      })
      .finally(() => setLoading(false))
  }, [])

  // 切到会话内
  if (activeSession !== null) {
    return (
      <SessionView
        session={activeSession}
        onBack={() => setActiveSession(null)}
        sessions={sessions}
      />
    )
  }

  // 会话列表
  return (
    <div className="flex flex-col gap-3 h-full min-h-0">
      <div className="flex items-center gap-3">
        <h2 className="text-base font-semibold">决策档案</h2>
        <span className="text-xs text-muted-foreground">{sessions.length} 个会话</span>
      </div>
      <div className="flex-1 overflow-y-auto rounded-lg border border-border bg-card">
        {loading ? (
          <div className="p-6 text-xs text-muted-foreground text-center">加载中…</div>
        ) : sessions.length === 0 ? (
          <div className="p-6 text-xs text-muted-foreground text-center">没有历史会话 — 跑一次脚本就有了</div>
        ) : (
          <ul className="divide-y divide-border">
            {sessions.map((s) => (
              <li key={s.session}>
                <button
                  onClick={() => setActiveSession(s.session)}
                  className={`w-full text-left px-4 py-3 hover:bg-zinc-50 transition flex items-center gap-3 ${
                    s.is_current ? 'bg-emerald-50/40' : ''
                  }`}
                >
                  <span
                    className={`inline-block w-2 h-2 rounded-full ${
                      s.is_current ? 'bg-emerald-500 animate-pulse' : 'bg-zinc-300'
                    }`}
                  />
                  <span className="font-mono text-sm flex-1">
                    {s.session}{s.is_current && <span className="ml-2 text-emerald-700 text-xs">(当前)</span>}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {s.decision_count} 决策
                  </span>
                  <span className="text-xs text-muted-foreground font-mono">
                    {fmtTime(s.mtime)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}


function SessionView({
  session, onBack, sessions,
}: {
  session: string
  onBack: () => void
  sessions: SessionItem[]
}) {
  const [items, setItems] = useState<DecisionListItem[]>([])
  const [loading, setLoading] = useState(false)
  const [filterInst, setFilterInst] = useState<number | null>(null)
  const [selectedId, setSelectedId] = useState<string>('')
  const [tick, setTick] = useState(0)

  const sessionMeta = sessions.find((s) => s.session === session)
  const isCurrent = sessionMeta?.is_current ?? false

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    // 当前会话 → 用 list_recent (内存索引); 历史 → 扫磁盘
    fetchDecisions({
      session: isCurrent ? '' : session,
      instance: filterInst ?? undefined,
      limit: 500,
    })
      .then((r) => { if (!cancelled) setItems(r.items) })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [session, filterInst, isCurrent, tick])

  // 自动刷新 (仅当前会话)
  useEffect(() => {
    if (!isCurrent) return
    const t = window.setInterval(() => setTick((x) => x + 1), 3000)
    return () => window.clearInterval(t)
  }, [isCurrent])

  const byInst = useMemo(() => {
    const m: Record<number, DecisionListItem[]> = {}
    for (const it of items) {
      const k = it.instance
      if (!m[k]) m[k] = []
      m[k].push(it)
    }
    return m
  }, [items])

  // 每实例当前进度: 最近一条决策的 phase + 各 phase 决策计数
  const progressByInst = useMemo(() => {
    const m: Record<number, { current: string; counts: Record<string, number>; total: number; toLobby: number }> = {}
    for (const [idx, list] of Object.entries(byInst)) {
      const counts: Record<string, number> = {}
      let toLobby = 0
      for (const d of list) {
        counts[d.phase] = (counts[d.phase] || 0) + 1
        if (d.outcome.startsWith('lobby_confirmed')) toLobby += 1
      }
      const lastByTs = list.slice().sort((a, b) => b.created - a.created)[0]
      m[Number(idx)] = {
        current: lastByTs?.phase || '',
        counts,
        total: list.length,
        toLobby,
      }
    }
    return m
  }, [byInst])

  const PHASE_ORDER = ['P0', 'P1', 'P2', 'P3a', 'P3b', 'P4']
  const PHASE_NAME: Record<string, string> = {
    P0: '加速器', P1: '启动', P2: '清弹窗', P3a: '建队', P3b: '加入', P4: '选地图',
  }

  return (
    <div className="flex flex-col gap-3 h-full min-h-0">
      <div className="flex items-center gap-2 flex-wrap">
        <Button variant="ghost" size="sm" onClick={onBack}>← 会话列表</Button>
        <span className="font-mono text-sm font-semibold">{session}</span>
        {isCurrent && <span className="text-emerald-700 text-xs">● 实时</span>}
        <span className="text-xs text-muted-foreground">{items.length} 决策</span>
        <span className="ml-2 text-xs text-muted-foreground">实例过滤:</span>
        <button
          onClick={() => setFilterInst(null)}
          className={`text-xs px-2 py-0.5 rounded border ${
            filterInst === null ? 'bg-blue-50 border-blue-300 text-blue-700' : 'border-border'
          }`}
        >
          全部
        </button>
        {Object.keys(byInst).map(Number).sort((a, b) => a - b).map((idx) => (
          <button
            key={idx}
            onClick={() => setFilterInst(idx === filterInst ? null : idx)}
            className={`text-xs px-2 py-0.5 rounded border font-mono ${
              filterInst === idx ? 'bg-blue-50 border-blue-300 text-blue-700' : 'border-border'
            }`}
          >
            #{idx}
          </button>
        ))}
        <Button variant="ghost" size="sm" onClick={() => setTick((x) => x + 1)} className="ml-auto">
          刷新
        </Button>
      </div>

      {/* 各实例进度概览 */}
      {Object.keys(byInst).length > 0 && (
        <div className="rounded-lg border border-border bg-card p-3 space-y-2">
          <div className="text-xs font-semibold text-foreground">实例进度</div>
          <div className="space-y-1.5">
            {Object.keys(byInst).map(Number).sort((a, b) => a - b).map((idx) => {
              const p = progressByInst[idx]
              return (
                <div key={idx} className="flex items-center gap-2 text-xs">
                  <span className="font-mono font-semibold w-8">#{idx}</span>
                  <div className="flex gap-1 flex-1">
                    {PHASE_ORDER.map((ph) => {
                      const cnt = p?.counts[ph] || 0
                      const isCur = p?.current === ph
                      return (
                        <div
                          key={ph}
                          className={`flex-1 px-2 py-1 rounded text-center text-[11px] border transition ${
                            isCur
                              ? 'bg-primary text-primary-foreground border-primary font-semibold'
                              : cnt > 0
                                ? 'bg-success-muted text-success border-success/30'
                                : 'bg-card border-border text-muted-foreground'
                          }`}
                          title={`${ph} ${PHASE_NAME[ph]}: ${cnt} 决策${isCur ? ' (当前)' : ''}`}
                        >
                          <span className="font-mono">{ph}</span>
                          <span className="ml-1 opacity-70">{cnt}</span>
                        </div>
                      )
                    })}
                  </div>
                  <span className="text-muted-foreground text-[11px]">
                    {p?.toLobby || 0} 次到大厅
                  </span>
                </div>
              )
            })}
          </div>
        </div>
      )}

      <div className="flex-1 grid gap-3 min-h-0" style={{ gridTemplateColumns: '380px 1fr' }}>
        {/* 左: 决策列表 */}
        <div className="rounded-lg border border-border bg-card overflow-y-auto">
          {loading ? (
            <div className="p-6 text-xs text-muted-foreground text-center">加载中…</div>
          ) : items.length === 0 ? (
            <div className="p-6 text-xs text-muted-foreground text-center">
              没有决策 — 跑一下 runner 才有数据
            </div>
          ) : (
            Object.keys(byInst).map(Number).sort((a, b) => a - b).map((idx) => (
              <div key={idx}>
                <div className="sticky top-0 bg-secondary border-b border-border px-3 py-1.5 text-[11px] font-semibold flex items-center gap-2">
                  <span className="font-mono">实例 #{idx}</span>
                  <span className="text-muted-foreground">{byInst[idx].length} 决策</span>
                </div>
                <ul className="divide-y divide-border">
                  {byInst[idx].map((it) => (
                    <li key={it.id}>
                      <button
                        onClick={() => setSelectedId(it.id)}
                        className={`w-full text-left px-3 py-1.5 hover:bg-zinc-50 ${
                          selectedId === it.id ? 'bg-blue-50' : ''
                        }`}
                      >
                        <div className="flex items-center gap-2 text-[11px]">
                          <OutcomeIcon outcome={it.outcome} verifySuccess={it.verify_success ?? null} />
                          <span className="font-mono text-zinc-500">{fmtTimeShort(it.created)}</span>
                          <span className="font-mono">{it.phase}</span>
                          <span className="text-muted-foreground">R{it.round}</span>
                        </div>
                        <div className="text-[11px] text-zinc-600 truncate mt-0.5">
                          {outcomeText(it.outcome)}
                          {it.tap_target && <span className="ml-2 text-blue-600">→ {it.tap_target}</span>}
                        </div>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ))
          )}
        </div>

        {/* 右: 决策详情 */}
        <DecisionDetail decisionId={selectedId} session={isCurrent ? '' : session} />
      </div>
    </div>
  )
}


function OutcomeIcon({ outcome, verifySuccess }: { outcome: string; verifySuccess: boolean | null }) {
  let cls = 'bg-zinc-300'
  let icon = '·'
  if (outcome.startsWith('lobby_confirmed') || outcome === 'phase_next' || outcome === 'phase_done') {
    cls = 'bg-emerald-500 text-white'
    icon = '✓'
  } else if (verifySuccess === true) {
    cls = 'bg-emerald-500 text-white'
    icon = '✓'
  } else if (verifySuccess === false || outcome === 'phase_fail') {
    cls = 'bg-red-500 text-white'
    icon = '✗'
  } else if (outcome === 'loop_blocked') {
    cls = 'bg-red-500 text-white'
    icon = '⚠'
  } else if (outcome.startsWith('lobby_pending')) {
    cls = 'bg-amber-400 text-white'
    icon = '◔'
  } else if (outcome === 'no_target') {
    cls = 'bg-zinc-400 text-white'
    icon = '○'
  } else if (outcome === 'tapped') {
    cls = 'bg-blue-500 text-white'
    icon = '⊕'
  }
  return (
    <span className={`inline-flex items-center justify-center w-4 h-4 rounded-full text-[10px] font-bold ${cls}`}>
      {icon}
    </span>
  )
}


function DecisionDetail({ decisionId, session }: { decisionId: string; session: string }) {
  const [data, setData] = useState<DecisionData | null>(null)
  const [loading, setLoading] = useState(false)
  const [lightboxSrc, setLightboxSrc] = useState<string>('')

  useEffect(() => {
    if (!decisionId) {
      setData(null)
      return
    }
    let cancelled = false
    setLoading(true)
    fetchDecisionData(decisionId, session)
      .then((d) => { if (!cancelled) setData(d) })
      .catch(() => { if (!cancelled) setData(null) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [decisionId, session])

  useEffect(() => {
    if (!lightboxSrc) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setLightboxSrc('') }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [lightboxSrc])

  if (!decisionId) {
    return (
      <div className="rounded-lg border border-dashed border-border bg-card flex items-center justify-center text-sm text-muted-foreground">
        从左边选一条决策看详情
      </div>
    )
  }
  if (loading) {
    return (
      <div className="rounded-lg border border-border bg-card flex items-center justify-center text-sm text-muted-foreground">
        加载中…
      </div>
    )
  }
  if (!data) {
    return (
      <div className="rounded-lg border border-border bg-card flex items-center justify-center text-sm text-muted-foreground">
        加载失败
      </div>
    )
  }

  const phaseLab = PHASE_LABEL[data.phase] || data.phase

  return (
    <div className="rounded-lg border border-border bg-card flex flex-col overflow-hidden">
      <div className="px-3 py-2 border-b border-border flex items-center gap-3 text-xs">
        <OutcomeIcon outcome={data.outcome} verifySuccess={data.verify?.success ?? null} />
        <span className="font-mono">{data.phase}</span>
        <span className="text-muted-foreground">{phaseLab}</span>
        <span className="text-muted-foreground">实例 #{data.instance} · R{data.round}</span>
        <span className="font-mono text-[11px] text-zinc-400 ml-auto">{data.id}</span>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {/* 三联截图 (点击放大) */}
        <div className="grid gap-2" style={{ gridTemplateColumns: '1fr 1fr 1fr' }}>
          {(() => {
            const original = data.input_image ? decisionImgSrc(data.id, data.input_image, session) : ''
            const annot = data.tap?.annot_image
              ? decisionImgSrc(data.id, data.tap.annot_image, session)
              : (data.tiers.find((t) => t.yolo_annot_image)?.yolo_annot_image
                  ? decisionImgSrc(data.id, data.tiers.find((t) => t.yolo_annot_image)!.yolo_annot_image!, session)
                  : '')
            const ocr = data.tiers.find((t) => t.ocr_roi_image)?.ocr_roi_image
              ? decisionImgSrc(data.id, data.tiers.find((t) => t.ocr_roi_image)!.ocr_roi_image!, session)
              : ''
            return (
              <>
                <ScreenshotPanel title="原图" src={original} onClick={() => original && setLightboxSrc(original)} />
                <ScreenshotPanel title="标注 (YOLO + 点击点)" src={annot} onClick={() => annot && setLightboxSrc(annot)} />
                <ScreenshotPanel title="OCR ROI" src={ocr} onClick={() => ocr && setLightboxSrc(ocr)} />
              </>
            )
          })()}
        </div>

        {/* 总览 */}
        <div className="rounded border border-border bg-zinc-50 p-2 text-[11px] space-y-0.5">
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            <span>结果: <b>{outcomeText(data.outcome)}</b></span>
            {data.tap && (
              <span>
                点击: <span className="font-mono">{data.tap.method}</span> @ ({data.tap.x}, {data.tap.y})
                {data.tap.target_class && <span className="ml-1 text-blue-700">[{data.tap.target_class}]</span>}
              </span>
            )}
            {data.verify && data.verify.success !== null && (
              <span>验证: {data.verify.success ? '✓ 通过' : '✗ 失败'} (phash dist={data.verify.distance})</span>
            )}
            <span>时间: {fmtTime(data.created)}</span>
          </div>
          {data.note && <div className="text-muted-foreground">{data.note}</div>}
        </div>

        {/* 5 层 Tier */}
        <div>
          <div className="text-xs font-semibold mb-1">识别证据 ({data.tiers.length} 层)</div>
          <div className="space-y-1.5">
            {data.tiers.map((t, i) => <TierRow key={i} t={t} />)}
          </div>
        </div>
      </div>

      {/* Lightbox */}
      {lightboxSrc && <Lightbox src={lightboxSrc} onClose={() => setLightboxSrc('')} />}
    </div>
  )
}


function ScreenshotPanel({ title, src, onClick }: { title: string; src: string; onClick?: () => void }) {
  return (
    <div className="rounded border border-border bg-foreground/95 overflow-hidden">
      <div className="bg-foreground text-background text-[10px] px-2 py-1">{title}</div>
      {src ? (
        <img
          src={src}
          alt={title}
          className="w-full max-h-[260px] object-contain cursor-zoom-in hover:opacity-90"
          onClick={onClick}
        />
      ) : (
        <div className="text-background/50 text-xs text-center py-12">— 无 —</div>
      )}
    </div>
  )
}


function Lightbox({ src, onClose }: { src: string; onClose: () => void }) {
  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [drag, setDrag] = useState<{ sx: number; sy: number; px: number; py: number } | null>(null)

  function onWheel(e: React.WheelEvent) {
    e.preventDefault()
    const delta = e.deltaY < 0 ? 1.15 : 1 / 1.15
    setZoom((z) => Math.max(0.3, Math.min(8, z * delta)))
  }
  function onMouseDown(e: React.MouseEvent) {
    setDrag({ sx: e.clientX, sy: e.clientY, px: pan.x, py: pan.y })
  }
  function onMouseMove(e: React.MouseEvent) {
    if (!drag) return
    setPan({ x: drag.px + (e.clientX - drag.sx), y: drag.py + (e.clientY - drag.sy) })
  }

  return (
    <div
      className="fixed inset-0 z-50 bg-foreground/95 flex flex-col"
      onWheel={onWheel}
      onMouseDown={onMouseDown}
      onMouseMove={onMouseMove}
      onMouseUp={() => setDrag(null)}
      onMouseLeave={() => setDrag(null)}
      onClick={(e) => {
        // 点击空白关闭 (但不在图上)
        if ((e.target as HTMLElement).tagName !== 'IMG') onClose()
      }}
    >
      <div className="flex items-center gap-2 p-2 text-background bg-foreground">
        <span className="text-xs font-mono">{Math.round(zoom * 100)}%</span>
        <button
          onClick={(e) => { e.stopPropagation(); setZoom(1); setPan({ x: 0, y: 0 }) }}
          className="text-xs underline opacity-80 hover:opacity-100"
        >重置</button>
        <span className="text-[10px] opacity-60 ml-2">滚轮缩放 · 拖动平移 · ESC / 点空白 关闭</span>
        <button onClick={onClose} className="ml-auto text-sm">✕</button>
      </div>
      <div className="flex-1 flex items-center justify-center overflow-hidden" style={{ cursor: drag ? 'grabbing' : 'grab' }}>
        <img
          src={src}
          alt="放大"
          draggable={false}
          style={{
            transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
            transformOrigin: 'center center',
            maxWidth: '95%', maxHeight: '95%',
            transition: drag ? 'none' : 'transform 0.05s',
          }}
        />
      </div>
    </div>
  )
}

function TierRow({ t }: { t: DecisionTier }) {
  const winner = t.early_exit
  return (
    <div className={`rounded border p-2 text-xs ${
      winner ? 'border-emerald-300 bg-emerald-50/60' : 'border-zinc-200 bg-zinc-50/40'
    }`}>
      <div className="flex items-center gap-2">
        <span className={`inline-block w-1.5 h-1.5 rounded-full ${winner ? 'bg-emerald-500' : 'bg-zinc-300'}`} />
        <span className="font-semibold">{t.name}</span>
        {winner && <span className="text-emerald-700 text-[10px] font-bold">[赢]</span>}
        <span className="ml-auto font-mono text-[10px] text-zinc-500">{t.duration_ms.toFixed(1)}ms</span>
      </div>
      {t.note && <div className="text-[11px] text-zinc-600 mt-0.5">{t.note}</div>}
      {t.templates && t.templates.length > 0 && (
        <ul className="mt-1 space-y-0.5">
          {t.templates.map((tp, i) => (
            <li key={i} className="flex items-center gap-2 font-mono text-[10px]">
              <span className={tp.hit ? 'text-emerald-600' : 'text-zinc-400'}>{tp.hit ? '✓' : '·'}</span>
              <span className="flex-1 truncate">{tp.name}</span>
              <span className="text-zinc-500">{tp.score.toFixed(3)}</span>
            </li>
          ))}
        </ul>
      )}
      {t.yolo_detections && t.yolo_detections.length > 0 && (
        <ul className="mt-1 space-y-0.5">
          {t.yolo_detections.slice(0, 6).map((d, i) => (
            <li key={i} className="flex items-center gap-2 font-mono text-[10px]">
              <span className="text-blue-600">▣</span>
              <span className="flex-1">{d.cls}</span>
              <span className="text-zinc-500">{d.conf.toFixed(3)}</span>
            </li>
          ))}
        </ul>
      )}
      {t.memory_hit && (
        <div className="mt-1 font-mono text-[10px] text-amber-700">
          ◆ phash {t.memory_phash_query} → ({t.memory_hit.cx}, {t.memory_hit.cy})
        </div>
      )}
    </div>
  )
}
