// /api/oracle/* 客户端 — 决策回放 + Oracle 标注 + 回归批跑

export type OracleCorrect = 'tap' | 'no_action' | 'exit_phase'

export interface OracleAnnotation {
  correct: OracleCorrect
  click_x: number | null
  click_y: number | null
  label: string
  note: string
}

export interface OracleEntry {
  id: string
  created_at: number
  source_decision_id: string
  source_session: string
  input_size: { w: number; h: number }
  annotation: OracleAnnotation
  original_action: Record<string, unknown>
}

export interface MatcherHit {
  name: string
  conf: number
  cx: number
  cy: number
  w: number
  h: number
}

export interface ReplayResult {
  ok: boolean
  error?: string
  decision_id?: string
  session?: string
  input_size?: { w: number; h: number }
  matchers?: {
    is_at_lobby: boolean
    find_close_button: MatcherHit | null
    find_action_button: MatcherHit | null
    find_dialog_close: MatcherHit | null
  }
  all_hits?: MatcherHit[]
  current_chosen?: { x: number; y: number; label: string; tier: string; conf: number } | null
  original?: Record<string, unknown>
}

export interface OracleComparison {
  match: 'PASS' | 'FAIL_NO_ACTION' | 'FAIL_OVER_ACTING' | 'FAIL_WRONG_TARGET' | 'ERROR'
  reason: string
  current?: { x: number; y: number; label: string; tier: string; conf: number }
  expected?: { x: number; y: number; label: string }
  distance_px?: number
}

export interface ReplayAllResult {
  total: number
  passed: number
  failed: number
  by_kind: Record<string, number>
  results: Array<{
    id: string
    decision_id: string
    session: string
    annotation: OracleAnnotation
    original_action: Record<string, unknown>
    match: OracleComparison['match']
    reason: string
    current?: OracleComparison['current']
    expected?: OracleComparison['expected']
    distance_px?: number
  }>
  ts: number
}

async function _check(r: Response) {
  if (!r.ok) {
    const text = await r.text().catch(() => '')
    throw new Error(`HTTP ${r.status}: ${text || r.statusText}`)
  }
  return r.json()
}

export async function createOracle(req: {
  decision_id: string
  session: string
  correct: OracleCorrect
  click_x: number | null
  click_y: number | null
  label: string
  note: string
}): Promise<OracleEntry> {
  const r = await fetch('/api/oracle', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  return _check(r)
}

export async function listOracles(): Promise<{ oracles: OracleEntry[]; total: number }> {
  return _check(await fetch('/api/oracle'))
}

export async function getOracle(id: string): Promise<OracleEntry> {
  return _check(await fetch(`/api/oracle/${encodeURIComponent(id)}`))
}

export async function deleteOracle(id: string): Promise<{ ok: boolean }> {
  return _check(await fetch(`/api/oracle/${encodeURIComponent(id)}`, { method: 'DELETE' }))
}

export async function replayOne(id: string): Promise<{
  oracle: OracleEntry
  replay: ReplayResult
  comparison: OracleComparison
}> {
  return _check(await fetch(`/api/oracle/${encodeURIComponent(id)}/replay`, { method: 'POST' }))
}

export async function replayAll(): Promise<ReplayAllResult> {
  return _check(await fetch('/api/oracle/replay-all', { method: 'POST' }))
}

export async function replayArbitrary(decisionId: string, session = ''): Promise<ReplayResult> {
  const q = session ? `?session=${encodeURIComponent(session)}` : ''
  return _check(await fetch(`/api/oracle/replay/${encodeURIComponent(decisionId)}${q}`))
}

export async function reloadTemplates(): Promise<{ ok: boolean }> {
  return _check(await fetch('/api/oracle/reload-templates', { method: 'POST' }))
}
