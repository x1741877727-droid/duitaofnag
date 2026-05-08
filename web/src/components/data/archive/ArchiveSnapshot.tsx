/**
 * ArchiveSnapshot — 显示决策截图 + bbox overlay 层.
 *
 * 优先用真截图 (src prop, 来自 /api/decision/{id}/image/input.jpg);
 * 没有 src 时退回 canvas mock (开发态/历史 session 缺图).
 *
 * bbox overlay: React-positioned divs over image (% 坐标, 缩放跟随父容器).
 */

import { useEffect, useRef, useState } from 'react'
import { C } from '@/lib/design-tokens'
import { TIER_META, type UIEvidence, type UIBox } from '@/lib/archive-adapter'

const PHASE_PALETTES: Record<string, string[]> = {
  in_game: ['#0d1f2a', '#1e4a5b', '#3aa6c4', '#f0e9d2'],
  P6: ['#0d1f2a', '#1e4a5b', '#3aa6c4', '#f0e9d2'],
  lobby: ['#1c1408', '#523218', '#a06632', '#f4d6a4'],
  P4: ['#1c1408', '#523218', '#a06632', '#f4d6a4'],
  P5: ['#1c1408', '#523218', '#a06632', '#f4d6a4'],
  map_setup: ['#10141d', '#2a3346', '#5a6885', '#dee4ee'],
  team_create: ['#1c1408', '#523218', '#a06632', '#f4d6a4'],
  team_join: ['#1c1408', '#523218', '#a06632', '#f4d6a4'],
  ready: ['#0d1f2a', '#1e4a5b', '#3aa6c4', '#f0e9d2'],
  dismiss_popups: ['#10141d', '#2a3346', '#5a6885', '#dee4ee'],
  P3: ['#10141d', '#2a3346', '#5a6885', '#dee4ee'],
  wait_login: ['#10141d', '#2a3346', '#5a6885', '#dee4ee'],
}

function drawScene(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  seed: number,
  phase: string,
) {
  const pal = PHASE_PALETTES[phase] || PHASE_PALETTES.in_game
  let s = seed >>> 0
  const r = () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0
    return s / 4294967296
  }

  const g = ctx.createLinearGradient(0, 0, w, h)
  g.addColorStop(0, pal[0])
  g.addColorStop(0.55, pal[1])
  g.addColorStop(1, pal[0])
  ctx.fillStyle = g
  ctx.fillRect(0, 0, w, h)

  ctx.globalAlpha = 0.45
  for (let i = 0; i < 6; i++) {
    ctx.fillStyle = pal[2]
    ctx.beginPath()
    ctx.ellipse(r() * w, r() * h, 90 + r() * 220, 24 + r() * 60, r() * Math.PI, 0, Math.PI * 2)
    ctx.fill()
  }
  ctx.globalAlpha = 1

  ctx.fillStyle = 'rgba(0,0,0,.45)'
  ctx.fillRect(0, 0, w, 36)
  ctx.fillRect(0, h - 60, w, 60)

  ctx.globalAlpha = 0.85
  ctx.fillStyle = pal[3]
  for (let i = 0; i < 3; i++) {
    const bx = w - 240 + i * 70
    const by = h - 50
    ctx.beginPath()
    ctx.roundRect(bx, by, 60, 30, 6)
    ctx.fill()
  }
  ctx.globalAlpha = 1

  ctx.globalAlpha = 0.6
  ctx.fillStyle = pal[3]
  ctx.beginPath()
  ctx.arc(r() * w, r() * h, 14 + r() * 16, 0, Math.PI * 2)
  ctx.fill()
  ctx.globalAlpha = 1

  const img = ctx.getImageData(0, 0, w, h)
  const d = img.data
  for (let i = 0; i < d.length; i += 16) {
    const n = (Math.random() - 0.5) * 22
    d[i] = Math.max(0, Math.min(255, d[i] + n))
    d[i + 1] = Math.max(0, Math.min(255, d[i + 1] + n))
    d[i + 2] = Math.max(0, Math.min(255, d[i + 2] + n))
  }
  ctx.putImageData(img, 0, 0)

  ctx.globalAlpha = 0.05
  ctx.fillStyle = '#000'
  for (let y = 0; y < h; y += 2) ctx.fillRect(0, y, w, 1)
  ctx.globalAlpha = 1
}

type SnapshotEvidence =
  | UIEvidence
  | {
      tier: 'T1' | 'T2' | 'T3' | 'T4' | 'T5'
      isDecided: boolean
      bbox?: [number, number, number, number]
      bboxes?: UIBox[]
      conf: string
    }

/** 4 个 L 形角点 — 用于同 tier 多检测的"候选 bbox", 视觉比完整矩形轻得多. */
function CornerBox({
  left,
  top,
  w,
  h,
  tick,
  color,
  stroke,
}: {
  left: number
  top: number
  w: number
  h: number
  tick: number
  color: string
  stroke: number
}) {
  // 4 角的 L 路径, 每个角 2 段线 (短边 + 长边)
  const t = tick
  const W = w
  const H = h
  return (
    <svg
      width={w}
      height={h}
      viewBox={`0 0 ${w} ${h}`}
      style={{
        position: 'absolute',
        left,
        top,
        pointerEvents: 'none',
        overflow: 'visible',
        filter: 'drop-shadow(0 0 1px rgba(0,0,0,.45))',
      }}
    >
      {/* TL */}
      <path
        d={`M0 ${t} L0 0 L${t} 0`}
        stroke={color}
        strokeWidth={stroke}
        fill="none"
        strokeLinecap="round"
      />
      {/* TR */}
      <path
        d={`M${W - t} 0 L${W} 0 L${W} ${t}`}
        stroke={color}
        strokeWidth={stroke}
        fill="none"
        strokeLinecap="round"
      />
      {/* BR */}
      <path
        d={`M${W} ${H - t} L${W} ${H} L${W - t} ${H}`}
        stroke={color}
        strokeWidth={stroke}
        fill="none"
        strokeLinecap="round"
      />
      {/* BL */}
      <path
        d={`M${t} ${H} L0 ${H} L0 ${H - t}`}
        stroke={color}
        strokeWidth={stroke}
        fill="none"
        strokeLinecap="round"
      />
    </svg>
  )
}

export function ArchiveSnapshot({
  src,
  seed = 1,
  phase = 'in_game',
  evidence = [],
  visibleTiers,
  width = 1280,
  height = 720,
  label,
}: {
  /** 真截图 url; 不传则 canvas mock. */
  src?: string
  seed?: number
  phase?: string
  evidence?: SnapshotEvidence[]
  visibleTiers?: Set<string>
  width?: number
  height?: number
  label?: string
  mainBboxTier?: string | null
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const wrapRef = useRef<HTMLDivElement | null>(null)
  // img 在 objectFit:contain 下的真实渲染矩形 (px, 相对 wrapper).
  // bbox 必须基于这个矩形定位, 否则会落到 letterbox 黑边外面.
  const [imgRect, setImgRect] = useState<{ x: number; y: number; w: number; h: number } | null>(null)

  useEffect(() => {
    if (src) return // 真图模式不画 canvas
    const c = canvasRef.current
    if (!c) return
    c.width = width
    c.height = height
    const ctx = c.getContext('2d')
    if (!ctx) return
    drawScene(ctx, width, height, seed, phase)
  }, [src, seed, phase, width, height])

  // 测量 wrapper 尺寸 → 算出 contain-fit 后图像在容器中的真实矩形.
  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const ratio = width / height
    const update = () => {
      const r = el.getBoundingClientRect()
      if (r.width === 0 || r.height === 0) return
      const wrapRatio = r.width / r.height
      let rW: number
      let rH: number
      if (wrapRatio > ratio) {
        // wrapper 宽 → 高度填满, 左右 letterbox
        rH = r.height
        rW = rH * ratio
      } else {
        rW = r.width
        rH = rW / ratio
      }
      const rX = (r.width - rW) / 2
      const rY = (r.height - rH) / 2
      setImgRect({ x: rX, y: rY, w: rW, h: rH })
    }
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [width, height])

  // YOLO class → 颜色映射 (识别样本名色彩区分, 便于一眼判断是 close_x / action_btn 等)
  const YOLO_CLASS_COLOR: Record<string, string> = {
    close_x: '#dc2626',           // 红 — 关闭按钮
    action_btn: '#2563eb',        // 蓝 — 确认/同意按钮
    dialog: '#d97706',            // 橙 — 弹窗框
    lobby: '#16a34a',             // 绿 — 大厅
    team_slot_btn_collapse: '#7c3aed',  // 紫
    team_slot_btn_exit: '#ec4899',      // 粉
    slot_nameplate: '#0891b2',    // 青
  }

  type Drawn = {
    tier: 'T1' | 'T2' | 'T3' | 'T4' | 'T5'
    /** 'primary' = 主 (完整矩形 + 标签), 'corner' = 同 tier 候选 (4 角点 ticks). */
    role: 'primary' | 'corner'
    isDecided: boolean
    /** 图像坐标 (原始 input.jpg 像素). */
    imgX: number
    imgY: number
    imgW: number
    imgH: number
    label: string
    /** 颜色: yolo 走 class 着色, 其他走 tier 着色. */
    color: string
  }
  const drawn: Drawn[] = []
  for (const e of evidence || []) {
    if (visibleTiers && !visibleTiers.has(e.tier)) continue
    const tier = e.tier as 'T1' | 'T2' | 'T3' | 'T4' | 'T5'
    const tierColor = TIER_META[tier].color
    const list: UIBox[] =
      ('bboxes' in e && e.bboxes && e.bboxes.length)
        ? e.bboxes
        : ('bbox' in e && e.bbox)
          ? [{ bbox: e.bbox as [number, number, number, number], conf: e.conf, label: `${tier} · ${e.conf}` }]
          : []
    list.forEach((b, i) => {
      // YOLO class 着色, 其他 (template / ocr / memory) 走 tier 颜色
      const color = b.source === 'yolo' && b.cls
        ? (YOLO_CLASS_COLOR[b.cls] || tierColor)
        : tierColor
      // label: backend UIBox.label 已经含 class 名 (e.g. "close_x 0.94"), 直接用; fallback tier
      const useLabel = b.label || `${tier} · ${b.conf}`
      drawn.push({
        tier,
        role: i === 0 ? 'primary' : 'corner',
        isDecided: !!e.isDecided,
        imgX: b.bbox[0],
        imgY: b.bbox[1],
        imgW: b.bbox[2],
        imgH: b.bbox[3],
        label: useLabel,
        color,
      })
    })
  }

  // px scale: 把图像原坐标 (b.imgX/imgY/imgW/imgH 0..width/height) 映射到 wrapper px.
  const sx = imgRect ? imgRect.w / width : 0
  const sy = imgRect ? imgRect.h / height : 0
  const ox = imgRect ? imgRect.x : 0
  const oy = imgRect ? imgRect.y : 0

  return (
    <div
      ref={wrapRef}
      style={{
        position: 'relative',
        width: '100%',
        height: '100%',
        background: '#000',
        borderRadius: 6,
        overflow: 'hidden',
        boxShadow: `inset 0 0 0 1px ${C.border}`,
      }}
    >
      {src ? (
        <img
          src={src}
          alt=""
          style={{
            width: '100%',
            height: '100%',
            objectFit: 'contain',
            display: 'block',
            background: '#000',
          }}
          onError={(e) => {
            const el = e.currentTarget
            el.style.opacity = '0'
          }}
        />
      ) : (
        <canvas
          ref={canvasRef}
          style={{ width: '100%', height: '100%', display: 'block' }}
        />
      )}
      <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}>
        {imgRect &&
          drawn.map((b, i) => {
            const left = ox + b.imgX * sx
            const top = oy + b.imgY * sy
            const w = b.imgW * sx
            const h = b.imgH * sy

            if (b.role === 'corner') {
              // 4 个 L 形角点 — 视觉轻盈, 多检测时不抢戏.
              const tick = Math.min(10, w * 0.25, h * 0.25)
              const stroke = 1.5
              return (
                <CornerBox
                  key={i}
                  left={left}
                  top={top}
                  w={w}
                  h={h}
                  tick={tick}
                  color={b.color}
                  stroke={stroke}
                />
              )
            }

            // primary 完整矩形 + 标签
            const labelInside = top - oy < 16
            return (
              <div
                key={i}
                style={{
                  position: 'absolute',
                  left,
                  top,
                  width: w,
                  height: h,
                  border: `1.5px solid ${b.color}`,
                  outline: '1px solid rgba(255,255,255,.45)',
                  outlineOffset: -2,
                  boxShadow: b.isDecided
                    ? `0 0 0 1.5px ${b.color}, 0 0 0 3px ${b.color}33`
                    : 'none',
                  borderRadius: 2,
                  background: b.isDecided ? b.color + '14' : 'transparent',
                }}
              >
                <div
                  style={{
                    position: 'absolute',
                    top: labelInside ? 0 : -15,
                    left: labelInside ? 0 : -1.5,
                    background: b.color,
                    color: '#fff',
                    fontFamily: C.fontMono,
                    fontSize: 9,
                    fontWeight: 600,
                    padding: '0 5px',
                    height: 14,
                    lineHeight: '14px',
                    borderRadius: labelInside ? '2px 0 2px 0' : '2px 2px 0 0',
                    whiteSpace: 'nowrap',
                    boxShadow: '0 1px 2px rgba(0,0,0,.4)',
                  }}
                >
                  {b.label}
                </div>
              </div>
            )
          })}
      </div>
      {label && (
        <div
          style={{
            position: 'absolute',
            top: 6,
            left: 8,
            fontFamily: C.fontMono,
            fontSize: 10,
            color: 'rgba(255,255,255,.85)',
            textShadow: '0 1px 2px rgba(0,0,0,.6)',
          }}
        >
          {label}
        </div>
      )}
    </div>
  )
}
