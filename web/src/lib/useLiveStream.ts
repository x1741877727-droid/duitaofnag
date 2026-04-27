/**
 * useLiveStream — WebSocket 接入中控台.
 *
 * 连 /ws/live, 收 decision / phase_change / intervene_ack / perf 事件,
 * 自动 dispatch 到 zustand store. 断线 3s 重连. 心跳每 25s 发 ping.
 *
 * 用法 (在 App.tsx 顶层调一次):
 *   useLiveStream()
 */
import { useEffect, useRef } from 'react'
import { useAppStore, type LiveEvent, type LiveDecisionEvent, type PhaseChangeEvent } from './store'

const RECONNECT_MS = 3000
const PING_MS = 25000

function buildWsUrl(): string {
  // 同源 ws (生产: exe 内嵌 / LAN 浏览器都通)
  // 开发模式 vite proxy /ws 转 8900
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const host = location.host
  return `${proto}//${host}/ws/live`
}

export function useLiveStream() {
  const setLiveConnected = useAppStore((s) => s.setLiveConnected)
  const pushLiveDecision = useAppStore((s) => s.pushLiveDecision)
  const pushPhaseChange = useAppStore((s) => s.pushPhaseChange)

  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimerRef = useRef<number | null>(null)
  const pingTimerRef = useRef<number | null>(null)
  const aliveRef = useRef(true)

  useEffect(() => {
    aliveRef.current = true

    const cleanup = () => {
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current)
        reconnectTimerRef.current = null
      }
      if (pingTimerRef.current !== null) {
        window.clearInterval(pingTimerRef.current)
        pingTimerRef.current = null
      }
      const ws = wsRef.current
      if (ws) {
        try {
          ws.close()
        } catch {}
        wsRef.current = null
      }
    }

    const scheduleReconnect = () => {
      if (!aliveRef.current) return
      if (reconnectTimerRef.current !== null) return
      reconnectTimerRef.current = window.setTimeout(() => {
        reconnectTimerRef.current = null
        connect()
      }, RECONNECT_MS)
    }

    const connect = () => {
      if (!aliveRef.current) return
      let ws: WebSocket
      try {
        ws = new WebSocket(buildWsUrl())
      } catch {
        scheduleReconnect()
        return
      }
      wsRef.current = ws

      ws.addEventListener('open', () => {
        if (!aliveRef.current) return
        setLiveConnected(true)
        // 心跳
        if (pingTimerRef.current !== null) window.clearInterval(pingTimerRef.current)
        pingTimerRef.current = window.setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            try {
              ws.send(JSON.stringify({ type: 'ping' }))
            } catch {}
          }
        }, PING_MS)
      })

      ws.addEventListener('message', (e) => {
        let data: LiveEvent
        try {
          data = JSON.parse(e.data)
        } catch {
          return
        }
        switch (data.type) {
          case 'decision':
            pushLiveDecision(data as LiveDecisionEvent)
            break
          case 'phase_change':
            pushPhaseChange(data as PhaseChangeEvent)
            break
          case 'intervene_ack':
            // Day 3 处理
            break
          case 'perf':
            // Day 5 处理
            break
          case 'hello':
          case 'pong':
          default:
            break
        }
      })

      ws.addEventListener('close', () => {
        setLiveConnected(false)
        if (pingTimerRef.current !== null) {
          window.clearInterval(pingTimerRef.current)
          pingTimerRef.current = null
        }
        scheduleReconnect()
      })

      ws.addEventListener('error', () => {
        // close 会跟着触发, 不在这里 reconnect 避免重复
      })
    }

    connect()
    return () => {
      aliveRef.current = false
      cleanup()
      setLiveConnected(false)
    }
  }, [setLiveConnected, pushLiveDecision, pushPhaseChange])
}
