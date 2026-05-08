/**
 * RecogRectEditor — 通用交互式矩形编辑器 (overlay on image).
 * rect: [x1, y1, x2, y2] in normalized 0..1.
 * 支持: drag-to-create / drag-to-move / 8-handle resize.
 */

import { useEffect, useRef, useState } from 'react'
import { C } from '@/lib/design-tokens'

type Rect = [number, number, number, number]
type Side = 'n' | 's' | 'e' | 'w' | 'nw' | 'ne' | 'sw' | 'se'
type DragKind = 'move' | 'resize' | 'create'

interface DragState {
  kind: DragKind
  side?: Side
  anchor: { x: number; y: number }
  startRect: Rect | null
}

export function RecogRectEditor({
  rect,
  onChange,
  color = '#3b3a36',
  label,
  fillOpacity = 0.10,
  allowCreate = true,
  disabled = false,
}: {
  rect: Rect | null
  onChange: (r: Rect) => void
  color?: string
  label?: string
  fillOpacity?: number
  allowCreate?: boolean
  disabled?: boolean
}) {
  const ref = useRef<HTMLDivElement | null>(null)
  const [drag, setDrag] = useState<DragState | null>(null)

  const getPt = (e: { clientX: number; clientY: number }) => {
    const r = ref.current!.getBoundingClientRect()
    return {
      x: Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)),
      y: Math.max(0, Math.min(1, (e.clientY - r.top) / r.height)),
    }
  }

  const onDown = (
    e: React.MouseEvent,
    kind: DragKind,
    side?: Side,
  ) => {
    if (disabled) return
    e.stopPropagation()
    e.preventDefault()
    const p = getPt(e)
    setDrag({ kind, side, anchor: p, startRect: rect ? ([...rect] as Rect) : null })
  }

  const onCanvasDown = (e: React.MouseEvent) => {
    if (disabled || !allowCreate) return
    if (rect) return
    const p = getPt(e)
    const r: Rect = [p.x, p.y, p.x, p.y]
    onChange(r)
    setDrag({ kind: 'create', anchor: p, startRect: r })
  }

  useEffect(() => {
    if (!drag) return
    const onMove = (e: MouseEvent) => {
      const p = getPt(e)
      if (drag.kind === 'create') {
        const r = drag.startRect!
        onChange([
          Math.min(r[0], p.x), Math.min(r[1], p.y),
          Math.max(r[0], p.x), Math.max(r[1], p.y),
        ])
      } else if (drag.kind === 'move' && drag.startRect) {
        const dx = p.x - drag.anchor.x
        const dy = p.y - drag.anchor.y
        const r = drag.startRect
        const w = r[2] - r[0]
        const h = r[3] - r[1]
        const x1 = Math.max(0, Math.min(1 - w, r[0] + dx))
        const y1 = Math.max(0, Math.min(1 - h, r[1] + dy))
        onChange([x1, y1, x1 + w, y1 + h])
      } else if (drag.kind === 'resize' && drag.startRect && drag.side) {
        const r: Rect = [...drag.startRect] as Rect
        const s = drag.side
        if (s.includes('w')) r[0] = Math.min(p.x, r[2] - 0.005)
        if (s.includes('e')) r[2] = Math.max(p.x, r[0] + 0.005)
        if (s.includes('n')) r[1] = Math.min(p.y, r[3] - 0.005)
        if (s.includes('s')) r[3] = Math.max(p.y, r[1] + 0.005)
        onChange(r)
      }
    }
    const onUp = () => setDrag(null)
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drag])

  return (
    <div
      ref={ref}
      onMouseDown={onCanvasDown}
      style={{
        position: 'absolute',
        inset: 0,
        cursor: rect ? 'default' : (allowCreate ? 'crosshair' : 'default'),
      }}
    >
      {rect && (() => {
        const [x1, y1, x2, y2] = rect
        const left = `${x1 * 100}%`
        const top = `${y1 * 100}%`
        const w = `${(x2 - x1) * 100}%`
        const h = `${(y2 - y1) * 100}%`
        const hsize = 9
        const handles: Array<[Side, number, number]> = [
          ['nw', 0, 0], ['n', 0.5, 0], ['ne', 1, 0],
          ['w', 0, 0.5], ['e', 1, 0.5],
          ['sw', 0, 1], ['s', 0.5, 1], ['se', 1, 1],
        ]
        const cur: Record<Side, string> = {
          nw: 'nwse-resize', se: 'nwse-resize',
          ne: 'nesw-resize', sw: 'nesw-resize',
          n: 'ns-resize', s: 'ns-resize',
          e: 'ew-resize', w: 'ew-resize',
        }
        const fillHex = `${color}${Math.round(fillOpacity * 255).toString(16).padStart(2, '0')}`
        return (
          <div
            style={{
              position: 'absolute', left, top, width: w, height: h,
              border: `2px solid ${color}`,
              background: fillHex,
              cursor: 'move',
            }}
            onMouseDown={(e) => onDown(e, 'move')}
          >
            {label && (
              <div
                style={{
                  position: 'absolute', top: -22, left: -2,
                  background: color, color: '#fff', padding: '2px 6px',
                  fontFamily: C.fontMono, fontSize: 11, fontWeight: 600,
                  borderRadius: 3, whiteSpace: 'nowrap',
                }}
              >
                {label}
              </div>
            )}
            {!disabled && handles.map(([s, fx, fy]) => (
              <div
                key={s}
                onMouseDown={(e) => onDown(e, 'resize', s)}
                style={{
                  position: 'absolute', width: hsize, height: hsize,
                  left: `calc(${fx * 100}% - ${hsize / 2}px)`,
                  top: `calc(${fy * 100}% - ${hsize / 2}px)`,
                  background: '#fff', border: `1.5px solid ${color}`,
                  cursor: cur[s], boxShadow: '0 1px 2px rgba(0,0,0,.2)',
                }}
              />
            ))}
          </div>
        )
      })()}
    </div>
  )
}
