/**
 * 决策档案 API.
 */

export interface SessionItem {
  session: string
  decision_count: number
  is_current: boolean
  mtime: number
}

export interface SessionsResp {
  sessions: SessionItem[]
  current_session: string
}

export async function fetchSessions(): Promise<SessionsResp> {
  const r = await fetch('/api/sessions')
  if (!r.ok) throw new Error(`sessions ${r.status}`)
  return await r.json()
}

export interface DecisionListItem {
  id: string
  instance: number
  phase: string
  round: number
  created: number
  outcome: string
  tap_method?: string
  tap_target?: string
  verify_success?: boolean | null
  tier_count?: number
  session?: string
}

export async function fetchDecisions(opts: {
  session?: string
  instance?: number
  limit?: number
}): Promise<{ count: number; items: DecisionListItem[]; session: string; enabled: boolean }> {
  const p = new URLSearchParams()
  if (opts.session) p.set('session', opts.session)
  if (opts.instance !== undefined && opts.instance >= 0) p.set('instance', String(opts.instance))
  if (opts.limit) p.set('limit', String(opts.limit))
  const r = await fetch(`/api/decisions?${p.toString()}`)
  if (!r.ok) throw new Error(`decisions ${r.status}`)
  return await r.json()
}

export interface DecisionTier {
  tier: number
  name: string
  duration_ms: number
  early_exit: boolean
  note: string
  templates?: Array<{ name: string; score: number; threshold: number; hit: boolean }>
  yolo_detections?: Array<{ cls: string; conf: number; bbox: number[] }>
  yolo_annot_image?: string
  ocr_hits?: Array<{ text: string; conf: number; bbox: number[] }>
  ocr_roi?: number[] | null
  ocr_roi_image?: string
  memory_phash_query?: string
  memory_hit?: { cx: number; cy: number; note?: string } | null
}

export interface DecisionData {
  id: string
  instance: number
  phase: string
  round: number
  created: number
  input_image: string
  input_phash: string
  input_w: number
  input_h: number
  tiers: DecisionTier[]
  tap?: {
    x: number
    y: number
    method: string
    target_class: string
    target_text: string
    target_conf: number
    annot_image: string
  }
  verify?: {
    phash_before: string
    phash_after: string
    distance: number
    success: boolean | null
  }
  outcome: string
  note: string
}

export async function fetchDecisionData(id: string, session?: string): Promise<DecisionData> {
  const p = new URLSearchParams()
  if (session) p.set('session', session)
  const r = await fetch(`/api/decision/${encodeURIComponent(id)}/data?${p.toString()}`)
  if (!r.ok) throw new Error(`decision ${r.status}`)
  return await r.json()
}

export function decisionImgSrc(id: string, image: string, session?: string): string {
  const p = new URLSearchParams()
  if (session) p.set('session', session)
  return `/api/decision/${encodeURIComponent(id)}/image/${encodeURIComponent(image)}?${p.toString()}`
}
