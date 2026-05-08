/**
 * GbSlider — 自定义 horizontal slider, 跟设计原型 gb-controls 对齐.
 * track + fill + default tick + 圆形 thumb (drag focus 加发光圈).
 */

import { useEffect, useRef, useState } from 'react'
import { C } from '@/lib/design-tokens'

export function GbSlider({
  value,
  onChange,
  min = 0,
  max = 1,
  step = 0.01,
  defaultMark,
}: {
  value: number
  onChange: (v: number) => void
  min?: number
  max?: number
  step?: number
  defaultMark?: number
}) {
  const trackRef = useRef<HTMLDivElement | null>(null)
  const [drag, setDrag] = useState(false)
  const pct = (value - min) / (max - min)
  const defPct = defaultMark != null ? (defaultMark - min) / (max - min) : null

  const setFromX = (clientX: number) => {
    const r = trackRef.current!.getBoundingClientRect()
    let p = (clientX - r.left) / r.width
    p = Math.max(0, Math.min(1, p))
    const raw = min + p * (max - min)
    const snapped = Math.round(raw / step) * step
    onChange(Math.max(min, Math.min(max, +snapped.toFixed(2))))
  }

  useEffect(() => {
    if (!drag) return
    const move = (e: MouseEvent) => setFromX(e.clientX)
    const up = () => setDrag(false)
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
    return () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drag])

  return (
    <div
      ref={trackRef}
      onMouseDown={(e) => {
        setDrag(true)
        setFromX(e.clientX)
      }}
      style={{
        position: 'relative',
        height: 22,
        cursor: 'pointer',
        userSelect: 'none',
        touchAction: 'none',
      }}
    >
      <div
        style={{
          position: 'absolute',
          left: 0, right: 0, top: 9, height: 4,
          borderRadius: 2,
          background: C.surface2,
          border: `1px solid ${C.borderSoft}`,
        }}
      />
      <div
        style={{
          position: 'absolute',
          left: 0, top: 9, height: 4,
          width: `${pct * 100}%`,
          borderRadius: 2,
          background: C.ink,
        }}
      />
      {defPct != null && (
        <div
          title={`默认 ${defaultMark}`}
          style={{
            position: 'absolute',
            left: `${defPct * 100}%`, top: 5,
            transform: 'translateX(-50%)',
            width: 1, height: 12,
            background: C.ink4, opacity: 0.55,
          }}
        />
      )}
      <div
        style={{
          position: 'absolute',
          left: `${pct * 100}%`, top: 11,
          transform: 'translate(-50%, -50%)',
          width: 14, height: 14, borderRadius: '50%',
          background: '#fff',
          border: `1.5px solid ${C.ink}`,
          boxShadow: drag
            ? `0 0 0 5px ${C.ink}1a, 0 1px 3px rgba(0,0,0,.18)`
            : '0 1px 2px rgba(0,0,0,.15)',
          transition: 'box-shadow .12s',
        }}
      />
    </div>
  )
}
