/**
 * RoiCanvas — 显示帧 + ROI 红框 + 鼠标拖框调整 + 点击放大.
 *
 * 关键 bug 修复: 之前 wrap div 跟 img 不严格等比例 → 鼠标在 letterbox
 * 空白区拖框, 红框落到画面外. 改成 clientToNorm 用 img.getBoundingClientRect,
 * 鼠标必须在 img 元素内才响应; ROI/OCR overlay 也都基于 img rect 而非 wrap.
 *
 * 点击放大: 全屏 modal 显示大图 + ROI 框 + OCR 框, ESC / 点击外部关闭.
 */
import { useRef, useState } from 'react'
import type { OcrHit } from '@/lib/roiApi'

interface Props {
  imageSrc: string
  imageSize: [number, number]
  rect: [number, number, number, number]
  onRectChange?: (rect: [number, number, number, number]) => void
  ocrHits?: OcrHit[]
  className?: string
  enableZoom?: boolean    // 默认 true, 点图标放大
}

export function RoiCanvas({ imageSrc, imageSize, rect, onRectChange, ocrHits, className, enableZoom = true }: Props) {
  const imgRef = useRef<HTMLImageElement | null>(null)
  const [drag, setDrag] = useState<{ startNx: number; startNy: number; cur: [number, number, number, number] } | null>(null)
  const [zoomed, setZoomed] = useState(false)

  function clientToNorm(clientX: number, clientY: number): { nx: number; ny: number } | null {
    const img = imgRef.current
    if (!img) return null
    const r = img.getBoundingClientRect()
    if (r.width <= 0 || r.height <= 0) return null
    // 鼠标必须在 img 内 (避免 letterbox 空白区拖框)
    if (clientX < r.left || clientX > r.right || clientY < r.top || clientY > r.bottom) {
      return null
    }
    return {
      nx: Math.max(0, Math.min(1, (clientX - r.left) / r.width)),
      ny: Math.max(0, Math.min(1, (clientY - r.top) / r.height)),
    }
  }

  function onMouseDown(e: React.MouseEvent) {
    if (e.button !== 0) return
    const p = clientToNorm(e.clientX, e.clientY)
    if (!p) return  // 鼠标在 img 外, 不响应
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

  const displayRect = drag ? drag.cur : rect
  const [iw, ih] = imageSize

  // 渲染主画布. 内嵌模式 (isModal=false): 左键点击放大, 不能拖框.
  // modal 模式 (isModal=true): 可以拖框画 ROI, ESC 或外部点击关闭.
  function renderCanvas(isModal: boolean) {
    return (
      <div
        className="relative w-full h-full flex items-center justify-center bg-foreground select-none"
        onMouseDown={isModal ? onMouseDown : undefined}
        onMouseMove={isModal ? onMouseMove : undefined}
        onMouseUp={isModal ? onMouseUp : undefined}
        onMouseLeave={isModal ? onMouseUp : undefined}
        onClick={(!isModal && enableZoom && imageSrc) ? () => setZoomed(true) : undefined}
        style={{ cursor: isModal ? 'crosshair' : (enableZoom && imageSrc ? 'zoom-in' : 'default') }}
      >
        {imageSrc ? (
          <div className="relative max-w-full max-h-full" style={{ aspectRatio: `${iw} / ${ih}` }}>
            <img
              ref={isModal ? imgRef : undefined}
              src={imageSrc}
              alt="frame"
              draggable={false}
              className="block w-full h-full object-contain pointer-events-none"
            />

            {/* ROI 红框 */}
            {(displayRect[2] > displayRect[0]) && (displayRect[3] > displayRect[1]) && (
              <div
                className="absolute pointer-events-none border-2 border-destructive bg-destructive/10"
                style={{
                  left: `${displayRect[0] * 100}%`,
                  top: `${displayRect[1] * 100}%`,
                  width: `${(displayRect[2] - displayRect[0]) * 100}%`,
                  height: `${(displayRect[3] - displayRect[1]) * 100}%`,
                }}
              >
                <div className="absolute -top-5 left-0 px-1 py-0.5 bg-destructive text-white text-[10px] font-mono rounded-sm whitespace-nowrap">
                  ROI · {((displayRect[2] - displayRect[0]) * iw).toFixed(0)} × {((displayRect[3] - displayRect[1]) * ih).toFixed(0)} px
                </div>
              </div>
            )}

            {/* OCR 文字框 (绿色) */}
            {ocrHits && iw > 0 && ih > 0 && ocrHits.map((h, i) => {
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

            {/* 内嵌模式下角标提示 "点击放大" */}
            {!isModal && enableZoom && (
              <div className="absolute top-1.5 right-1.5 px-2 py-1 bg-black/60 text-white text-[10px] rounded font-mono pointer-events-none">
                点击放大编辑
              </div>
            )}
          </div>
        ) : (
          <div className="text-muted-foreground text-sm">点 "抓帧" 加载画面</div>
        )}
      </div>
    )
  }

  return (
    <>
      <div className={className}>
        {renderCanvas(false)}
      </div>

      {/* 放大 modal */}
      {zoomed && imageSrc && (
        <div
          className="fixed inset-0 z-50 bg-black/90 flex items-center justify-center p-4"
          onClick={() => setZoomed(false)}
          onKeyDown={(e) => e.key === 'Escape' && setZoomed(false)}
          tabIndex={-1}
        >
          <button
            onClick={() => setZoomed(false)}
            className="absolute top-4 right-4 px-3 py-1.5 bg-white/20 hover:bg-white/30 text-white rounded font-mono text-xs"
          >
            关闭 (ESC)
          </button>
          <div
            className="max-w-[95vw] max-h-[95vh] w-full h-full"
            onClick={(e) => e.stopPropagation()}
          >
            {renderCanvas(true)}
          </div>
        </div>
      )}
    </>
  )
}
