/**
 * 记忆库 API.
 */

export interface MemoryRecord {
  id: number
  target_name: string
  phash: string
  action_x: number
  action_y: number
  action_w: number
  action_h: number
  hit_count: number
  success_count: number
  fail_count: number
  last_seen_ts: number
  snapshot_path: string
  note: string
  success_rate: number
}

export interface MemoryListResp {
  items: MemoryRecord[]
  count: number
  targets: { name: string; count: number }[]
  available: boolean
}

export async function fetchMemoryList(target = ''): Promise<MemoryListResp> {
  const p = target ? `?target=${encodeURIComponent(target)}` : ''
  const r = await fetch(`/api/memory/list${p}`)
  if (!r.ok) throw new Error(`memory list ${r.status}`)
  return await r.json()
}

export interface MemoryDetail extends MemoryRecord {
  similar: { id: number; phash_dist: number; action_xy: [number, number]; hits: number; succ: number; fail: number }[]
}

export async function fetchMemoryDetail(id: number): Promise<MemoryDetail> {
  const r = await fetch(`/api/memory/${id}`)
  if (!r.ok) throw new Error(`memory detail ${r.status}`)
  return await r.json()
}

export function memorySnapshotSrc(id: number): string {
  return `/api/memory/${id}/snapshot`
}

export async function deleteMemory(id: number): Promise<void> {
  const r = await fetch(`/api/memory/${id}`, { method: 'DELETE' })
  if (!r.ok) throw new Error(`delete ${r.status}`)
}

export async function markMemoryFail(id: number): Promise<{ ok: boolean; record: MemoryRecord }> {
  const r = await fetch(`/api/memory/${id}/mark_fail`, { method: 'POST' })
  if (!r.ok) throw new Error(`mark_fail ${r.status}`)
  return await r.json()
}
