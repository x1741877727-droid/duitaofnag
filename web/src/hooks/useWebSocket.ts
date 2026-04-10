import { useEffect, useRef } from 'react'
import { useAppStore, type Instance, type InstanceState } from '@/lib/store'

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<number | undefined>(undefined)
  const store = useAppStore

  useEffect(() => {
    function connect() {
      const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
      const url = `${protocol}//${location.host}/ws`
      const ws = new WebSocket(url)
      wsRef.current = ws

      ws.onmessage = (evt) => {
        try {
          const msg = JSON.parse(evt.data)
          switch (msg.type) {
            case 'snapshot': {
              // 转换后端格式 → 前端 Instance 格式
              const instances: Record<string, Instance> = {}
              if (msg.instances) {
                for (const [key, val] of Object.entries(msg.instances)) {
                  const v = val as Record<string, unknown>
                  instances[key] = {
                    index: v.index as number,
                    group: (v.group as string || 'A') as 'A' | 'B',
                    role: (v.role as string || 'member') as 'captain' | 'member',
                    state: (v.state as InstanceState) || 'init',
                    nickname: (v.nickname as string) || `实例${v.index}`,
                    error: (v.error as string) || '',
                    stateDuration: (v.state_duration as number) || 0,
                    adbSerial: `emulator-${5554 + (v.index as number) * 2}`,
                  }
                }
              }
              store.getState().setInstances(instances)
              store.getState().setIsRunning(!!msg.running)
              if (msg.stats?.running_duration) {
                store.getState().setRunningDuration(msg.stats.running_duration)
              }
              break
            }
            case 'state_change': {
              const d = msg.data
              store.getState().updateInstance(String(d.instance), {
                state: d.new as InstanceState,
                stateDuration: 0,
              })
              break
            }
            case 'log': {
              const d = msg.data
              let instance: number | 'SYS' | 'GUARD' = d.instance
              if (d.instance === -1) instance = 'SYS'
              if (d.message?.includes('[守卫]')) instance = 'GUARD'

              store.getState().addLog({
                timestamp: d.timestamp * 1000,
                instance,
                level: d.level || 'info',
                message: d.message || '',
                state: d.state as InstanceState | undefined,
              })
              break
            }
          }
        } catch {
          // ignore parse errors
        }
      }

      ws.onclose = () => {
        reconnectTimer.current = window.setTimeout(connect, 3000)
      }

      ws.onerror = () => ws.close()

      const heartbeat = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send('ping')
      }, 15000)

      ws.addEventListener('close', () => clearInterval(heartbeat))
    }

    connect()

    return () => {
      clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [])
}
