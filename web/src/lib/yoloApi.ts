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
