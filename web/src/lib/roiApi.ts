/**
 * ROI 调试 API.
 */
export interface RoiItem {
  name: string
  rect: [number, number, number, number]   // [x1,y1,x2,y2] in [0,1]
  scale: number
  desc: string
  used_in: string
}

export interface RoiListResp {
  items: RoiItem[]
  count: number
  yaml_path: string
}

export interface RoiSaveReq {
  name: string
  rect: [number, number, number, number]
  scale: number
  desc?: string
  used_in?: string
}

export interface OcrHit {
  text: string
  conf: number
  box: [number, number][]   // 4 点, 在原图坐标系
  cx: number
  cy: number
}

export interface RoiTestOcrReq {
  instance?: number
  decision_id?: string
  session?: string
  rect: [number, number, number, number]
  scale: number
}

export interface RoiTestOcrResp {
  ok: boolean
  full_image_b64: string         // data:image/jpeg;base64,...
  cropped_image_b64: string
  source_size: [number, number]  // [w, h]
  rect_pixels: [number, number, number, number]
  scale: number
  ocr_results: OcrHit[]
  n_texts: number
}

export async function fetchRoiList(): Promise<RoiListResp> {
  const r = await fetch('/api/roi/list')
  if (!r.ok) throw new Error(`roi list ${r.status}`)
  return await r.json()
}

export async function saveRoi(req: RoiSaveReq): Promise<{ ok: boolean; name: string; backup: string }> {
  const r = await fetch('/api/roi/save', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  if (!r.ok) {
    const t = await r.text()
    throw new Error(`save ${r.status} ${t.slice(0, 200)}`)
  }
  return await r.json()
}

export async function testRoiOcr(req: RoiTestOcrReq): Promise<RoiTestOcrResp> {
  const r = await fetch('/api/roi/test_ocr', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  if (!r.ok) {
    const t = await r.text()
    throw new Error(`test_ocr ${r.status} ${t.slice(0, 300)}`)
  }
  return await r.json()
}
