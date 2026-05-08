/**
 * GbSourceDropdown — instance / decision 来源选择, 带搜索的 popover dropdown.
 */

import { useEffect, useRef, useState } from 'react'
import { C } from '@/lib/design-tokens'
import type { TestInstance, TestDecision } from '@/lib/recog-mock'

export function GbSourceDropdown({
  kind,
  value,
  onChange,
  instances,
  decisions,
  width,
  dense,
}: {
  kind: 'instance' | 'decision'
  value: number
  onChange: (idx: number) => void
  instances: TestInstance[]
  decisions: TestDecision[]
  width?: string | number
  dense?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const wrapRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false)
    }
    if (open) {
      document.addEventListener('mousedown', onDoc)
      return () => document.removeEventListener('mousedown', onDoc)
    }
  }, [open])

  const list = kind === 'instance' ? instances : decisions
  const filtered = list.filter((it) => {
    if (!q) return true
    const s = q.toLowerCase()
    if (kind === 'instance') {
      const x = it as TestInstance
      return x.label.toLowerCase().includes(s) || x.phase.includes(s)
    }
    const x = it as TestDecision
    return (
      String(x.id).includes(s) ||
      x.target.toLowerCase().includes(s) ||
      x.session.includes(s)
    )
  })

  const cur = list[value] as TestInstance | TestDecision | undefined
  const triggerLabel = !cur
    ? '选择…'
    : kind === 'instance'
      ? `${(cur as TestInstance).label} · ${(cur as TestInstance).phase}`
      : `#${(cur as TestDecision).id} · ${(cur as TestDecision).target}`

  return (
    <div ref={wrapRef} style={{ position: 'relative', width: width ?? 'auto', display: 'inline-block' }}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        style={{
          width: width ?? 'auto',
          padding: dense ? '4px 9px' : '6px 11px',
          borderRadius: 5,
          fontSize: dense ? 11.5 : 12,
          fontFamily: C.fontMono,
          fontWeight: 500,
          color: C.ink2,
          background: open ? C.surface : C.surface2,
          border: `1px solid ${C.border}`,
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
          cursor: 'pointer',
          textAlign: 'left',
          minWidth: dense ? 180 : 220,
        }}
      >
        <span
          style={{
            flex: 1,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {triggerLabel}
        </span>
        <svg width="9" height="9" viewBox="0 0 9 9">
          <path
            d="M1.5 3l3 3 3-3"
            stroke={C.ink3}
            strokeWidth="1.4"
            fill="none"
            strokeLinecap="round"
          />
        </svg>
      </button>
      {open && (
        <div
          style={{
            position: 'absolute',
            top: '100%',
            left: 0,
            marginTop: 4,
            width: 320,
            maxWidth: 'min(420px, 90vw)',
            background: C.surface,
            border: `1px solid ${C.border}`,
            borderRadius: 7,
            boxShadow: '0 10px 28px rgba(0,0,0,.10), 0 2px 6px rgba(0,0,0,.05)',
            zIndex: 30,
            overflow: 'hidden',
          }}
        >
          <div style={{ padding: 8, borderBottom: `1px solid ${C.borderSoft}` }}>
            <input
              autoFocus
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={kind === 'instance' ? '搜索实例 / phase…' : '搜索 #id / target / session…'}
              style={{
                width: '100%',
                padding: '5px 8px',
                fontSize: 11.5,
                background: C.bg,
                border: `1px solid ${C.border}`,
                borderRadius: 4,
                color: C.ink,
                outline: 'none',
                boxSizing: 'border-box',
                fontFamily: 'inherit',
              }}
            />
          </div>
          <div className="gb-scroll" style={{ maxHeight: 260, overflow: 'hidden auto' }}>
            {filtered.map((it) => {
              const idx = list.indexOf(it as TestInstance & TestDecision)
              const on = idx === value
              if (kind === 'instance') {
                const x = it as TestInstance
                return (
                  <button
                    key={x.idx}
                    type="button"
                    onClick={() => {
                      onChange(idx)
                      setOpen(false)
                    }}
                    style={{
                      display: 'flex',
                      width: '100%',
                      textAlign: 'left',
                      padding: '6px 10px',
                      gap: 8,
                      background: on ? C.surface2 : 'transparent',
                      border: 'none',
                      cursor: 'pointer',
                      fontFamily: 'inherit',
                      borderBottom: `1px solid ${C.borderSoft}`,
                    }}
                  >
                    <span
                      className="gb-mono"
                      style={{ fontSize: 11.5, fontWeight: 600, color: C.ink, minWidth: 70 }}
                    >
                      {x.label}
                    </span>
                    <span style={{ fontSize: 11, color: C.ink3 }}>{x.phase}</span>
                    <span style={{ flex: 1 }} />
                    <span className="gb-mono" style={{ fontSize: 10, color: C.ink4 }}>
                      seed {x.seed}
                    </span>
                  </button>
                )
              }
              const x = it as TestDecision
              return (
                <button
                  key={x.id}
                  type="button"
                  onClick={() => {
                    onChange(idx)
                    setOpen(false)
                  }}
                  style={{
                    display: 'flex',
                    width: '100%',
                    textAlign: 'left',
                    padding: '6px 10px',
                    gap: 8,
                    background: on ? C.surface2 : 'transparent',
                    border: 'none',
                    cursor: 'pointer',
                    fontFamily: 'inherit',
                    borderBottom: `1px solid ${C.borderSoft}`,
                  }}
                >
                  <span
                    className="gb-mono"
                    style={{ fontSize: 11.5, fontWeight: 600, color: C.ink }}
                  >
                    #{x.id}
                  </span>
                  <span className="gb-mono" style={{ fontSize: 11, color: C.ink3 }}>
                    {x.target}
                  </span>
                  <span style={{ flex: 1 }} />
                  <span className="gb-mono" style={{ fontSize: 10, color: C.ink4 }}>
                    {x.ts}
                  </span>
                </button>
              )
            })}
            {filtered.length === 0 && (
              <div
                style={{
                  padding: 16,
                  textAlign: 'center',
                  fontSize: 11.5,
                  color: C.ink4,
                }}
              >
                无匹配
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
