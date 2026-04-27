/**
 * 模版库 API 客户端封装.
 */

export interface TemplateMeta {
  name: string
  category: string
  path: string
  size_bytes: number
  mtime: number
  width: number
  height: number
  phash: string
}

export interface TemplateListResp {
  count: number
  items: TemplateMeta[]
  categories: { name: string; count: number }[]
  template_dir: string
}

export interface TemplateStats {
  name: string
  sessions_scanned: number
  match_count: number
  hit_count: number
  hit_rate: number
  avg_score: number
}

export interface TemplateTestResult {
  template: string
  threshold: number
  hit: boolean
  score: number
  cx: number
  cy: number
  w: number
  h: number
  bbox: number[] | null
  duration_ms: number
  annotated_b64: string
  note: string
  source?: string
  source_image_size?: [number, number]
}

export async function fetchTemplateList(): Promise<TemplateListResp> {
  const r = await fetch('/api/templates/list')
  if (!r.ok) throw new Error(`list ${r.status}`)
  return await r.json()
}

export async function fetchTemplateStats(name: string): Promise<TemplateStats> {
  const r = await fetch(`/api/templates/stats/${encodeURIComponent(name)}`)
  if (!r.ok) throw new Error(`stats ${r.status}`)
  return await r.json()
}

export function templateImgSrc(name: string): string {
  return `/api/templates/file/${encodeURIComponent(name)}?t=${Math.floor(Date.now() / 60000)}`
}

export async function deleteTemplate(name: string): Promise<void> {
  const r = await fetch(`/api/templates/${encodeURIComponent(name)}`, { method: 'DELETE' })
  if (!r.ok) throw new Error(`delete ${r.status} ${(await r.text()).slice(0, 200)}`)
}

export interface TestArgs {
  name: string
  instance?: number
  decision_id?: string
  session?: string
  image_b64?: string
  threshold?: number
  use_edge?: boolean
}

export async function testTemplate(args: TestArgs): Promise<TemplateTestResult> {
  const r = await fetch('/api/templates/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(args),
  })
  if (!r.ok) throw new Error(`test ${r.status} ${(await r.text()).slice(0, 300)}`)
  return await r.json()
}

export interface UploadArgs {
  name: string
  file: Blob | File
  overwrite?: boolean
  crop?: { x: number; y: number; w: number; h: number }
}

export async function uploadTemplate(args: UploadArgs): Promise<{
  ok: boolean
  name: string
  path: string
  width: number
  height: number
  phash: string
  similar: { name: string; phash_dist: number }[]
}> {
  const fd = new FormData()
  fd.append('file', args.file, (args.file as File).name || `${args.name}.png`)
  fd.append('name', args.name)
  fd.append('overwrite', args.overwrite ? 'true' : 'false')
  if (args.crop) {
    fd.append('crop_x', String(args.crop.x))
    fd.append('crop_y', String(args.crop.y))
    fd.append('crop_w', String(args.crop.w))
    fd.append('crop_h', String(args.crop.h))
  }
  const r = await fetch('/api/templates/upload', { method: 'POST', body: fd })
  if (!r.ok) throw new Error(`upload ${r.status} ${(await r.text()).slice(0, 300)}`)
  return await r.json()
}
