/**
 * Animated snapshot canvas factory — demo mode 下替代真 MJPEG 流.
 * 来源: /tmp/design_dl/gameautomation/project/data.js (ensureSnapCanvases / drawSnaps).
 *
 * 4 套 palette (live/warn/idle/error), 共享 4 个 offscreen canvas, 220ms 重绘一次,
 * 多个 <SnapshotThumb> 通过 subscribeSnap 共享同一 canvas (cheap fan-out).
 */

import type { Tone } from './design-tokens'

export const SNAP_W = 240
export const SNAP_H = 135

const PALETTES: Record<Tone, [string, string, string, string]> = {
  live: ['#0c2a3a', '#1d6584', '#3aa6c4', '#f0e9d2'],
  warn: ['#2a1c08', '#7a4a14', '#d99245', '#f4e6c4'],
  idle: ['#15171d', '#2c3346', '#5a6885', '#e1e6ee'],
  error: ['#1d0606', '#5a1414', '#c33232', '#f1d5d5'],
}

const snapCanvases: Partial<Record<Tone, HTMLCanvasElement>> = {}
let snapInited = false
let snapTick = 0
const subs = new Set<(t: number) => void>()

function makeCanvas(): HTMLCanvasElement {
  const c = document.createElement('canvas')
  c.width = SNAP_W
  c.height = SNAP_H
  return c
}

function drawSnaps(t: number) {
  for (const tone of Object.keys(PALETTES) as Tone[]) {
    const c = snapCanvases[tone]
    if (!c) continue
    const ctx = c.getContext('2d')
    if (!ctx) continue
    const pal = PALETTES[tone]
    const off = (t * 9) % SNAP_W

    // gradient base
    const grd = ctx.createLinearGradient(0, 0, SNAP_W, SNAP_H)
    grd.addColorStop(0, pal[0])
    grd.addColorStop(0.5, pal[1])
    grd.addColorStop(1, pal[0])
    ctx.fillStyle = grd
    ctx.fillRect(0, 0, SNAP_W, SNAP_H)

    // ribbons
    ctx.globalAlpha = 0.55
    for (let i = 0; i < 5; i++) {
      const y = ((i * 60 + t * 4) % (SNAP_H + 80)) - 40
      ctx.fillStyle = pal[2]
      ctx.beginPath()
      ctx.ellipse(
        ((t * 6 + i * 90) % (SNAP_W + 200)) - 100,
        y,
        160,
        28 + i * 4,
        0,
        0,
        Math.PI * 2,
      )
      ctx.fill()
    }
    ctx.globalAlpha = 1

    // accent blob
    ctx.globalAlpha = 0.7
    ctx.fillStyle = pal[3]
    ctx.beginPath()
    ctx.arc(
      (off + 80) % SNAP_W,
      SNAP_H / 2 + Math.sin(t / 6) * 30,
      18,
      0,
      Math.PI * 2,
    )
    ctx.fill()
    ctx.globalAlpha = 1

    // noise
    const img = ctx.getImageData(0, 0, SNAP_W, SNAP_H)
    const d = img.data
    for (let i = 0; i < d.length; i += 4) {
      const n = (Math.random() - 0.5) * 28
      d[i] = Math.max(0, Math.min(255, d[i] + n))
      d[i + 1] = Math.max(0, Math.min(255, d[i + 1] + n))
      d[i + 2] = Math.max(0, Math.min(255, d[i + 2] + n))
    }
    ctx.putImageData(img, 0, 0)

    // scanlines
    ctx.globalAlpha = 0.06
    ctx.fillStyle = '#000'
    for (let y = 0; y < SNAP_H; y += 2) ctx.fillRect(0, y, SNAP_W, 1)
    ctx.globalAlpha = 1
  }
}

export function ensureSnapCanvases() {
  if (snapInited) return
  snapInited = true
  for (const tone of Object.keys(PALETTES) as Tone[]) {
    snapCanvases[tone] = makeCanvas()
  }
  drawSnaps(0)
  let last = 0
  function loop(ts: number) {
    if (ts - last > 220) {
      last = ts
      snapTick++
      drawSnaps(snapTick)
      subs.forEach((fn) => fn(snapTick))
    }
    requestAnimationFrame(loop)
  }
  requestAnimationFrame(loop)
}

export function subscribeSnap(fn: (t: number) => void): () => void {
  subs.add(fn)
  return () => subs.delete(fn) as unknown as void
}

export function getSnapCanvas(tone: Tone): HTMLCanvasElement | undefined {
  return snapCanvases[tone]
}
