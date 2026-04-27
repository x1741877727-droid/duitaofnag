/**
 * YOLO 测试 API.
 */
export interface YoloInfo {
  available: boolean
  model_path: string
  classes: string[]
  input_size: number
  error?: string
}

export interface YoloDet {
  name: string
  class_id: number
  score: number
  x1: number
  y1: number
  x2: number
  y2: number
  cx: number
  cy: number
}

export interface YoloTestResult {
  ok: boolean
  source: string
  source_image_size: [number, number]
  conf_thr: number
  duration_ms: number
  detections: YoloDet[]
  annotated_b64: string
  error?: string
}

export async function fetchYoloInfo(): Promise<YoloInfo> {
  const r = await fetch('/api/yolo/info')
  if (!r.ok) throw new Error(`yolo info ${r.status}`)
  return await r.json()
}

export interface YoloTestArgs {
  instance?: number
  decision_id?: string
  session?: string
  image_b64?: string
  conf_thr?: number
  classes?: string[]
}

export async function runYoloTest(args: YoloTestArgs): Promise<YoloTestResult> {
  const r = await fetch('/api/yolo/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(args),
  })
  if (!r.ok) throw new Error(`yolo test ${r.status} ${(await r.text()).slice(0, 300)}`)
  return await r.json()
}


// ─── 数据集 / 标注 / 采集 / 模型 (api_yolo_labeler) ───

export interface DatasetItem {
  name: string
  size: number
  mtime: number
  labeled: boolean
  skipped: boolean
  class_ids: number[]
}

export interface PerClassStat {
  id: number
  name: string
  instances: number
  images: number
}

export interface DatasetList {
  total: number
  labeled: number
  skipped: number
  remaining: number
  classes: string[]
  per_class: PerClassStat[]
  items: DatasetItem[]
}

export async function fetchDataset(): Promise<DatasetList> {
  const r = await fetch('/api/labeler/list')
  if (!r.ok) throw new Error(`dataset ${r.status}`)
  return await r.json()
}

export function datasetImgSrc(name: string): string {
  return `/api/labeler/image/${encodeURIComponent(name)}`
}

export interface LabelBox {
  class_id: number
  cx: number      // normalized 0-1
  cy: number
  w: number
  h: number
}

export async function fetchLabels(name: string): Promise<{ boxes: LabelBox[]; exists: boolean }> {
  const r = await fetch(`/api/labeler/labels/${encodeURIComponent(name)}`)
  if (!r.ok) throw new Error(`labels ${r.status}`)
  return await r.json()
}

export async function saveLabels(name: string, boxes: LabelBox[]): Promise<{ ok: boolean; count: number }> {
  const r = await fetch(`/api/labeler/labels/${encodeURIComponent(name)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ boxes }),
  })
  if (!r.ok) throw new Error(`save ${r.status}`)
  return await r.json()
}

export async function deleteDatasetImage(name: string): Promise<{ ok: boolean; moved: string[] }> {
  const r = await fetch(`/api/labeler/image/${encodeURIComponent(name)}`, { method: 'DELETE' })
  if (!r.ok) throw new Error(`delete ${r.status}`)
  return await r.json()
}

export async function captureFromInstance(
  instance: number, tag = 'manual',
): Promise<{ ok: boolean; name: string; size: number; width: number; height: number }> {
  const r = await fetch('/api/labeler/capture', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ instance, tag }),
  })
  if (!r.ok) throw new Error(`capture ${r.status} ${(await r.text()).slice(0, 200)}`)
  return await r.json()
}

export async function uploadModel(file: Blob): Promise<{ ok: boolean; saved: string; size: number; latest: string }> {
  const fd = new FormData()
  fd.append('file', file, (file as File).name || 'model.onnx')
  const r = await fetch('/api/labeler/upload_model', { method: 'POST', body: fd })
  if (!r.ok) throw new Error(`upload model ${r.status}`)
  return await r.json()
}

export function exportZipUrl(): string {
  return '/api/labeler/export.zip'
}

export interface ClassesResp {
  classes: string[]
  legacy_cids: number[]
}

export async function fetchLabelClasses(): Promise<ClassesResp> {
  const r = await fetch('/api/labeler/classes')
  if (!r.ok) throw new Error(`classes ${r.status}`)
  return await r.json()
}

export async function addLabelClass(name: string): Promise<{ ok: boolean; classes: string[]; new_id: number }> {
  const r = await fetch('/api/labeler/classes', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  if (!r.ok) {
    const t = await r.text()
    throw new Error(`add class ${r.status} ${t.slice(0, 200)}`)
  }
  return await r.json()
}

export interface PreannotateBox extends LabelBox {
  class_name: string
  score: number
}

export interface PreannotateResp {
  ok: boolean
  filename: string
  image_size: [number, number]
  duration_ms: number
  boxes: PreannotateBox[]
  count: number
}

export async function preannotateLabels(name: string): Promise<PreannotateResp> {
  const r = await fetch(`/api/labeler/preannotate/${encodeURIComponent(name)}`, {
    method: 'POST',
  })
  if (!r.ok) {
    const t = await r.text()
    throw new Error(`preannotate ${r.status} ${t.slice(0, 300)}`)
  }
  return await r.json()
}
