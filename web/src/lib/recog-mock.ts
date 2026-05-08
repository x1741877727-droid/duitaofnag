/**
 * recog-mock.ts — 识别页 mock 数据 (templates / classes / ROIs / dataset / yolo info).
 * 完全照搬 /tmp/design_dl/gameautomation/project/recognition-data.js
 *
 * 阶段 4 用 mock 让视觉跟原型完全一致; 阶段 6 切真后端.
 */

export type Preproc = 'grayscale' | 'clahe' | 'binarize' | 'sharpen' | 'invert' | 'edge'

export const ALL_PREPROC: Preproc[] = [
  'grayscale', 'clahe', 'binarize', 'sharpen', 'invert', 'edge',
]
export const PREPROC_LABEL: Record<Preproc, string> = {
  grayscale: '灰度', clahe: 'CLAHE', binarize: '二值化',
  sharpen: '锐化', invert: '反色', edge: '边缘',
}
export const PREPROC_GLYPH: Record<Preproc, string> = {
  grayscale: 'GR', clahe: 'CL', binarize: 'BN',
  sharpen: 'SH', invert: 'IV', edge: 'ED',
}

export interface Category { name: string; label: string; count: number }
export const CATEGORIES: Category[] = [
  { name: 'launch', label: '启动', count: 4 },
  { name: 'lobby', label: '大厅', count: 7 },
  { name: 'matchmake', label: '匹配中', count: 3 },
  { name: 'in_game', label: '对局中', count: 11 },
  { name: 'settle', label: '结算', count: 5 },
  { name: 'reward', label: '奖励', count: 3 },
  { name: 'error', label: '异常', count: 2 },
]

export interface Template {
  name: string
  category: string
  path: string
  size_bytes: number
  mtime: number
  width: number
  height: number
  phash: string
  preprocessing: Preproc[]
  threshold: number
  source_w: number
  source_h: number
  has_original: boolean
  crop_bbox: [number, number, number, number] | null
  source: string
  saved_at: number
  sessions_scanned: number
  match_count: number
  hit_count: number
  hit_rate: number
  avg_score: number
}

const TEMPLATE_SEEDS: Array<[string, string, number, number, number]> = [
  ['btn_continue', 'lobby', 86, 32, 0.82],
  ['btn_start_match', 'lobby', 142, 44, 0.85],
  ['btn_back', 'launch', 72, 32, 0.80],
  ['btn_close_dialog', 'lobby', 32, 32, 0.78],
  ['btn_confirm', 'lobby', 108, 38, 0.84],
  ['btn_cancel', 'lobby', 108, 38, 0.80],
  ['btn_settings', 'lobby', 40, 40, 0.80],
  ['lobby_logo', 'lobby', 220, 64, 0.78],
  ['lobby_event_card', 'lobby', 360, 120, 0.72],
  ['matchmake_spinner', 'matchmake', 48, 48, 0.76],
  ['matchmake_cancel', 'matchmake', 96, 32, 0.80],
  ['matchmake_count', 'matchmake', 64, 24, 0.78],
  ['hp_bar_full', 'in_game', 180, 14, 0.86],
  ['hp_bar_low', 'in_game', 180, 14, 0.78],
  ['mp_bar', 'in_game', 180, 10, 0.84],
  ['gold_count_icon', 'in_game', 28, 28, 0.82],
  ['skill_icon_q', 'in_game', 54, 54, 0.84],
  ['skill_icon_w', 'in_game', 54, 54, 0.84],
  ['skill_icon_e', 'in_game', 54, 54, 0.84],
  ['skill_icon_r', 'in_game', 54, 54, 0.84],
  ['minimap_frame', 'in_game', 220, 220, 0.74],
  ['ping_indicator', 'in_game', 24, 24, 0.78],
  ['kill_feed_icon', 'in_game', 32, 32, 0.78],
  ['recall_progress', 'in_game', 80, 12, 0.82],
  ['btn_buy', 'in_game', 72, 28, 0.80],
  ['settle_victory', 'settle', 320, 96, 0.88],
  ['settle_defeat', 'settle', 320, 96, 0.86],
  ['settle_continue', 'settle', 140, 44, 0.84],
  ['mvp_badge', 'settle', 72, 72, 0.80],
  ['reward_chest', 'reward', 120, 120, 0.80],
  ['reward_claim', 'reward', 140, 44, 0.82],
  ['daily_complete', 'reward', 180, 36, 0.78],
  ['app_icon_login', 'launch', 96, 96, 0.84],
  ['app_logo_splash', 'launch', 400, 200, 0.80],
  ['err_disconnect', 'error', 420, 180, 0.86],
]

const NOW = Date.parse('2026-05-07T14:32:00Z')
const DAY_MS = 86400000

function pseudo(seed: number) {
  let x = seed * 9301 + 49297
  return () => {
    x = (x * 16807) % 2147483647
    return (x % 10000) / 10000
  }
}

function makeTemplates(): Template[] {
  return TEMPLATE_SEEDS.map(([name, category, w, h, threshold], i) => {
    const r = pseudo(i + 17)
    const preprocLen = Math.floor(r() * 3)
    const choices = ALL_PREPROC.filter(() => r() > 0.5)
    const preprocessing = choices.slice(0, preprocLen) as Preproc[]
    const sessions_scanned = 8 + Math.floor(r() * 22)
    const match_count = Math.floor(r() * 80) + 10
    const hit_count = Math.floor(match_count * (0.55 + r() * 0.4))
    const avg_score = Number((threshold + r() * 0.12 - 0.04).toFixed(3))
    return {
      name, category,
      path: `templates/${category}/${name}.png`,
      size_bytes: Math.floor(w * h * 4 * (0.4 + r() * 0.3)),
      mtime: NOW - Math.floor(r() * 6 * DAY_MS),
      width: w, height: h,
      phash: 'p' + Math.floor(r() * 1e16).toString(16).padStart(13, '0').slice(0, 13),
      preprocessing,
      threshold: i % 7 === 3 ? 0 : threshold,
      source_w: 1280, source_h: 720,
      has_original: i % 5 !== 0,
      crop_bbox: i % 5 !== 0
        ? [
            Math.floor(100 + r() * 800),
            Math.floor(80 + r() * 500),
            Math.floor(100 + r() * 800) + w,
            Math.floor(80 + r() * 500) + h,
          ] as [number, number, number, number]
        : null,
      source:
        i % 4 === 0 ? `decision_${4500 + i}` :
        i % 4 === 1 ? `instance_${i % 12}` :
        i % 4 === 2 ? `upload` : `decision_${4400 + i}`,
      saved_at: NOW - Math.floor(r() * 6 * DAY_MS),
      sessions_scanned,
      match_count,
      hit_count,
      hit_rate: Number((hit_count / Math.max(1, match_count)).toFixed(3)),
      avg_score,
    }
  })
}
export const TEMPLATES: Template[] = makeTemplates()

// ─── YOLO classes ───────────────────────────────────────────────────────

export interface YoloClass { id: number; name: string; instances: number; images: number }
export const CLASSES: YoloClass[] = [
  { id: 0, name: 'enemy_hero', instances: 312, images: 184 },
  { id: 1, name: 'allied_hero', instances: 291, images: 178 },
  { id: 2, name: 'minion', instances: 824, images: 201 },
  { id: 3, name: 'tower', instances: 96, images: 71 },
  { id: 4, name: 'gold_drop', instances: 142, images: 88 },
  { id: 5, name: 'btn_recall', instances: 64, images: 64 },
  { id: 6, name: 'shop_icon', instances: 31, images: 31 },
  { id: 7, name: 'skill_indicator', instances: 187, images: 132 },
]
export const CLASS_COLOR = [
  '#dc2626', '#2563eb', '#a16207', '#7c3aed',
  '#059669', '#db2777', '#0891b2', '#ea580c',
]

// ─── Dataset ────────────────────────────────────────────────────────────

export interface DatasetItem {
  name: string
  size: number
  mtime: number
  labeled: boolean
  skipped: boolean
  class_ids: number[]
  seed: number
}

function makeDataset(): DatasetItem[] {
  const items: DatasetItem[] = []
  for (let i = 0; i < 240; i++) {
    const r = pseudo(i + 71)
    const labeled = r() > 0.32
    const skipped = !labeled && r() > 0.85
    const classCount = labeled ? 1 + Math.floor(r() * 3) : 0
    const class_ids: number[] = []
    for (let j = 0; j < classCount; j++) {
      const cid = Math.floor(r() * CLASSES.length)
      if (!class_ids.includes(cid)) class_ids.push(cid)
    }
    const tag = (['manual', 'auto', 'capture', 'replay'] as const)[Math.floor(r() * 4)]
    const ts = NOW - Math.floor(r() * 6 * DAY_MS)
    items.push({
      name: `${tag}_${String(i).padStart(4, '0')}_${ts.toString(36)}.png`,
      size: 180000 + Math.floor(r() * 120000),
      mtime: ts,
      labeled,
      skipped,
      class_ids,
      seed: i + 71,
    })
  }
  items.sort((a, b) => b.mtime - a.mtime)
  return items
}
export const DATASET: DatasetItem[] = makeDataset()
export const DATASET_TOTAL = DATASET.length
export const DATASET_LABELED = DATASET.filter((d) => d.labeled).length
export const DATASET_SKIPPED = DATASET.filter((d) => d.skipped).length
export const DATASET_REMAINING = DATASET_TOTAL - DATASET_LABELED - DATASET_SKIPPED

// ─── ROIs (OCR) ─────────────────────────────────────────────────────────

export interface Roi {
  name: string
  rect: [number, number, number, number]
  scale: number
  desc: string
  used_in: string
  preprocessing: Preproc[]
}

export const ROIS: Roi[] = [
  { name: 'gold_count', rect: [0.85, 0.04, 0.96, 0.085], scale: 2,
    desc: '右上金币数', used_in: 'in_game · gold tracker',
    preprocessing: ['grayscale', 'clahe', 'binarize'] },
  { name: 'level_text', rect: [0.04, 0.05, 0.10, 0.085], scale: 3,
    desc: '左上等级数字', used_in: 'in_game · level',
    preprocessing: ['grayscale', 'binarize'] },
  { name: 'kill_count', rect: [0.45, 0.03, 0.55, 0.07], scale: 2,
    desc: '顶部 K/D/A', used_in: 'in_game · kda',
    preprocessing: ['grayscale', 'sharpen'] },
  { name: 'matchmake_eta', rect: [0.40, 0.55, 0.60, 0.62], scale: 2,
    desc: '匹配预计耗时', used_in: 'matchmake',
    preprocessing: ['grayscale'] },
  { name: 'settle_score', rect: [0.32, 0.40, 0.68, 0.55], scale: 1.5,
    desc: '结算页 MVP 分数', used_in: 'settle',
    preprocessing: ['grayscale', 'clahe'] },
  { name: 'err_message', rect: [0.25, 0.42, 0.75, 0.58], scale: 1,
    desc: '异常弹窗文本', used_in: 'error · disconnect',
    preprocessing: ['grayscale', 'sharpen'] },
  { name: 'reward_qty', rect: [0.62, 0.50, 0.80, 0.58], scale: 2,
    desc: '奖励数量', used_in: 'reward',
    preprocessing: ['grayscale', 'clahe', 'binarize'] },
  { name: 'lobby_player', rect: [0.05, 0.86, 0.20, 0.94], scale: 2,
    desc: '大厅玩家昵称', used_in: 'lobby',
    preprocessing: ['grayscale'] },
]

export const OCR_HITS: Record<string, Array<{ text: string; conf: number }>> = {
  gold_count: [{ text: '4,287', conf: 0.987 }],
  level_text: [{ text: 'Lv.14', conf: 0.962 }],
  kill_count: [{ text: '7 / 2 / 11', conf: 0.948 }],
  matchmake_eta: [{ text: '预计 0:42', conf: 0.892 }],
  settle_score: [
    { text: 'VICTORY', conf: 0.991 },
    { text: '+182', conf: 0.943 },
  ],
  err_message: [
    { text: '与服务器断开连接', conf: 0.876 },
    { text: '正在重连...', conf: 0.812 },
  ],
  reward_qty: [{ text: '×3', conf: 0.953 }],
  lobby_player: [{ text: 'KD_Shadow', conf: 0.928 }],
}

// ─── YOLO model info ────────────────────────────────────────────────────

export const YOLO_INFO = {
  available: true,
  model_path: 'models/yolov8n_gamebot_v3.onnx',
  classes: CLASSES.map((c) => c.name),
  input_size: 640,
  model_size_mb: 12.4,
  uploaded_at: NOW - 3 * DAY_MS - 17 * 3600 * 1000,
  history: [
    { name: 'yolov8n_gamebot_v3.onnx', size_mb: 12.4, uploaded_at: NOW - 3 * DAY_MS, mAP: 0.847, current: true },
    { name: 'yolov8n_gamebot_v2.onnx', size_mb: 12.4, uploaded_at: NOW - 12 * DAY_MS, mAP: 0.812, current: false },
    { name: 'yolov8n_gamebot_v1.onnx', size_mb: 12.4, uploaded_at: NOW - 28 * DAY_MS, mAP: 0.764, current: false },
  ],
}

// ─── synthetic test result helpers ──────────────────────────────────────

export interface FakeTestResult {
  template: string
  threshold: number
  hit: boolean
  score: number
  cx: number
  cy: number
  w: number
  h: number
  duration_ms: number
  note: string
  source: string
  source_image_size: [number, number]
}

export function fakeTestResult(template: Template, sourceLabel: string): FakeTestResult {
  const r = pseudo(template.name.length + 7)
  const score = Math.min(0.998, template.threshold + r() * 0.18 - 0.04)
  const hit = score >= template.threshold
  return {
    template: template.name,
    threshold: template.threshold || 0.80,
    hit,
    score: Number(score.toFixed(3)),
    cx: 320 + Math.floor(r() * 600),
    cy: 200 + Math.floor(r() * 320),
    w: template.width,
    h: template.height,
    duration_ms: Math.floor(8 + r() * 38),
    note: hit ? '命中' : '未达阈值',
    source: sourceLabel,
    source_image_size: [1280, 720],
  }
}

export interface FakeYoloDet {
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

export function fakeYoloDetections(seed = 1, conf_thr = 0.4): FakeYoloDet[] {
  const r = pseudo(seed * 13 + 5)
  const n = 3 + Math.floor(r() * 6)
  const out: FakeYoloDet[] = []
  for (let i = 0; i < n; i++) {
    const cid = Math.floor(r() * CLASSES.length)
    const cls = CLASSES[cid]
    const score = Math.min(0.99, conf_thr + r() * (1 - conf_thr) * 0.95 + 0.02)
    const x = 60 + r() * 1100
    const y = 60 + r() * 580
    const w = 50 + r() * 140
    const h = 50 + r() * 140
    out.push({
      name: cls.name,
      class_id: cid,
      score: Number(score.toFixed(3)),
      x1: Math.floor(x), y1: Math.floor(y),
      x2: Math.floor(x + w), y2: Math.floor(y + h),
      cx: Math.floor(x + w / 2), cy: Math.floor(y + h / 2),
    })
  }
  return out
}

// ─── test sources ───────────────────────────────────────────────────────

export interface TestInstance { idx: number; label: string; phase: string; seed: number }
export interface TestDecision { id: number; session: string; ts: string; target: string; seed: number }

export const TEST_INSTANCES: TestInstance[] = Array.from({ length: 12 }, (_, i) => ({
  idx: i,
  label: `实例 ${String(i).padStart(2, '0')}`,
  phase: (['lobby', 'matchmake', 'in_game', 'settle', 'reward'] as const)[i % 5],
  seed: i * 31 + 9,
}))

export const TEST_DECISIONS: TestDecision[] = Array.from({ length: 30 }, (_, i) => ({
  id: 4500 + i,
  session: `2026-05-07-${10 + (i % 4)}`,
  ts: `14:${String(20 + (i % 40)).padStart(2, '0')}:${String((i * 7) % 60).padStart(2, '0')}`,
  target: (['btn_continue', 'btn_start_match', 'skill_icon_q', 'recall_progress'] as const)[i % 4],
  seed: i * 53 + 11,
}))

export function fmtBytes(n: number): string {
  if (n < 1024) return n + ' B'
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB'
  return (n / 1024 / 1024).toFixed(2) + ' MB'
}

export function fmtAgo(ts: number): string {
  const m = Math.floor((Date.now() - ts) / 60000)
  if (m < 60) return m + ' 分前'
  if (m < 1440) return Math.floor(m / 60) + ' 时前'
  return Math.floor(m / 1440) + ' 天前'
}
