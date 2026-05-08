import { useEffect, useState } from 'react'
import { cn } from '@/lib/utils'

// ─────────────────────────────────────────────────────────────────────────
// 加速器 — 极简版
//
// 一张小卡片, 显示当前是否在保护. 别的不做.
//   - 保护中 (TUN)         — wintun adapter ready
//   - 保护中 (SOCKS5)      — 兼容模式
//   - 启动中               — service 在跑但 wintun 还没起
//   - 服务未启动            — backend 拿不到 gameproxy
//
// 副标只放一行: 累计改写包数 + 运行时长
// ─────────────────────────────────────────────────────────────────────────

type TunMode = 'tun' | 'tun-pending' | 'socks5' | 'offline'
interface TunState {
  ok: boolean
  mode: TunMode
  uptime_seconds?: number
  counters: {
    tun_tcp_proxy?: number
    tun_tcp_direct?: number
    rule1_hits?: number
    rule2_hits?: number
  }
}

const POLL_MS = 3000

function statusInfo(mode: TunMode): { label: string; tone: 'ok' | 'pending' | 'off' } {
  switch (mode) {
    case 'tun':
      return { label: '保护中 · TUN', tone: 'ok' }
    case 'socks5':
      return { label: '保护中 · SOCKS5', tone: 'ok' }
    case 'tun-pending':
      return { label: '启动中', tone: 'pending' }
    case 'offline':
    default:
      return { label: '服务未启动', tone: 'off' }
  }
}

function fmtUptime(sec?: number): string {
  if (!sec || sec < 0) return ''
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  if (h > 0) return `${h}h${m}m`
  if (m > 0) return `${m}m`
  return `${sec}s`
}

function fmtN(n?: number): string {
  if (!n) return '0'
  if (n < 1000) return String(n)
  if (n < 1_000_000) return (n / 1000).toFixed(1) + 'k'
  return (n / 1_000_000).toFixed(1) + 'M'
}

export function AcceleratorView() {
  const [state, setState] = useState<TunState | null>(null)

  useEffect(() => {
    let alive = true
    let timer: ReturnType<typeof setTimeout> | null = null
    const tick = async () => {
      try {
        const r = await fetch('/api/tun/state')
        const j = (await r.json()) as TunState
        if (alive) setState(j)
      } catch {
        if (alive) setState(s => (s ? { ...s, mode: 'offline' as const } : null))
      } finally {
        if (alive) timer = setTimeout(tick, POLL_MS)
      }
    }
    tick()
    return () => {
      alive = false
      if (timer) clearTimeout(timer)
    }
  }, [])

  const mode: TunMode = state?.mode ?? 'offline'
  const { label, tone } = statusInfo(mode)
  const c = state?.counters ?? {}
  const totalRewrites = (c.rule1_hits ?? 0) + (c.rule2_hits ?? 0)
  const uptime = fmtUptime(state?.uptime_seconds)

  return (
    <div className="p-4">
      <div
        className={cn(
          'inline-flex items-center gap-3 rounded-lg border px-4 py-3 text-sm transition-colors',
          tone === 'ok' && 'bg-emerald-600/5 border-emerald-600/30 text-foreground',
          tone === 'pending' && 'bg-amber-600/5 border-amber-600/30 text-foreground',
          tone === 'off' && 'bg-zinc-600/5 border-zinc-600/30 text-muted-foreground',
        )}
      >
        <span
          className={cn(
            'h-2 w-2 rounded-full shrink-0',
            tone === 'ok' && 'bg-emerald-500 animate-pulse',
            tone === 'pending' && 'bg-amber-500 animate-pulse',
            tone === 'off' && 'bg-zinc-500',
          )}
        />
        <span className="font-medium">{label}</span>
        {tone === 'ok' && (totalRewrites > 0 || uptime) && (
          <span className="text-xs text-muted-foreground border-l border-border pl-3">
            {totalRewrites > 0 && <>已改写 {fmtN(totalRewrites)} 包</>}
            {totalRewrites > 0 && uptime && ' · '}
            {uptime && <>运行 {uptime}</>}
          </span>
        )}
      </div>
    </div>
  )
}
