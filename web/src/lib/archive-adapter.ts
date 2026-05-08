/**
 * archive-adapter.ts — backend → 数据页 UI 适配层.
 *
 * 后端 schema (来自 [api_decisions.py](../../../backend/api_decisions.py) +
 * [decision_log.py](../../../backend/automation/decision_log.py)):
 *   sessions: {session, decision_count, is_current, mtime}
 *   decisions: {id, instance, phase, round, created, outcome, tap_method, tap_target,
 *               verify_success, tier_count, session}
 *   decision data: {id, tiers: [{tier, name, duration_ms, early_exit, note,
 *                   templates, yolo_detections, ocr_hits, ocr_roi, memory_hit, ...}],
 *                   tap?, verify?, outcome, ...}
 *   memory: {id, target_name, action_x/y/w/h, hit_count, success_count, fail_count,
 *            last_seen_ts, ...}
 *   pending: {key, target_name, samples, median_xy, ...}
 *
 * UI 期望 (跟 design 5-tier evidence chain 对齐):
 *   T1 模板 / T2 OCR / T3 YOLO / T4 Memory / T5 兜底
 */

import {
  fetchSessions as apiFetchSessions,
  fetchDecisions as apiFetchDecisions,
  fetchDecisionData as apiFetchDecisionData,
  decisionImgSrc,
  type SessionItem as BackendSession,
  type DecisionListItem as BackendDecisionItem,
  type DecisionData as BackendDecisionData,
  type DecisionTier as BackendTier,
} from './archiveApi'

// ─── tier color / 中文名 (5-tier 设计) ────────────────────────────────
export type TierKey = 'T1' | 'T2' | 'T3' | 'T4' | 'T5'

export interface TierMeta {
  name: string
  color: string
  soft: string
  desc: string
}

export const TIER_META: Record<TierKey, TierMeta> = {
  T1: { name: '模板', color: '#16a34a', soft: '#dcfce7', desc: '基于 OpenCV 模板匹配的快速命中通道' },
  T2: { name: 'OCR', color: '#d97706', soft: '#fef3c7', desc: 'PaddleOCR 文本识别 · 用于带文字按钮' },
  T3: { name: 'YOLO', color: '#4338ca', soft: '#e0e7ff', desc: '语义分割 · 用于战斗目标 / 队友 / 敌人' },
  T4: { name: 'Memory', color: '#7d7869', soft: '#efece4', desc: '历史决策回放 · 同状态曾经的成功路径' },
  T5: { name: '兜底', color: '#aca695', soft: '#f4f4f5', desc: '规则兜底 · idle_recover / 默认tap' },
}

// phase id → 中文 label (跟 store.STATE_LABEL / runner Phase 对齐)
const PHASE_LABEL: Record<string, string> = {
  P0: '环境检查',
  P1: '启动游戏',
  P2: '登录',
  P3: '清理弹窗',
  P3a: '清理弹窗 A',
  P3b: '清理弹窗 B',
  P4: '进入大厅',
  P5: '开局准备',
  P6: '战斗中',
  lobby: '大厅就绪',
  map_setup: '设置地图',
  team_create: '创建队伍',
  team_join: '加入队伍',
  ready: '准备就绪',
  in_game: '战斗中',
  dismiss_popups: '清理弹窗',
  wait_login: '等待登录',
  init: '等待启动',
}

export function phaseLabelOf(phase: string): string {
  return PHASE_LABEL[phase] || phase
}

// ─── UI 类型 ────────────────────────────────────────────────────────────

/** 概览卡片用 — 来自 /api/sessions, 已 enrich (可选 lazy load 决策聚合). */
export interface SessionSummary {
  /** 后端原始 session id, 例 "test_20260507_013757" */
  id: string
  /** 显示用 day 字符串, 例 "今天 14:30" / "5/7 01:37" */
  day: string
  /** 决策总数 (后端给的). */
  decisions: number
  /** 是否当前活跃 session. */
  isCurrent: boolean
  /** mtime (ms epoch). */
  mtime: number
  /** 已加载的聚合 (lazy fill). */
  agg?: SessionAgg
}

/** 决策聚合 — 客户端从决策列表算出来的. */
export interface SessionAgg {
  successCount: number
  failCount: number
  pendingCount: number /* verify_success === null */
  successRate: number /* successCount / (successCount + failCount), null-safe 0 */
  errors: number /* outcome 为 error / cancelled 的数 */
  instances: number[] /* 涉及的 instance idx 数组 */
  rounds: number /* unique round 数 */
  /** 第一/最后决策时间, 用来算 duration. */
  firstTs: number
  lastTs: number
  duration: string /* "HH:MM:SS" */
}

/** 决策列表行 — 来自 /api/decisions. */
export interface DecisionRow {
  id: string
  ts: string /* "HH:MM:SS.mmm" 显示用 */
  created: number /* unix epoch s, 排序用 */
  phase: string /* P5 etc */
  phaseLabel: string
  target: string /* tap_target 或 outcome 兜底 */
  ok: boolean | null /* verify_success */
  decidedBy: TierKey | null /* 从 tap_method 推 */
  tierCount: number
  instance: number
  round: number
  outcome: string
  session: string
}

/** 单个 bbox 命中 — yolo/ocr/template 的一条 hit. */
export interface UIBox {
  bbox: [number, number, number, number]
  conf: string
  /** "lobby (0.93)" 之类的简短 label, 显示在 box 上方. */
  label: string
}

/** 5-tier 证据 (UI 用) — 从后端 tiers[] 整合而成. */
export interface UIEvidence {
  tier: TierKey
  /** backend 给的 tier 名 (e.g. "YOLO" / "OCR" / "VPN校验"). 没有就回退 TIER_META[tier].name. */
  kindName: string
  isDecided: boolean
  tried: boolean
  passed: boolean
  conf: string
  latency_ms: number
  /** 主 bbox (兼容老用法), bboxes[0] 同值. */
  bbox: [number, number, number, number] | null
  /** 全部命中 bbox (yolo 多检测时全画). */
  bboxes: UIBox[]
  detail: string
  memoryHash: string | null
}

/** 完整决策详情 (UI 用). */
export interface DecisionDetail {
  id: string
  ts: string
  created: number
  phase: string
  phaseLabel: string
  target: string
  ok: boolean | null
  decidedBy: TierKey | null
  confidence: string
  latency_ms: number
  evidence: UIEvidence[]
  inputW: number
  inputH: number
  inputImage: string /* image url */
  imageBefore?: string
  imageAfter?: string
  outcome: string
  note: string
  session: string
}

// ─── 时间格式化 ─────────────────────────────────────────────────────────

const NOW = () => Date.now()

function fmtSessionDay(mtimeMs: number): string {
  const d = new Date(mtimeMs)
  const now = new Date(NOW())
  const dDay = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
  const nDay = `${now.getFullYear()}-${now.getMonth()}-${now.getDate()}`
  const yDay = `${now.getFullYear()}-${now.getMonth()}-${now.getDate() - 1}`
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  if (dDay === nDay) return `今天 ${hh}:${mm}`
  if (dDay === yDay) return `昨天 ${hh}:${mm}`
  return `${d.getMonth() + 1}/${d.getDate()} ${hh}:${mm}`
}

function fmtTs(unixSec: number): string {
  const d = new Date(unixSec * 1000)
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  const ss = String(d.getSeconds()).padStart(2, '0')
  const ms = String(d.getMilliseconds()).padStart(3, '0')
  return `${hh}:${mm}:${ss}.${ms}`
}

function fmtDuration(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  const hh = String(Math.floor(s / 3600)).padStart(2, '0')
  const mm = String(Math.floor((s % 3600) / 60)).padStart(2, '0')
  const ss = String(s % 60).padStart(2, '0')
  return `${hh}:${mm}:${ss}`
}

// ─── tap_method → tier 推断 ────────────────────────────────────────────

function tapMethodToTier(method: string): TierKey | null {
  const m = (method || '').toLowerCase()
  if (!m) return null
  if (m.includes('template') || m.includes('tpl')) return 'T1'
  if (m.includes('ocr')) return 'T2'
  if (m.includes('yolo')) return 'T3'
  if (m.includes('memory') || m.includes('mem')) return 'T4'
  if (m.includes('fallback') || m.includes('idle') || m.includes('center')) return 'T5'
  return null
}

/** 从 backend tier 数字转 UI tier key. 1→T1, 2→T2, ..., 但 backend tier 可能 >5
 *  (runner 内部 tier 不是设计的 5-tier). 用 phase 内 tier 数字 mod 5 兜底, 不丢. */
function backendTierToUIKey(t: number): TierKey {
  const n = Math.max(1, Math.min(5, t || 1))
  return (`T${n}` as TierKey)
}

// ─── 公开 API ──────────────────────────────────────────────────────────

export async function fetchSessionList(): Promise<SessionSummary[]> {
  const r = await apiFetchSessions()
  return (r.sessions || []).map((s: BackendSession): SessionSummary => ({
    id: s.session,
    day: fmtSessionDay(s.mtime * 1000),
    decisions: s.decision_count,
    isCurrent: !!s.is_current,
    mtime: s.mtime * 1000,
  }))
}

export async function fetchDecisionList(sessionId: string): Promise<DecisionRow[]> {
  const r = await apiFetchDecisions({ session: sessionId, limit: 500 })
  return (r.items || []).map((d: BackendDecisionItem): DecisionRow => {
    return {
      id: d.id,
      ts: fmtTs(d.created),
      created: d.created,
      phase: d.phase || '',
      phaseLabel: phaseLabelOf(d.phase || ''),
      target: d.tap_target || d.outcome || '—',
      ok: d.verify_success ?? null,
      decidedBy: tapMethodToTier(d.tap_method || ''),
      tierCount: d.tier_count || 0,
      instance: d.instance ?? 0,
      round: d.round ?? 0,
      outcome: d.outcome || '',
      session: d.session || sessionId,
    }
  })
}

export function aggregateSession(decisions: DecisionRow[]): SessionAgg {
  let successCount = 0
  let failCount = 0
  let pendingCount = 0
  let errors = 0
  const instances = new Set<number>()
  const rounds = new Set<number>()
  let firstTs = Number.POSITIVE_INFINITY
  let lastTs = 0
  for (const d of decisions) {
    if (d.ok === true) successCount++
    else if (d.ok === false) failCount++
    else pendingCount++
    if (d.outcome === 'error' || d.outcome === 'cancelled') errors++
    instances.add(d.instance)
    rounds.add(d.round)
    if (d.created < firstTs) firstTs = d.created
    if (d.created > lastTs) lastTs = d.created
  }
  const denom = successCount + failCount
  const successRate = denom > 0 ? successCount / denom : 0
  const dur = lastTs > 0 && firstTs < lastTs ? lastTs - firstTs : 0
  return {
    successCount,
    failCount,
    pendingCount,
    successRate,
    errors,
    instances: [...instances].sort((a, b) => a - b),
    rounds: rounds.size,
    firstTs: firstTs === Number.POSITIVE_INFINITY ? 0 : firstTs,
    lastTs,
    duration: fmtDuration(dur),
  }
}

function rectFromXyxy(b: number[]): [number, number, number, number] | null {
  if (!b || b.length < 4) return null
  return [b[0], b[1], b[2] - b[0], b[3] - b[1]]
}

/** 把后端单 tier 转 UI evidence. */
function adaptTier(t: BackendTier, isDecided: boolean): UIEvidence {
  const ui = backendTierToUIKey(t.tier)
  const tplHits = (t.templates || []).filter((x) => x.hit)
  const yoloHits = t.yolo_detections || []
  const ocrHits = t.ocr_hits || []
  const memHit = t.memory_hit
  const passed =
    tplHits.length > 0 ||
    yoloHits.length > 0 ||
    ocrHits.length > 0 ||
    !!memHit

  // 收集所有 bbox
  const bboxes: UIBox[] = []
  for (const y of yoloHits) {
    const r = rectFromXyxy(y.bbox)
    if (r) bboxes.push({ bbox: r, conf: y.conf.toFixed(3), label: `${y.cls} ${y.conf.toFixed(2)}` })
  }
  for (const o of ocrHits) {
    const r = rectFromXyxy(o.bbox)
    if (r) bboxes.push({ bbox: r, conf: o.conf.toFixed(3), label: `"${o.text}" ${o.conf.toFixed(2)}` })
  }
  if (bboxes.length === 0 && t.ocr_roi && t.ocr_roi.length >= 4) {
    const r = rectFromXyxy(t.ocr_roi)
    if (r) bboxes.push({ bbox: r, conf: '—', label: 'ocr roi' })
  }
  if (bboxes.length === 0 && memHit) {
    bboxes.push({
      bbox: [memHit.cx - 50, memHit.cy - 18, 110, 38],
      conf: '—',
      label: `mem (${memHit.cx},${memHit.cy})`,
    })
  }

  // conf (主)
  let confNum = 0
  if (tplHits.length) confNum = Math.max(...tplHits.map((x) => x.score))
  else if (yoloHits.length) confNum = Math.max(...yoloHits.map((x) => x.conf))
  else if (ocrHits.length) confNum = Math.max(...ocrHits.map((x) => x.conf))
  else if (t.templates && t.templates.length)
    confNum = Math.max(...t.templates.map((x) => x.score))
  const conf = confNum > 0 ? confNum.toFixed(3) : '—'

  // detail
  const parts: string[] = []
  if (t.note) parts.push(t.note)
  if (tplHits.length) parts.push(`tpl ${tplHits[0].name} (${tplHits[0].score.toFixed(3)})`)
  if (yoloHits.length) {
    const cls = [...new Set(yoloHits.map((x) => x.cls))].join('/')
    parts.push(`yolo ${cls} ×${yoloHits.length}`)
  }
  if (ocrHits.length) parts.push(`ocr "${ocrHits[0].text}" (${ocrHits[0].conf.toFixed(3)})`)
  if (memHit) parts.push(`mem hit @ (${memHit.cx},${memHit.cy})`)
  const detail = parts.join(' · ') || (passed ? '命中' : '未命中')

  return {
    tier: ui,
    kindName: t.name || TIER_META[ui].name,
    isDecided,
    tried: true,
    passed,
    conf,
    latency_ms: Math.round(t.duration_ms || 0),
    bbox: bboxes[0]?.bbox || null,
    bboxes,
    detail,
    memoryHash: t.memory_phash_query || (memHit ? `mem_${(t.memory_phash_query || '').slice(0, 12)}` : null),
  }
}

/** 把 backend tiers 数组适配到 UI 5-tier 数组. 若 backend 不足 5, 补 "未尝试". */
function adaptTiers(
  tiers: BackendTier[],
  decidedTierKey: TierKey | null,
): UIEvidence[] {
  // 按 tier 数字分组取最有信息量的一条 (有 hit > 没 hit, 有 note > 没 note)
  const byTier = new Map<TierKey, BackendTier>()
  for (const t of tiers) {
    const k = backendTierToUIKey(t.tier)
    const cur = byTier.get(k)
    if (!cur) byTier.set(k, t)
    else {
      const score = (x: BackendTier) =>
        (x.templates?.filter((y) => y.hit).length || 0) +
        (x.yolo_detections?.length || 0) +
        (x.ocr_hits?.length || 0) +
        (x.memory_hit ? 1 : 0) +
        (x.note ? 0.1 : 0)
      if (score(t) > score(cur)) byTier.set(k, t)
    }
  }
  const out: UIEvidence[] = []
  for (const k of ['T1', 'T2', 'T3', 'T4', 'T5'] as TierKey[]) {
    const t = byTier.get(k)
    if (t) {
      out.push(adaptTier(t, decidedTierKey === k))
    } else {
      out.push({
        tier: k,
        kindName: TIER_META[k].name,
        isDecided: false,
        tried: false,
        passed: false,
        conf: '—',
        latency_ms: 0,
        bbox: null,
        bboxes: [],
        detail: '未触发',
        memoryHash: null,
      })
    }
  }
  return out
}

export async function fetchDecisionDetail(
  decisionId: string,
  sessionId?: string,
): Promise<DecisionDetail> {
  const data: BackendDecisionData = await apiFetchDecisionData(decisionId, sessionId)
  // decided_by
  let decidedBy: TierKey | null = null
  if (data.tap?.method) decidedBy = tapMethodToTier(data.tap.method)
  if (!decidedBy) {
    // 兜底: 第一个 early_exit + has_hit 的 tier
    for (const t of data.tiers || []) {
      const tk = backendTierToUIKey(t.tier)
      const hasHit =
        (t.templates?.filter((x) => x.hit).length || 0) +
          (t.yolo_detections?.length || 0) +
          (t.ocr_hits?.length || 0) +
          (t.memory_hit ? 1 : 0) >
        0
      if (t.early_exit && hasHit) {
        decidedBy = tk
        break
      }
    }
  }
  if (!decidedBy && data.tap) decidedBy = 'T5'

  const evidence = adaptTiers(data.tiers || [], decidedBy)
  const decided = decidedBy ? evidence.find((e) => e.tier === decidedBy) : null
  const totalLatency = (data.tiers || []).reduce(
    (a, t) => a + (t.duration_ms || 0),
    0,
  )

  return {
    id: data.id,
    ts: fmtTs(data.created || 0),
    created: data.created || 0,
    phase: data.phase || '',
    phaseLabel: phaseLabelOf(data.phase || ''),
    target: data.tap?.target_class || data.tap?.target_text || data.outcome || '—',
    ok: data.verify?.success ?? null,
    decidedBy,
    confidence: decided?.conf || '—',
    latency_ms: Math.round(totalLatency),
    evidence,
    inputW: data.input_w || 1280,
    inputH: data.input_h || 720,
    inputImage: decisionImgSrc(data.id, data.input_image || 'input.jpg', sessionId),
    imageBefore: data.tap?.annot_image
      ? decisionImgSrc(data.id, data.tap.annot_image, sessionId)
      : undefined,
    outcome: data.outcome || '',
    note: data.note || '',
    session: sessionId || '',
  }
}

export { decisionImgSrc }
