/**
 * GamebotMark — logo + 文字标识.
 * 来源: shell-parts.jsx GamebotMark.
 */
export function GamebotMark({
  size = 22,
  color,
}: {
  size?: number
  color?: string
}) {
  const fg = color ?? 'currentColor'
  return (
    <div className="flex items-center gap-2 text-foreground">
      <svg
        width={size}
        height={size}
        viewBox="0 0 22 22"
        fill="none"
        aria-hidden
      >
        <rect
          x="2"
          y="5"
          width="18"
          height="14"
          rx="3"
          stroke={fg}
          strokeWidth="1.6"
        />
        <circle cx="8" cy="12" r="1.5" fill={fg} />
        <circle cx="14" cy="12" r="1.5" fill={fg} />
        <path
          d="M11 5V2M8 2h6"
          stroke={fg}
          strokeWidth="1.6"
          strokeLinecap="round"
        />
      </svg>
      <span className="text-[14px] font-semibold tracking-tight">Gamebot</span>
    </div>
  )
}
