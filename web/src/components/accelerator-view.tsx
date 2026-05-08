/**
 * AcceleratorView — 加速器 (TUN-only).
 *
 * 完全本地化: 校验 = /api/tun/state 拉本地 wintun 适配器状态 + 包计数.
 * 删了 APK 时代的 3 层校验 (level1_proxy / level2_mitm / level3_rules) 和 SOCKS5 兼容模式.
 *
 * 状态机:
 *   TUN  — wintun 适配器在跑, gameproxy 健康 (running + ok)
 *   未启动 — 服务/适配器没起 (running=false 或 ok=false)
 */

import { useEffect, useState } from 'react'
import { C } from '@/lib/design-tokens'

interface TunState {
  ok: boolean
  running: boolean
  mode?: string
  uptime_seconds?: number
  counters?: {
    tun_tcp_proxy?: number
    tun_tcp_direct?: number
    rule1_hits?: number
    rule2_hits?: number
  }
}

const POLL_MS = 3000

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
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [actionMsg, setActionMsg] = useState<string | null>(null)

  const refetch = async () => {
    try {
      const r = await fetch('/api/tun/state')
      if (!r.ok) throw new Error(String(r.status))
      const j = (await r.json()) as TunState
      setState(j)
      setLoadErr(null)
    } catch (e) {
      setLoadErr(String(e).slice(0, 100))
      setState({ ok: false, running: false })
    }
  }

  useEffect(() => {
    let alive = true
    let timer: ReturnType<typeof setTimeout> | null = null
    const tick = async () => {
      if (!alive) return
      await refetch()
      if (alive) timer = setTimeout(tick, POLL_MS)
    }
    tick()
    return () => {
      alive = false
      if (timer) clearTimeout(timer)
    }
  }, [])

  const doStart = async () => {
    setBusy(true)
    setActionMsg('启动中…')
    try {
      const r = await fetch('/api/tun/start', { method: 'POST' })
      const j = await r.json()
      if (j.ok) {
        setActionMsg(j.already_running ? '已在跑' : `已启动 (PID ${j.pid})`)
      } else {
        setActionMsg(`启动失败: ${j.error || '未知'}`)
      }
      await refetch()
    } catch (e) {
      setActionMsg(`启动失败: ${String(e).slice(0, 80)}`)
    } finally {
      setBusy(false)
      setTimeout(() => setActionMsg(null), 4000)
    }
  }

  const doStop = async () => {
    if (!confirm('确认停止 gameproxy.exe? 停止后所有模拟器流量将不再走加速器.')) return
    setBusy(true)
    setActionMsg('停止中…')
    try {
      const r = await fetch('/api/tun/stop', { method: 'POST' })
      const j = await r.json()
      setActionMsg(j.ok ? '已停止' : `停止失败: ${j.error || j.stderr || '未知'}`)
      await refetch()
    } catch (e) {
      setActionMsg(`停止失败: ${String(e).slice(0, 80)}`)
    } finally {
      setBusy(false)
      setTimeout(() => setActionMsg(null), 4000)
    }
  }

  const isOn = !!(state?.running && state?.ok)
  const c = state?.counters ?? {}
  const totalRewrites = (c.rule1_hits ?? 0) + (c.rule2_hits ?? 0)
  const tcpProxy = c.tun_tcp_proxy ?? 0
  const tcpDirect = c.tun_tcp_direct ?? 0
  const uptime = fmtUptime(state?.uptime_seconds)

  return (
    <div
      style={{
        flex: 1,
        padding: '22px 28px',
        background: C.bg,
        color: C.ink,
        fontFamily: C.fontUi,
        overflow: 'auto',
        display: 'flex',
        flexDirection: 'column',
        gap: 16,
      }}
    >
      {/* 主状态卡 */}
      <div
        style={{
          background: C.surface,
          border: `1px solid ${isOn ? C.live + '40' : C.border}`,
          borderRadius: 14,
          padding: '20px 24px',
          display: 'flex',
          alignItems: 'center',
          gap: 16,
          maxWidth: 720,
          boxShadow: '0 1px 0 rgba(0,0,0,.02)',
        }}
      >
        <span
          className={isOn ? 'gb-pulse' : ''}
          style={{
            width: 12,
            height: 12,
            borderRadius: '50%',
            background: isOn ? C.live : C.ink4,
            flexShrink: 0,
          }}
        />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontSize: 14,
              fontWeight: 600,
              color: C.ink,
              letterSpacing: '.02em',
            }}
          >
            {isOn ? '保护中 · TUN 模式' : '未启动'}
          </div>
          <div
            style={{
              fontSize: 11.5,
              color: C.ink3,
              marginTop: 4,
              fontFamily: C.fontMono,
            }}
          >
            {isOn
              ? `wintun 适配器 ✓ · gameproxy 健康 ✓${uptime ? ' · 运行 ' + uptime : ''}`
              : 'wintun 适配器 / gameproxy 服务未就绪'}
          </div>
        </div>
        <span
          className="gb-mono"
          style={{
            fontSize: 10.5,
            color: C.ink3,
            padding: '3px 8px',
            borderRadius: 4,
            background: C.surface2,
            border: `1px solid ${C.border}`,
            letterSpacing: '.04em',
            textTransform: 'uppercase',
            flexShrink: 0,
          }}
        >
          local · no apk
        </span>
        {/* 启动 / 停止 按钮 — 随手开关 */}
        {!isOn ? (
          <button
            type="button"
            onClick={doStart}
            disabled={busy}
            style={{
              padding: '7px 16px',
              borderRadius: 6,
              fontSize: 12.5,
              fontWeight: 600,
              color: '#f0eee9',
              background: C.ink,
              border: 'none',
              cursor: busy ? 'wait' : 'pointer',
              fontFamily: 'inherit',
              flexShrink: 0,
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
            }}
          >
            <svg width="11" height="11" viewBox="0 0 11 11" fill="#fff">
              <path d="M2 1l8 4.5L2 10z" />
            </svg>
            {busy ? '启动中…' : '启动加速器'}
          </button>
        ) : (
          <button
            type="button"
            onClick={doStop}
            disabled={busy}
            style={{
              padding: '7px 14px',
              borderRadius: 6,
              fontSize: 12.5,
              fontWeight: 500,
              color: C.ink2,
              background: C.surface,
              border: `1px solid ${C.border}`,
              cursor: busy ? 'wait' : 'pointer',
              fontFamily: 'inherit',
              flexShrink: 0,
            }}
          >
            {busy ? '停止中…' : '停止'}
          </button>
        )}
      </div>
      {actionMsg && (
        <div
          className="gb-mono"
          style={{
            fontSize: 11,
            color: actionMsg.includes('失败') ? C.error : C.ink3,
            padding: '0 4px',
            maxWidth: 720,
          }}
        >
          {actionMsg}
        </div>
      )}

      {/* 计数 KPI 行 */}
      {isOn && (
        <div
          style={{
            background: C.surface,
            border: `1px solid ${C.border}`,
            borderRadius: 10,
            padding: '14px 18px',
            display: 'grid',
            gridTemplateColumns: 'repeat(3, 1fr)',
            gap: 18,
            maxWidth: 720,
          }}
        >
          <Kpi label="改写包数" value={fmtN(totalRewrites)} mono />
          <Kpi label="TCP · 走代理" value={fmtN(tcpProxy)} mono color={C.live} />
          <Kpi label="TCP · 直连" value={fmtN(tcpDirect)} mono color={C.ink3} />
        </div>
      )}

      {/* 错误提示 */}
      {loadErr && (
        <div
          style={{
            background: C.surface,
            border: `1px solid ${C.error}30`,
            borderRadius: 8,
            padding: '12px 16px',
            maxWidth: 720,
            fontSize: 11.5,
            color: C.error,
            fontFamily: C.fontMono,
          }}
        >
          /api/tun/state 拉取失败: {loadErr}
        </div>
      )}

      {/* 说明 */}
      <div
        style={{
          background: C.surface,
          border: `1px solid ${C.borderSoft}`,
          borderRadius: 10,
          padding: '14px 18px',
          maxWidth: 720,
          fontSize: 12,
          color: C.ink3,
          lineHeight: 1.7,
        }}
      >
        <div
          style={{
            fontSize: 11,
            color: C.ink3,
            fontWeight: 500,
            letterSpacing: '.06em',
            textTransform: 'uppercase',
            marginBottom: 8,
          }}
        >
          工作原理
        </div>
        <div>
          本地启 wintun 虚拟网卡 + gameproxy 服务 (PID 9901),
          劫持游戏 TCP 流量按 rule1/rule2 改写指纹后转发。
          完全本地化,不需装任何 APK 或 VPN 客户端到模拟器里。
        </div>
        <div style={{ marginTop: 10, color: C.ink4, fontSize: 11.5 }}>
          状态来源:{' '}
          <span className="gb-mono" style={{ color: C.ink3 }}>
            /api/tun/state
          </span>{' '}
          (反代本机 gameproxy <span className="gb-mono">:9901</span>)
        </div>
      </div>
    </div>
  )
}

function Kpi({
  label,
  value,
  mono,
  color,
}: {
  label: string
  value: string
  mono?: boolean
  color?: string
}) {
  return (
    <div>
      <div
        style={{
          fontSize: 9.5,
          color: C.ink3,
          fontWeight: 500,
          letterSpacing: '.06em',
          textTransform: 'uppercase',
        }}
      >
        {label}
      </div>
      <div
        className={mono ? 'gb-mono' : ''}
        style={{
          marginTop: 4,
          fontSize: 20,
          fontWeight: 600,
          color: color || C.ink,
          lineHeight: 1.1,
        }}
      >
        {value}
      </div>
    </div>
  )
}
