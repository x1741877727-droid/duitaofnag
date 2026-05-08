import { cn } from '@/lib/utils'
import { C, type Tone } from '@/lib/design-tokens'
import type { ReactNode } from 'react'

/**
 * SnapshotThumb — 实例画面缩略图.
 *
 * 设计原型 (snapshot-thumb.jsx) 用 canvas 噪声合成模拟动态画面;
 * 生产版**接真 MJPEG 流** (/api/stream/{idx}), 浏览器原生 multipart/x-mixed-replace.
 *
 * 行为:
 *   - tone='error' → 红底 hatched 条纹 + OFFLINE 文字, 不接 stream
 *   - 其他 → <img src="/api/stream/{idx}?w=N&fps=N">, 浏览器自动播放
 *
 * 参数:
 *   instanceIdx  实例编号
 *   tone         状态 tone (决定是否走 error 视图)
 *   errorMsg     error 时副标 (例 "adb 已断开")
 *   width        请求宽度 (传 backend 的 ?w 参数, 0 = 全尺寸)
 *   fps          帧率 (1-15)
 *   children     画面上叠加的元素 (角标 / 信息条等)
 */
export function SnapshotThumb({
  instanceIdx,
  tone,
  errorMsg,
  width = 320,
  fps = 2,
  className,
  children,
}: {
  instanceIdx: number
  tone: Tone
  errorMsg?: string
  width?: number
  fps?: number
  className?: string
  children?: ReactNode
}) {
  if (tone === 'error') {
    return (
      <div
        className={cn(
          'relative w-full h-full overflow-hidden flex items-center justify-center',
          className,
        )}
        style={{ background: '#1a0606' }}
      >
        {/* hatched stripes 红底斜线 */}
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
          // MJPEG 偶尔断流后浏览器会停止动画; img 元素本身不会消失, 黑底兜底
          ;(e.target as HTMLImageElement).style.opacity = '0.6'
        }}
        style={{ imageRendering: 'auto' }}
      />
      {children}
    </div>
  )
}

export const SNAPSHOT_BG = C.bg
