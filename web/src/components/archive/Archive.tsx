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
import {
  createOracle,
  replayArbitrary,
  type ReplayResult,
  type OracleCorrect,
} from '@/lib/oracleApi'

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
  // 通用
  if (o === 'tapped') return '已点击'
  if (o === 'retry') return '重试一帧'
  if (o === 'wait') return '等待中'
  if (o === 'phase_next') return '阶段完成 → 下一阶段'
  if (o === 'phase_done') return '全流程完成'
  if (o === 'phase_fail') return '阶段失败'
  if (o === 'phase_exception') return '阶段异常 (看详情 note)'
  // P0 加速器
  if (o === 'vpn_already_connected') return 'VPN 已连接'
  if (o === 'vpn_broadcast_ok') return 'VPN 广播启动成功'
  if (o === 'vpn_ui_ok') return 'VPN UI 启动成功'
  if (o === 'vpn_all_failed') return 'VPN 全部失败'
  // P1 启动游戏
  if (o === 'lobby_template_hit') return '大厅模板命中'
  if (o === 'popup_or_login_template_hit') return '弹窗/登录模板命中'
  if (o === 'yolo_dets_seen') return 'YOLO 看到目标'
  if (o === 'waiting_ui') return '等待 UI 出现'
  // P2 清弹窗
  if (o === 'no_target') return '无目标 (5 层都空)'
  if (o === 'loop_blocked') return '同坐标连击 → 加黑名单'
  if (o === 'lobby_confirmed_quad') return '大厅确认 (四元)'
  if (o === 'lobby_confirmed_legacy') return '大厅 (兜底命中模板)'
  if (o.startsWith('lobby_pending_')) return '大厅判定 ' + o.replace('lobby_pending_', '')
  if (o === 'login_timeout_fail') return '登录超时 60s → 重启'
  if (o === 'dead_screen') return '死屏 (>12 轮无目标)'
  if (o === 'game_restart') return '请求重启游戏'
  // P3a 队长
  if (o === 'team_create_ok') return '队伍创建成功'
  if (o === 'team_create_fail') return '队伍创建失败'
  if (o === 'team_create_exception') return '队伍创建异常'
  // P3b 队员
  if (o === 'team_join_ok') return '加入队伍成功'
  if (o === 'team_join_fail') return '加入队伍失败'
  if (o === 'team_join_no_scheme') return '没拿到 scheme'
  if (o === 'team_join_exception') return '加入队伍异常'
  // P4 选地图
  if (o === 'map_setup_ok') return '地图设置完成'
  if (o === 'map_setup_fail') return '地图设置失败'
  if (o === 'map_setup_exception') return '地图设置异常'
  return o
}

// 简短阶段名 (用于决策列表): P0 → 加速器 等
const PHASE_SHORT: Record<string, string> = {
  P0: '加速器', P1: '启动游戏', P2: '清弹窗',
  P3a: '队长建队', P3b: '队员加入', P4: '选地图',
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
                  className={`w-full text-left px-4 py-3 hover:bg-muted transition flex items-center gap-3 ${
                    s.is_current ? 'bg-success-muted' : ''
                  }`}
                >
                  <span
                    className={`inline-block w-2 h-2 rounded-full ${
                      s.is_current ? 'bg-success animate-pulse' : 'bg-muted-foreground/30'
                    }`}
                  />
                  <span className="font-mono text-sm flex-1">
                    {s.session}{s.is_current && <span className="ml-2 text-success text-xs">(当前)</span>}
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
    // 只在首次加载或换会话时显 loading; 自动刷新 (tick) 不闪屏
    setLoading((prev) => items.length === 0 ? true : prev)
    // 当前会话 → 用 list_recent (内存索引); 历史 → 扫磁盘
    // 不在 fetch 层 filter instance, 卡片网格永远显全部实例; 列表段渲染时再 filter
    fetchDecisions({
      session: isCurrent ? '' : session,
      limit: 500,
    })
      .then((r) => { if (!cancelled) setItems(r.items) })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session, isCurrent, tick])

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

  // 列表段用的 filtered: filterInst != null 则只保该实例的 key
  const byInstFiltered = useMemo(() => {
    if (filterInst === null) return byInst
    return filterInst in byInst ? { [filterInst]: byInst[filterInst] } : {}
  }, [byInst, filterInst])

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
        {isCurrent && <span className="text-success text-xs">● 实时</span>}
        <span className="text-xs text-muted-foreground">{items.length} 决策</span>
        <span className="ml-2 text-xs text-muted-foreground">实例过滤:</span>
        <button
          onClick={() => setFilterInst(null)}
          className={`text-xs px-2 py-0.5 rounded border ${
            filterInst === null ? 'bg-info/10 border-info/30 text-info' : 'border-border'
          }`}
        >
          全部
        </button>
        {Object.keys(byInst).map(Number).sort((a, b) => a - b).map((idx) => (
          <button
            key={idx}
            onClick={() => setFilterInst(idx === filterInst ? null : idx)}
            className={`text-xs px-2 py-0.5 rounded border font-mono ${
              filterInst === idx ? 'bg-info/10 border-info/30 text-info' : 'border-border'
            }`}
          >
            #{idx}
          </button>
        ))}
        <Button variant="ghost" size="sm" onClick={() => setTick((x) => x + 1)} className="ml-auto">
          刷新
        </Button>
      </div>

      {/* 实例进度 — 卡片网格 (每实例 1 卡, 6 阶段圆点 + 当前 + 总决策数) */}
      {Object.keys(byInst).length > 0 && (
        <div
          className="grid gap-2"
          style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))' }}
        >
          {Object.keys(byInst).map(Number).sort((a, b) => a - b).map((idx) => {
            const p = progressByInst[idx]
            const curIdx = PHASE_ORDER.indexOf(p?.current || '')
            const isSelected = filterInst === idx
            return (
              <button
                key={idx}
                onClick={() => setFilterInst(idx === filterInst ? null : idx)}
                className={`text-left rounded-lg border bg-card hover:bg-muted/30 transition p-2.5 ${
                  isSelected ? 'border-primary shadow-sm ring-1 ring-primary/20' : 'border-border'
                }`}
              >
                <div className="flex items-center justify-between mb-1.5">
                  <span className="font-mono font-bold text-sm">#{idx}</span>
                  <span className="text-[10px] text-muted-foreground">
                    {p?.total || 0} 决策{(p?.toLobby || 0) > 0 && ` · 大厅 ${p.toLobby}`}
                  </span>
                </div>
                <div className="flex items-center gap-1 mb-1.5">
                  {PHASE_ORDER.map((ph, i) => {
                    const cnt = p?.counts[ph] || 0
                    const isCur = p?.current === ph
                    const isPast = i < curIdx
                    return (
                      <div key={ph} className="flex items-center" title={`${PHASE_NAME[ph]}: ${cnt} 决策`}>
                        <div
                          className={`w-2.5 h-2.5 rounded-full transition flex items-center justify-center ${
                            isCur
                              ? 'bg-primary animate-pulse'
                              : cnt > 0 || isPast
                                ? 'bg-success'
                                : 'bg-muted-foreground/20'
                          }`}
                        />
                        {i < PHASE_ORDER.length - 1 && (
                          <div className={`h-px w-2 ${
                            cnt > 0 && i < curIdx ? 'bg-success' : 'bg-muted-foreground/15'
                          }`} />
                        )}
                      </div>
                    )
                  })}
                </div>
                <div className="text-[11px] text-foreground/80 font-medium truncate">
                  {p?.current ? `${PHASE_NAME[p.current]} 阶段` : '未开始'}
                  {p?.current && (p?.counts[p.current] || 0) > 0 && (
                    <span className="text-muted-foreground"> · 第 {p.counts[p.current]} 帧</span>
                  )}
                </div>
              </button>
            )
          })}
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
          ) : Object.keys(byInstFiltered).length === 0 ? (
            <div className="p-6 text-xs text-muted-foreground text-center">
              实例 #{filterInst} 暂无决策 (点 "全部" 看所有)
            </div>
          ) : (
            Object.keys(byInstFiltered).map(Number).sort((a, b) => a - b).map((idx) => (
              <div key={idx}>
                <div className="sticky top-0 bg-secondary border-b border-border px-3 py-1.5 text-[11px] font-semibold flex items-center gap-2">
                  <span className="font-mono">实例 #{idx}</span>
                  <span className="text-muted-foreground">{byInstFiltered[idx].length} 决策</span>
                </div>
                <ul className="divide-y divide-border">
                  {byInstFiltered[idx].map((it) => (
                    <li key={it.id}>
                      <button
                        onClick={() => setSelectedId(it.id)}
                        className={`w-full text-left px-3 py-1.5 hover:bg-muted ${
                          selectedId === it.id ? 'bg-info/10' : ''
                        }`}
                      >
                        <div className="flex items-center gap-2 text-[11px]">
                          <OutcomeIcon outcome={it.outcome} verifySuccess={it.verify_success ?? null} />
                          <span className="font-mono text-muted-foreground">{fmtTimeShort(it.created)}</span>
                          <span className="font-mono font-semibold text-foreground">{it.phase}</span>
                          <span className="text-foreground/80">{PHASE_SHORT[it.phase] || ''}</span>
                          <span className="text-muted-foreground font-mono">R{it.round}</span>
                        </div>
                        <div className="text-[11px] text-foreground/70 truncate mt-0.5">
                          {outcomeText(it.outcome)}
                          {it.tap_target && <span className="ml-2 text-info">→ {it.tap_target}</span>}
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
  let cls = 'bg-muted-foreground/30'
  let icon = '·'
  if (outcome.startsWith('lobby_confirmed') || outcome === 'phase_next' || outcome === 'phase_done') {
    cls = 'bg-success text-white'
    icon = '✓'
  } else if (verifySuccess === true) {
    cls = 'bg-success text-white'
    icon = '✓'
  } else if (verifySuccess === false || outcome === 'phase_fail') {
    cls = 'bg-destructive text-white'
    icon = '✗'
  } else if (outcome === 'loop_blocked') {
    cls = 'bg-destructive text-white'
    icon = '⚠'
  } else if (outcome.startsWith('lobby_pending')) {
    cls = 'bg-warning text-white'
    icon = '◔'
  } else if (outcome === 'no_target') {
    cls = 'bg-muted-foreground/50 text-white'
    icon = '○'
  } else if (outcome === 'tapped') {
    cls = 'bg-info text-white'
    icon = '⊕'
  }
  return (
    <span className={`inline-flex items-center justify-center w-4 h-4 rounded-full text-[10px] font-bold ${cls}`}>
      {icon}
    </span>
  )
}


// outcome → flow_step 序号 (前端启发式映射, 用于在阶段步骤里高亮"目前到哪步")
const OUTCOME_TO_STEP_IDX: Record<string, number> = {
  // P0
  vpn_already_connected: 0,
  vpn_broadcast_ok: 1,
  vpn_ui_ok: 2,
  vpn_all_failed: 3,
  // P1 / 通用
  lobby_template_hit: 0,
  popup_or_login_template_hit: 1,
  yolo_dets_seen: 2,
  // P2
  lobby_confirmed_quad: 1,           // step 1: 守门 1 大厅判
  lobby_pending_1: 1, lobby_pending_2: 1,
  login_timeout_fail: 2,             // step 2: 登录守门
  tapped: 3,                         // step 3: 决策 → tap
  loop_blocked: 4,                   // step 4: 防死循环
  lobby_confirmed_legacy: 5,         // step 5: 兜底
  no_target: 5,
  dead_screen: 6,                    // step 6: 死屏
  // P3a / P3b / P4 → 简单按是否成功
  team_create_ok: 6, team_create_fail: 6, team_create_exception: 6,
  team_join_ok: 3, team_join_fail: 3, team_join_exception: 3,
  team_join_no_scheme: 0,
  map_setup_ok: 4, map_setup_fail: 4, map_setup_exception: 4,
}

interface PhaseDocData {
  key: string
  name: string
  description: string
  flow_steps: string[]
  max_rounds: number
}

// 决策详情进程级缓存 (跨组件 mount 保留, 列表刷新不丢已加载数据)
const _decisionCache = new Map<string, DecisionData>()

function DecisionDetail({ decisionId, session }: { decisionId: string; session: string }) {
  // SWR-style: 切换 id 时先用缓存的旧数据展示, 后台 revalidate
  const [data, setData] = useState<DecisionData | null>(() => _decisionCache.get(decisionId) || null)
  const [revalidating, setRevalidating] = useState(false)
  const [retryAttempt, setRetryAttempt] = useState(0)   // 0=没重试 1-3=正在第N次重试
  const [lastError, setLastError] = useState('')
  const [lightboxSrc, setLightboxSrc] = useState<string>('')
  const [phaseDoc, setPhaseDoc] = useState<PhaseDocData | null>(null)
  // Oracle 标注 / replay
  const [annotatorOpen, setAnnotatorOpen] = useState(false)
  const [replayResult, setReplayResult] = useState<ReplayResult | null>(null)
  const [replayLoading, setReplayLoading] = useState(false)

  useEffect(() => {
    if (!decisionId) {
      setData(null); setLastError(''); setRetryAttempt(0)
      return
    }
    let cancelled = false
    let retryTimer: number | undefined

    // 先用缓存替换 (id 变了 → 新缓存 / 命中即 stale-but-shown)
    const cached = _decisionCache.get(decisionId)
    if (cached) setData(cached)
    else setData(null)
    setLastError(''); setRetryAttempt(0)
    setRevalidating(true)

    const tryFetch = (attempt: number) => {
      if (cancelled) return
      fetchDecisionData(decisionId, session)
        .then((d) => {
          if (cancelled) return
          _decisionCache.set(decisionId, d)
          setData(d); setLastError(''); setRetryAttempt(0); setRevalidating(false)
        })
        .catch((e) => {
          if (cancelled) return
          const msg = String(e?.message || e)
          setLastError(msg)
          // 决策可能正在写入 (404 / 不完整 JSON), 1.5s 后重试, 最多 3 次
          if (attempt < 3) {
            setRetryAttempt(attempt)
            retryTimer = window.setTimeout(() => tryFetch(attempt + 1), 1500)
          } else {
            setRevalidating(false)
            setRetryAttempt(0)
          }
        })
    }
    tryFetch(1)

    return () => {
      cancelled = true
      if (retryTimer !== undefined) window.clearTimeout(retryTimer)
    }
  }, [decisionId, session])

  // 拉对应 phase 的 doc (description + flow_steps)
  useEffect(() => {
    if (!data?.phase) { setPhaseDoc(null); return }
    let cancelled = false
    fetch(`/api/runner/phase_doc/${encodeURIComponent(data.phase)}`)
      .then((r) => r.ok ? r.json() : null)
      .then((d) => { if (!cancelled && d) setPhaseDoc(d) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [data?.phase])

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
  // 没缓存数据 + 在重试 → "决策正在写入" 友好提示
  if (!data && retryAttempt > 0) {
    return (
      <div className="rounded-lg border border-border bg-card flex flex-col items-center justify-center gap-2 text-sm text-muted-foreground p-6">
        <div className="text-warning">决策正在写入磁盘…</div>
        <div className="text-xs">第 {retryAttempt}/3 次重试 (实时会话决策可能滞后)</div>
      </div>
    )
  }
  // 没缓存 + 重试也失败 (3 次后)
  if (!data && lastError) {
    return (
      <div className="rounded-lg border border-border bg-card flex flex-col items-center justify-center gap-2 text-sm text-muted-foreground p-6">
        <div className="text-destructive">加载失败</div>
        <div className="text-xs font-mono break-all max-w-md">{lastError.slice(0, 200)}</div>
        <div className="text-xs">如果是实时会话, 决策可能尚未写完, 稍后再点</div>
      </div>
    )
  }
  if (!data) {
    return (
      <div className="rounded-lg border border-border bg-card flex items-center justify-center text-sm text-muted-foreground">
        加载中…
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
        <span className="font-mono text-[11px] text-muted-foreground ml-auto">{data.id}</span>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {/* 阶段步骤 stepper — 高亮当前 outcome 推算的步骤 */}
        {phaseDoc && phaseDoc.flow_steps.length > 0 && (
          <div className="rounded-lg border border-border bg-secondary/50 p-3 space-y-2">
            <div className="flex items-center gap-2 text-xs">
              <span className="font-semibold text-foreground">
                {phaseDoc.name} · 流程步骤
              </span>
              {phaseDoc.description && (
                <span className="text-muted-foreground italic">{phaseDoc.description}</span>
              )}
            </div>
            <ol className="space-y-1">
              {phaseDoc.flow_steps.map((s, i) => {
                const curIdx = OUTCOME_TO_STEP_IDX[data.outcome]
                const isCur = curIdx === i
                const isPast = curIdx !== undefined && i < curIdx
                return (
                  <li
                    key={i}
                    className={`flex items-start gap-2 px-2 py-1 rounded text-[12px] transition ${
                      isCur
                        ? 'bg-primary/10 border-l-2 border-primary text-foreground font-medium'
                        : isPast
                          ? 'text-muted-foreground'
                          : 'text-muted-foreground/70'
                    }`}
                  >
                    <span className={`font-mono text-[10px] w-5 shrink-0 ${
                      isCur ? 'text-primary font-bold' : 'text-muted-foreground'
                    }`}>
                      {isCur ? '►' : isPast ? '✓' : (i + 1)}
                    </span>
                    <span>{s}</span>
                  </li>
                )
              })}
            </ol>
          </div>
        )}

        {/* Oracle 工具栏 */}
        <div className="flex items-center gap-2 -mb-1">
          <button
            onClick={() => setAnnotatorOpen(true)}
            className="text-xs px-2.5 py-1 rounded border border-amber-500/40 bg-amber-500/10 text-amber-300 hover:bg-amber-500/20 font-medium"
            title="此决策有错？标注正确位置 → 加进 Oracle 集"
          >
            🐞 标记错误
          </button>
          <button
            onClick={async () => {
              setReplayLoading(true)
              try {
                const r = await replayArbitrary(decisionId, session)
                setReplayResult(r)
              } catch (e) {
                setReplayResult({ ok: false, error: String(e) })
              } finally {
                setReplayLoading(false)
              }
            }}
            className="text-xs px-2.5 py-1 rounded border border-blue-500/40 bg-blue-500/10 text-blue-300 hover:bg-blue-500/20"
            title="不重启 backend, 把这帧扔回当前代码看选什么"
          >
            {replayLoading ? '回放中...' : '🔁 看现在代码选啥'}
          </button>
          {replayResult && (
            <button
              onClick={() => setReplayResult(null)}
              className="text-xs px-2 py-1 text-muted-foreground hover:text-foreground"
            >清除</button>
          )}
        </div>

        {/* Replay 结果面板 */}
        {replayResult && (
          <div className="rounded border border-blue-500/30 bg-blue-500/5 p-2 text-[11px] space-y-1">
            {!replayResult.ok ? (
              <div className="text-red-300">回放失败: {replayResult.error}</div>
            ) : (
              <>
                <div className="font-semibold text-blue-300">
                  当前代码会选: {replayResult.current_chosen
                    ? <span className="font-mono">{replayResult.current_chosen.label} @ ({replayResult.current_chosen.x}, {replayResult.current_chosen.y}) conf={replayResult.current_chosen.conf} (tier={replayResult.current_chosen.tier})</span>
                    : <span className="text-muted-foreground">不动作 (无目标)</span>}
                </div>
                <div className="text-muted-foreground">
                  is_at_lobby: <b>{String(replayResult.matchers?.is_at_lobby)}</b>
                  {' · '} find_close: {replayResult.matchers?.find_close_button
                    ? <span className="font-mono">{replayResult.matchers.find_close_button.name}@({replayResult.matchers.find_close_button.cx},{replayResult.matchers.find_close_button.cy}) {replayResult.matchers.find_close_button.conf}</span>
                    : 'null'}
                  {' · '} find_action: {replayResult.matchers?.find_action_button
                    ? <span className="font-mono">{replayResult.matchers.find_action_button.name}@({replayResult.matchers.find_action_button.cx},{replayResult.matchers.find_action_button.cy}) {replayResult.matchers.find_action_button.conf}</span>
                    : 'null'}
                </div>
                {(replayResult.all_hits?.length ?? 0) > 0 && (
                  <div className="text-muted-foreground">
                    其他命中: {replayResult.all_hits!.map(h =>
                      <span key={h.name} className="inline-block mr-2 font-mono">{h.name}({h.conf})@{h.cx},{h.cy}</span>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        )}

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
            const m = data.tap?.method || ''
            const annotTitle = m
              ? `标注 (${m} + 点击点)`
              : (data.tiers.some((t) => t.yolo_annot_image)
                  ? '标注 (YOLO)'
                  : '标注')
            return (
              <>
                <ScreenshotPanel title="原图" src={original} onClick={() => original && setLightboxSrc(original)} />
                <ScreenshotPanel title={annotTitle} src={annot} onClick={() => annot && setLightboxSrc(annot)} />
                <ScreenshotPanel title="OCR ROI" src={ocr} onClick={() => ocr && setLightboxSrc(ocr)} />
              </>
            )
          })()}
        </div>

        {/* 总览 */}
        <div className="rounded border border-border bg-muted p-2 text-[11px] space-y-0.5">
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            <span>结果: <b>{outcomeText(data.outcome)}</b></span>
            {data.tap && (
              <span>
                点击: <span className="font-mono">{data.tap.method}</span> @ ({data.tap.x}, {data.tap.y})
                {data.tap.target_class && <span className="ml-1 text-info">[{data.tap.target_class}]</span>}
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

      {/* Oracle 标注 modal */}
      {annotatorOpen && (
        <OracleAnnotator
          decisionId={decisionId}
          session={session}
          imgSrc={data.input_image ? decisionImgSrc(data.id, data.input_image, session) : ''}
          originalTap={data.tap ? { x: data.tap.x, y: data.tap.y, label: data.tap.method || '' } : null}
          onClose={() => setAnnotatorOpen(false)}
          onSaved={() => {
            setAnnotatorOpen(false)
            // 刷新 replay 看 oracle 是否符合
          }}
        />
      )}
    </div>
  )
}


// ─── Oracle 标注 modal ────────────────────────────────────────────
//
// 用户工作流: 看到错决策 → 点截图上的"应该点这里" → 选标签 → 提交
// 坐标转换: img.naturalWidth/Height vs displayed rect, 自动算出原图坐标
//
function OracleAnnotator({
  decisionId, session, imgSrc, originalTap, onClose, onSaved,
}: {
  decisionId: string
  session: string
  imgSrc: string
  originalTap: { x: number; y: number; label: string } | null
  onClose: () => void
  onSaved: () => void
}) {
  const [correct, setCorrect] = useState<OracleCorrect>('tap')
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null)
  const [label, setLabel] = useState('')
  const [note, setNote] = useState('')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')
  const [imgRect, setImgRect] = useState<{ w: number; h: number } | null>(null)

  function handleClickImage(e: React.MouseEvent<HTMLImageElement>) {
    if (correct !== 'tap') return
    const img = e.currentTarget
    const rect = img.getBoundingClientRect()
    if (img.naturalWidth === 0) return
    setImgRect({ w: img.naturalWidth, h: img.naturalHeight })
    const xRatio = img.naturalWidth / rect.width
    const yRatio = img.naturalHeight / rect.height
    const x = Math.round((e.clientX - rect.left) * xRatio)
    const y = Math.round((e.clientY - rect.top) * yRatio)
    setPos({ x, y })
  }

  async function handleSubmit() {
    setErr('')
    if (correct === 'tap' && (pos === null || !label.trim())) {
      setErr('点击模式必须: 在图上点位置 + 填标签 (close_x / btn_join 等)')
      return
    }
    setSaving(true)
    try {
      await createOracle({
        decision_id: decisionId,
        session,
        correct,
        click_x: pos?.x ?? null,
        click_y: pos?.y ?? null,
        label: label.trim(),
        note: note.trim(),
      })
      onSaved()
    } catch (e) {
      setErr(String(e))
    } finally {
      setSaving(false)
    }
  }

  // 计算红圈在 displayed 坐标系的位置
  const dotStyle: React.CSSProperties | null = pos && imgRect
    ? {
        position: 'absolute',
        left: `${(pos.x / imgRect.w) * 100}%`,
        top: `${(pos.y / imgRect.h) * 100}%`,
        transform: 'translate(-50%, -50%)',
        pointerEvents: 'none',
      }
    : null

  // 常用标签快捷
  const labelPresets = ['close_x', 'btn_join', 'btn_paste_code', 'btn_no_need', 'lobby_start', 'queding']

  return (
    <div
      className="fixed inset-0 z-[60] bg-black/80 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="bg-card border border-border rounded-lg max-w-5xl w-full max-h-[95vh] overflow-auto p-4 space-y-3"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold">🐞 标记此决策错误</h3>
          <span className="text-xs text-muted-foreground font-mono">{decisionId}</span>
          <button onClick={onClose} className="ml-auto text-sm text-muted-foreground hover:text-foreground">✕</button>
        </div>

        {originalTap && (
          <div className="text-[11px] text-muted-foreground bg-muted/50 rounded px-2 py-1.5">
            原决策: <span className="font-mono">{originalTap.label}</span> @ ({originalTap.x}, {originalTap.y})
          </div>
        )}

        {/* 模式选择 */}
        <div className="flex items-center gap-3 text-xs">
          <span className="text-muted-foreground">期望:</span>
          {(['tap', 'no_action', 'exit_phase'] as const).map(c => (
            <label key={c} className="flex items-center gap-1 cursor-pointer">
              <input
                type="radio"
                checked={correct === c}
                onChange={() => { setCorrect(c); if (c !== 'tap') setPos(null) }}
              />
              {c === 'tap' && '点击某位置'}
              {c === 'no_action' && '不应动作 (代码该忍住)'}
              {c === 'exit_phase' && '该退出 phase'}
            </label>
          ))}
        </div>

        {/* 截图 + 点击标注 */}
        {correct === 'tap' && (
          <div className="space-y-2">
            <div className="text-xs text-muted-foreground">
              在下面截图上点击 <b>正确的位置</b> ({pos ? <span className="text-amber-400 font-mono">已选 ({pos.x}, {pos.y})</span> : '未选'})
            </div>
            {imgSrc ? (
              <div className="relative inline-block max-w-full bg-foreground/95 rounded">
                <img
                  src={imgSrc}
                  alt="标注"
                  onClick={handleClickImage}
                  className="block max-w-full max-h-[60vh] cursor-crosshair"
                  draggable={false}
                />
                {dotStyle && (
                  <div
                    style={dotStyle}
                    className="w-6 h-6 rounded-full border-2 border-amber-400 bg-amber-400/40 shadow-lg shadow-amber-500/50"
                  />
                )}
              </div>
            ) : (
              <div className="text-xs text-red-300">没有 input.jpg 可标注</div>
            )}
          </div>
        )}

        {/* 标签 + 备注 */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
          <div>
            <label className="block text-muted-foreground mb-1">标签 {correct === 'tap' && <span className="text-amber-400">(必填)</span>}</label>
            <input
              type="text"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="如 close_x / btn_join"
              className="w-full px-2 py-1 rounded border border-border bg-background"
            />
            <div className="flex gap-1 mt-1 flex-wrap">
              {labelPresets.map(p => (
                <button
                  key={p}
                  onClick={() => setLabel(p)}
                  className="text-[10px] px-1.5 py-0.5 rounded bg-muted hover:bg-muted/70 text-muted-foreground font-mono"
                >{p}</button>
              ))}
            </div>
          </div>
          <div>
            <label className="block text-muted-foreground mb-1">备注 (可选)</label>
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="为什么错的 / 应该怎样"
              rows={3}
              className="w-full px-2 py-1 rounded border border-border bg-background resize-none"
            />
          </div>
        </div>

        {err && <div className="text-xs text-red-300">{err}</div>}

        <div className="flex justify-end gap-2 pt-1">
          <button
            onClick={onClose}
            className="text-xs px-3 py-1.5 rounded border border-border hover:bg-muted"
          >取消</button>
          <button
            onClick={handleSubmit}
            disabled={saving}
            className="text-xs px-4 py-1.5 rounded bg-amber-500/80 hover:bg-amber-500 text-zinc-900 font-semibold disabled:opacity-50"
          >{saving ? '保存中...' : '保存到 Oracle 集'}</button>
        </div>
      </div>
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
  const [popoverTpl, setPopoverTpl] = useState<string>('')
  const winner = t.early_exit
  return (
    <div className={`rounded border p-2 text-xs ${
      winner ? 'border-success/30 bg-success-muted' : 'border-border bg-muted/40'
    }`}>
      <div className="flex items-center gap-2">
        <span className={`inline-block w-1.5 h-1.5 rounded-full ${winner ? 'bg-success' : 'bg-muted-foreground/30'}`} />
        <span className="font-semibold">{t.name}</span>
        {winner && <span className="text-success text-[10px] font-bold">[赢]</span>}
        <span className="ml-auto font-mono text-[10px] text-muted-foreground">{t.duration_ms.toFixed(1)}ms</span>
      </div>
      {t.note && <div className="text-[11px] text-muted-foreground mt-0.5">{t.note}</div>}
      {t.templates && t.templates.length > 0 && (
        <ul className="mt-1 space-y-0.5">
          {t.templates.map((tp, i) => (
            <li key={i} className="flex items-center gap-2 font-mono text-[10px]">
              <span className={tp.hit ? 'text-success' : 'text-muted-foreground'}>{tp.hit ? '✓' : '·'}</span>
              <button
                onClick={() => setPopoverTpl(tp.name)}
                className="flex-1 truncate text-left underline decoration-dotted hover:text-info hover:decoration-solid"
                title={`点击查看模版图 ${tp.name}`}
              >
                {tp.name}
              </button>
              <span className="text-muted-foreground">{tp.score.toFixed(3)}</span>
            </li>
          ))}
        </ul>
      )}
      {popoverTpl && <TemplatePreview name={popoverTpl} onClose={() => setPopoverTpl('')} />}
      {t.yolo_detections && t.yolo_detections.length > 0 && (
        <ul className="mt-1 space-y-0.5">
          {t.yolo_detections.slice(0, 6).map((d, i) => (
            <li key={i} className="flex items-center gap-2 font-mono text-[10px]">
              <span className="text-info">▣</span>
              <span className="flex-1">{d.cls}</span>
              <span className="text-muted-foreground">{d.conf.toFixed(3)}</span>
            </li>
          ))}
        </ul>
      )}
      {t.memory_hit && (
        <div className="mt-1 font-mono text-[10px] text-warning">
          ◆ phash {t.memory_phash_query} → ({t.memory_hit.cx}, {t.memory_hit.cy})
        </div>
      )}
    </div>
  )
}


function TemplatePreview({ name, onClose }: { name: string; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div
      className="fixed inset-0 z-50 bg-foreground/85 flex items-center justify-center p-6"
      onClick={onClose}
    >
      <div
        className="bg-card rounded-lg shadow-xl max-w-[min(700px,90vw)] max-h-[85vh] flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 px-3 py-2 border-b border-border">
          <span className="text-sm font-semibold">模版预览</span>
          <span className="font-mono text-xs text-muted-foreground">{name}</span>
          <button
            onClick={onClose}
            className="ml-auto text-muted-foreground hover:text-foreground text-sm"
          >✕</button>
        </div>
        <div className="flex-1 bg-foreground/95 flex items-center justify-center p-3 overflow-hidden">
          <img
            src={`/api/templates/file/${encodeURIComponent(name)}`}
            alt={name}
            className="max-w-full max-h-[70vh] object-contain"
            onError={(e) => {
              (e.currentTarget as HTMLImageElement).replaceWith(
                Object.assign(document.createElement('div'), {
                  className: 'text-background/60 text-xs',
                  textContent: '模版图找不到 (可能已删除)',
                }),
              )
            }}
          />
        </div>
        <div className="px-3 py-2 text-[11px] text-muted-foreground border-t border-border">
          ESC / 点空白 关闭 · 想看更多详情请去「模版库」找 <span className="font-mono">{name}</span>
        </div>
      </div>
    </div>
  )
}
