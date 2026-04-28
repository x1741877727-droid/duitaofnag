/**
 * RoiCanvas — 显示帧 + ROI 红框 + 鼠标拖框调整.
 *
 * - 鼠标左键按下 → 开始画新框 (会覆盖现有 rect)
 * - 拖动 → 实时显示
 * - 松开 → 调用 onRectChange (传归一化坐标)
 * - 可选 ocrHits prop: 在帧上叠加 OCR 文字框 (绿色)
 */
import { useRef, useState } from 'react'
import type { OcrHit } from '@/lib/roiApi'

interface Props {
  imageSrc: string                                       // data:image/jpeg;base64,...
  imageSize: [number, number]                            // [w, h] 原图尺寸
  rect: [number, number, number, number]                 // 归一化 [x1,y1,x2,y2]
  onRectChange?: (rect: [number, number, number, number]) => void
  ocrHits?: OcrHit[]                                     // 可选: 叠加 OCR 框
  className?: string
}

export function RoiCanvas({ imageSrc, imageSize, rect, onRectChange, ocrHits, className }: Props) {
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const [drag, setDrag] = useState<{ startNx: number; startNy: number; cur: [number, number, number, number] } | null>(null)

  function clientToNorm(clientX: number, clientY: number): { nx: number; ny: number } | null {
    const wrap = wrapRef.current
    if (!wrap) return null
    const r = wrap.getBoundingClientRect()
    const nx = (clientX - r.left) / r.width
    const ny = (clientY - r.top) / r.height
    return {
      nx: Math.max(0, Math.min(1, nx)),
      ny: Math.max(0, Math.min(1, ny)),
    }
  }

  function onMouseDown(e: React.MouseEvent) {
    if (e.button !== 0) return
    const p = clientToNorm(e.clientX, e.clientY)
    if (!p) return
    e.preventDefault()
    setDrag({ startNx: p.nx, startNy: p.ny, cur: [p.nx, p.ny, p.nx, p.ny] })
  }

  function onMouseMove(e: React.MouseEvent) {
    if (!drag) return
    const p = clientToNorm(e.clientX, e.clientY)
    if (!p) return
    const x1 = Math.min(drag.startNx, p.nx)
    const y1 = Math.min(drag.startNy, p.ny)
    const x2 = Math.max(drag.startNx, p.nx)
    const y2 = Math.max(drag.startNy, p.ny)
    setDrag({ ...drag, cur: [x1, y1, x2, y2] })
  }

  function onMouseUp() {
    if (!drag) return
    const [x1, y1, x2, y2] = drag.cur
    if (x2 - x1 > 0.005 && y2 - y1 > 0.005) {
      onRectChange?.(drag.cur)
    }
    setDrag(null)
  }

  // 显示框: 拖动中用 drag.cur, 否则用 prop rect
  const displayRect = drag ? drag.cur : rect
  const [dx1, dy1, dx2, dy2] = displayRect
  const [iw, ih] = imageSize

  return (
    <div
      ref={wrapRef}
      className={`relative bg-foreground select-none ${className || ''}`}
      style={{ aspectRatio: iw && ih ? `${iw} / ${ih}` : '16 / 9', cursor: 'crosshair' }}
      onMouseDown={onMouseDown}
      onMouseMove={onMouseMove}
      onMouseUp={onMouseUp}
      onMouseLeave={onMouseUp}
    >
      {imageSrc ? (
        <img
          src={imageSrc}
          alt="frame"
          draggable={false}
          className="absolute inset-0 w-full h-full object-contain pointer-events-none"
        />
      ) : (
        <div className="absolute inset-0 flex items-center justify-center text-muted-foreground text-sm">
          点 "抓帧" 加载画面
        </div>
      )}

      {/* ROI 红框 */}
      {imageSrc && (dx2 > dx1) && (dy2 > dy1) && (
        <div
          className="absolute pointer-events-none border-2 border-destructive bg-destructive/10"
          style={{
            left: `${dx1 * 100}%`,
            top: `${dy1 * 100}%`,
            width: `${(dx2 - dx1) * 100}%`,
            height: `${(dy2 - dy1) * 100}%`,
          }}
        >
          <div className="absolute -top-5 left-0 px-1 py-0.5 bg-destructive text-white text-[10px] font-mono rounded-sm">
            ROI · {((dx2 - dx1) * iw).toFixed(0)} × {((dy2 - dy1) * ih).toFixed(0)} px
          </div>
        </div>
      )}

      {/* OCR 文字框叠加 (绿色) */}
      {imageSrc && ocrHits && iw > 0 && ih > 0 && ocrHits.map((h, i) => {
        if (!h.box || h.box.length !== 4) return null
        const xs = h.box.map(([x]) => x)
        const ys = h.box.map(([, y]) => y)
        const minX = Math.min(...xs) / iw
        const minY = Math.min(...ys) / ih
        const maxX = Math.max(...xs) / iw
        const maxY = Math.max(...ys) / ih
        return (
          <div
            key={i}
            className="absolute pointer-events-none border-[1.5px] border-success bg-success/10"
            style={{
              left: `${minX * 100}%`,
              top: `${minY * 100}%`,
              width: `${(maxX - minX) * 100}%`,
              height: `${(maxY - minY) * 100}%`,
            }}
            title={`${h.text} (${(h.conf * 100).toFixed(0)}%)`}
          >
            <div className="absolute -top-4 left-0 px-1 bg-success/90 text-white text-[9px] font-mono rounded-sm whitespace-nowrap">
              {h.text}
            </div>
          </div>
        )
      })}
    </div>
  )
}
