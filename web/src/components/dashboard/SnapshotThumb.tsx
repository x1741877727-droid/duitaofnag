import { useEffect, useRef } from 'react'
import { cn } from '@/lib/utils'
import { type Tone } from '@/lib/design-tokens'
import {
  ensureSnapCanvases,
  getSnapCanvas,
  subscribeSnap,
  SNAP_W,
  SNAP_H,
} from '@/lib/snap-canvas'
import type { ReactNode } from 'react'

/** 派生: ?demo=running URL → 用 canvas 噪声合成 (设计原型行为). */
function isMockMode(): boolean {
  if (typeof window === 'undefined') return false
  const p = new URLSearchParams(window.location.search)
  return p.get('demo') === 'running'
}

/**
 * SnapshotThumb — 实例画面缩略图.
 *
 * 行为:
 *   - tone='error' → 红底 hatched 条纹 + OFFLINE (跟设计原型一致)
 *   - mock 模式 (?demo=running) → canvas 动态噪声画面 (照搬设计 data.js drawSnaps)
 *   - 真模式 → MJPEG 流 (/api/stream/{idx}?w=N&fps=N)
 */
export function SnapshotThumb({
  instanceIdx,
  tone,
  errorMsg,
  width = 320,
  fps = 2,
  className,
  children,
  state,
}: {
  instanceIdx: number
  tone: Tone
  errorMsg?: string
  width?: number
  fps?: number
  className?: string
  children?: ReactNode
  /** 实例状态. 'init'/'done' 时不接 MJPEG, 防止对没启动的 emulator 起 adb screenrecord
   * 导致 backend native 段错误 (PyAV decode 空 H.264 流). */
  state?: string
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const mock = isMockMode()

  // canvas mock 模式: 订阅共享 canvas, 重绘到本地 canvas
  useEffect(() => {
    if (!mock || tone === 'error') return
    ensureSnapCanvases()
    const local = canvasRef.current
    if (!local) return
    const ctx = local.getContext('2d')
    if (!ctx) return
    const draw = () => {
      const src = getSnapCanvas(tone) ?? getSnapCanvas('idle')
      if (src) ctx.drawImage(src, 0, 0, local.width, local.height)
    }
    draw()
    return subscribeSnap(draw)
  }, [mock, tone])

  // error 状态 (mock + 真模式都一样)
  if (tone === 'error') {
    return (
      <div
        className={cn(
          'relative w-full h-full overflow-hidden flex items-center justify-center',
          className,
        )}
        style={{ background: '#1a0606' }}
      >
        <div
          className="absolute inset-0 pointer-events-none"
          style={{
            backgroundImage:
              'repeating-linear-gradient(45deg, rgba(220,38,38,.18) 0 6px, transparent 6px 14px)',
          }}
        />
        <div className="text-center p-3 relative">
          <div
            className="text-[11px] font-semibold uppercase"
            style={{ color: '#fca5a5', letterSpacing: '.04em' }}
          >
            OFFLINE
          </div>
          {errorMsg && (
            <div
              className="text-[12px] mt-1 gb-mono"
              style={{ color: '#fef2f2' }}
            >
              {errorMsg}
            </div>
          )}
        </div>
        {children}
      </div>
    )
  }

  if (mock) {
    return (
      <div
        className={cn('relative w-full h-full overflow-hidden', className)}
        style={{ background: '#0a0a0a' }}
      >
        <canvas
          ref={canvasRef}
          width={SNAP_W}
          height={SNAP_H}
          className="block w-full h-full object-cover"
          style={{ imageRendering: 'auto' }}
        />
        {children}
      </div>
    )
  }

  // 真模式 — MJPEG. instance 没启动 (state init/done) 时显示占位, 不挂流避免 backend 崩.
  const isInactive = state === 'init' || state === 'done' || !state
  if (isInactive) {
    return (
      <div
        className={cn('relative w-full h-full overflow-hidden flex items-center justify-center', className)}
        style={{ background: '#1a1a17', color: '#7d7869' }}
      >
        <span className="text-[11px] gb-mono">未启动</span>
        {children}
      </div>
    )
  }
  const wQuery = width > 0 ? `w=${width}&` : ''
  const src = `/api/stream/${instanceIdx}?${wQuery}fps=${fps}`

  return (
    <div
      className={cn('relative w-full h-full overflow-hidden', className)}
      style={{ background: '#0a0a0a' }}
    >
      <img
        src={src}
        alt=""
        className="block w-full h-full object-cover"
        loading="lazy"
        decoding="async"
        onError={(e) => {
          ;(e.target as HTMLImageElement).style.opacity = '0.6'
        }}
        style={{ imageRendering: 'auto' }}
      />
      {children}
    </div>
  )
}
