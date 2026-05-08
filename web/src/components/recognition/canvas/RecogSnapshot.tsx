/**
 * RecogSnapshot — 识别页共用的 canvas 合成游戏帧.
 * deterministic by seed, supports preprocessing visualization.
 */

import { useEffect, useRef } from 'react'
import type { Preproc } from '@/lib/recog-mock'

const PHASE_PALETTE: Record<string, string[]> = {
  lobby: ['#1f2937', '#374151', '#9ca3af', '#fbbf24'],
  matchmake: ['#0c1022', '#1e3a8a', '#60a5fa', '#fde68a'],
  in_game: ['#0a1014', '#1e3a3a', '#16a34a', '#dc2626'],
  settle: ['#1c1917', '#78350f', '#fbbf24', '#fafaf9'],
  reward: ['#1c1917', '#7c2d12', '#fb923c', '#fef3c7'],
  error: ['#1c1917', '#7f1d1d', '#dc2626', '#fafafa'],
  launch: ['#0a0a08', '#1f2937', '#6b7280', '#e5e7eb'],
}

function rngFromSeed(seed: number) {
  let x = (seed | 0) || 1
  return () => {
    x = (x * 1103515245 + 12345) & 0x7fffffff
    return x / 0x7fffffff
  }
}

function paintFrame(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  seed: number,
  phase: string,
  preproc?: Preproc[],
) {
  const r = rngFromSeed(seed)
  const pal = PHASE_PALETTE[phase] || PHASE_PALETTE.lobby

  const grad = ctx.createLinearGradient(0, 0, 0, h)
  grad.addColorStop(0, pal[0])
  grad.addColorStop(1, pal[1])
  ctx.fillStyle = grad
  ctx.fillRect(0, 0, w, h)

  // organic blobs
  for (let i = 0; i < 80; i++) {
    const x = r() * w
    const y = r() * h
    const rad = 12 + r() * 96
    ctx.globalAlpha = 0.05 + r() * 0.08
    ctx.fillStyle = pal[2]
    ctx.beginPath()
    ctx.arc(x, y, rad, 0, Math.PI * 2)
    ctx.fill()
  }
  ctx.globalAlpha = 1

  // simulated UI rects
  const blocks = 22 + Math.floor(r() * 18)
  for (let i = 0; i < blocks; i++) {
    const x = r() * w * 0.92
    const y = r() * h * 0.92
    const bw = 30 + r() * 220
    const bh = 14 + r() * 70
    ctx.fillStyle = i % 5 === 0 ? pal[3] : pal[2]
    ctx.globalAlpha = 0.18 + r() * 0.32
    ctx.fillRect(x, y, bw, bh)
  }
  ctx.globalAlpha = 1

  // central subject
  ctx.fillStyle = pal[3]
  ctx.globalAlpha = 0.55
  ctx.beginPath()
  ctx.arc(
    w * (0.4 + r() * 0.2),
    h * (0.45 + r() * 0.15),
    50 + r() * 30,
    0,
    Math.PI * 2,
  )
  ctx.fill()
  ctx.globalAlpha = 1

  // hud bars
  ctx.fillStyle = pal[2]
  ctx.globalAlpha = 0.6
  ctx.fillRect(20, h - 56, 200, 12)
  ctx.fillStyle = '#16a34a'
  ctx.fillRect(20, h - 56, 132, 12)
  ctx.fillStyle = pal[2]
  ctx.fillRect(20, h - 38, 200, 8)
  ctx.fillStyle = '#2563eb'
  ctx.fillRect(20, h - 38, 88, 8)
  ctx.globalAlpha = 1

  // top-right gold counter
  ctx.fillStyle = '#fbbf24'
  ctx.fillRect(w - 130, 22, 24, 24)
  ctx.fillStyle = '#fef3c7'
  ctx.font = 'bold 18px "IBM Plex Mono", monospace'
  ctx.textBaseline = 'middle'
  ctx.fillText('4,287', w - 100, 34)

  // top-left level
  ctx.fillStyle = '#fafaf9'
  ctx.font = 'bold 16px "IBM Plex Mono", monospace'
  ctx.fillText('Lv.14', 30, 36)

  // top-center kda
  ctx.fillStyle = '#fafaf9'
  ctx.font = '600 16px "IBM Plex Mono", monospace'
  ctx.textAlign = 'center'
  ctx.fillText('7 / 2 / 11', w / 2, 36)
  ctx.textAlign = 'start'

  if (preproc && preproc.length) {
    const img = ctx.getImageData(0, 0, w, h)
    const d = img.data
    for (const op of preproc) {
      if (op === 'grayscale' || op === 'binarize' || op === 'edge') {
        for (let i = 0; i < d.length; i += 4) {
          const g = (d[i] * 0.299 + d[i + 1] * 0.587 + d[i + 2] * 0.114) | 0
          d[i] = d[i + 1] = d[i + 2] = g
        }
      }
      if (op === 'invert') {
        for (let i = 0; i < d.length; i += 4) {
          d[i] = 255 - d[i]
          d[i + 1] = 255 - d[i + 1]
          d[i + 2] = 255 - d[i + 2]
        }
      }
      if (op === 'binarize') {
        for (let i = 0; i < d.length; i += 4) {
          const v = d[i] > 128 ? 255 : 0
          d[i] = d[i + 1] = d[i + 2] = v
        }
      }
      if (op === 'sharpen' || op === 'clahe') {
        const factor = op === 'sharpen' ? 1.45 : 1.25
        for (let i = 0; i < d.length; i += 4) {
          d[i] = Math.max(0, Math.min(255, (d[i] - 128) * factor + 128))
          d[i + 1] = Math.max(0, Math.min(255, (d[i + 1] - 128) * factor + 128))
          d[i + 2] = Math.max(0, Math.min(255, (d[i + 2] - 128) * factor + 128))
        }
      }
      if (op === 'edge') {
        const out = new Uint8ClampedArray(d.length)
        for (let y = 1; y < h - 1; y++) {
          for (let x = 1; x < w - 1; x++) {
            const idx = (y * w + x) * 4
            const c = d[idx]
            const l = d[idx - 4]
            const t = d[idx - w * 4]
            const v = Math.min(255, Math.abs(c - l) * 4 + Math.abs(c - t) * 4)
            out[idx] = out[idx + 1] = out[idx + 2] = v
            out[idx + 3] = 255
          }
        }
        for (let i = 0; i < d.length; i++) d[i] = out[i]
      }
    }
    ctx.putImageData(img, 0, 0)
  }
}

export function RecogSnapshot({
  seed = 1,
  phase = 'in_game',
  preproc,
  width = 1280,
  height = 720,
  style,
}: {
  seed?: number
  phase?: string
  preproc?: Preproc[]
  width?: number
  height?: number
  style?: React.CSSProperties
}) {
  const ref = useRef<HTMLCanvasElement | null>(null)
  useEffect(() => {
    const c = ref.current
    if (!c) return
    c.width = width
    c.height = height
    const ctx = c.getContext('2d')
    if (!ctx) return
    paintFrame(ctx, width, height, seed, phase, preproc)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seed, phase, JSON.stringify(preproc), width, height])
  return (
    <canvas
      ref={ref}
      style={{
        display: 'block',
        width: '100%',
        height: '100%',
        objectFit: 'contain',
        ...style,
      }}
    />
  )
}
