/**
 * RecogBboxLayer — 只读 bbox overlay (yolo predictions). SVG-based.
 */

export interface BboxItem {
  x1: number
  y1: number
  x2: number
  y2: number
  color: string
  label?: string
  fill?: boolean
  dashed?: boolean
}

export function RecogBboxLayer({
  boxes = [],
  imgW = 1280,
  imgH = 720,
}: {
  boxes?: BboxItem[]
  imgW?: number
  imgH?: number
}) {
  return (
    <svg
      viewBox={`0 0 ${imgW} ${imgH}`}
      preserveAspectRatio="none"
      style={{
        position: 'absolute',
        inset: 0,
        width: '100%',
        height: '100%',
        pointerEvents: 'none',
      }}
    >
      {boxes.map((b, i) => {
        const w = b.x2 - b.x1
        const h = b.y2 - b.y1
        return (
          <g key={i}>
            <rect
              x={b.x1}
              y={b.y1}
              width={w}
              height={h}
              fill={b.color}
              fillOpacity={b.fill ? 0.12 : 0}
              stroke={b.color}
              strokeWidth={2}
              strokeDasharray={b.dashed ? '6 4' : 'none'}
            />
            {b.label && (
              <g transform={`translate(${b.x1}, ${Math.max(0, b.y1 - 22)})`}>
                <rect
                  x="0"
                  y="0"
                  width={Math.max(40, b.label.length * 8 + 14)}
                  height="20"
                  fill={b.color}
                  rx="3"
                />
                <text
                  x="6"
                  y="14"
                  fill="#fff"
                  style={{ font: '600 11px "IBM Plex Mono", monospace' }}
                >
                  {b.label}
                </text>
              </g>
            )}
          </g>
        )
      })}
    </svg>
  )
}
