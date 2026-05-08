import { lazy, Suspense, useEffect, useState } from 'react'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useLiveStream } from '@/lib/useLiveStream'
import { useAppStore, type TeamGroup, type TeamRole } from '@/lib/store'
import {
  getDemoAccounts,
  getDemoEmulators,
  getDemoInstances,
  getDemoLiveDecisions,
  getDemoPhaseHistory,
} from '@/lib/demo-data'
import { Header } from '@/components/header/Header'
import { DashboardLayoutA } from '@/components/dashboard/DashboardLayoutA'
import { LogDrawer } from '@/components/log-panel'

// 主页 (中控台) eager 加载, 其他子页 lazy split.
// 减小首屏 bundle, 用户进特定页面才加载对应代码块.
const SettingsView = lazy(() =>
  import('@/components/settings-view').then((m) => ({ default: m.SettingsView })),
)
const PerfView = lazy(() =>
  import('@/components/perf/PerfView').then((m) => ({ default: m.PerfView })),
)
const RecognitionShell = lazy(() =>
  import('@/components/recognition/RecognitionShell').then((m) => ({
    default: m.RecognitionShell,
  })),
)
const DataShell = lazy(() =>
  import('@/components/data/DataShell').then((m) => ({ default: m.DataShell })),
)

/** 派生: URL ?demo=running|standby 时注入 mock 数据直接看视觉. */
type DemoMode = 'running' | 'standby' | null
function getDemoMode(): DemoMode {
  if (typeof window === 'undefined') return null
  const p = new URLSearchParams(window.location.search)
  const v = p.get('demo')
  return v === 'running' || v === 'standby' ? v : null
}

function App() {
  const demo = getDemoMode()

  // hooks 总是调用 (Rules of Hooks); demo 模式 WS 失败也不影响 mock UI
  useWebSocket()
  useLiveStream()

  const {
    currentView,
    showLogPanel,
    setAccounts,
    setEmulators,
    isRunning,
    setIsRunning,
    setInstances,
  } = useAppStore()

  // demo 模式: 注入 mock 数据 (只跑一次)
  useEffect(() => {
    if (!demo) return
    setAccounts(getDemoAccounts())
    setEmulators(getDemoEmulators())
    if (demo === 'running') {
      setInstances(getDemoInstances())
      setIsRunning(true)
      useAppStore.setState({
        liveDecisions: getDemoLiveDecisions(),
        phaseHistory: getDemoPhaseHistory(),
        runningDuration: 8163, // 02:16:03
        liveConnected: true,
      })
    } else {
      // standby — 只注入 accounts + emulators, isRunning 保持 false → 进 HeroSplash
      setIsRunning(false)
      setInstances({})
    }
  }, [demo, setAccounts, setEmulators, setInstances, setIsRunning])

  // 启动时加载账号 (一次)
  useEffect(() => {
    if (demo) return
    fetch('/api/accounts')
      .then((r) => r.json())
      .then((data: Record<string, unknown>[]) => {
        if (Array.isArray(data) && data.length > 0) {
          setAccounts(
            data.map((a) => ({
              index: (a.instance_index as number) ?? 0,
              name: (a.nickname as string) || '',
              running: false,
              adbSerial: `emulator-${5554 + ((a.instance_index as number) ?? 0) * 2}`,
              group: (a.group as TeamGroup) || 'A',
              role: (a.role as TeamRole) || 'member',
              nickname: (a.nickname as string) || '',
              gameId: (a.game_id as string) || '',
            })),
          )
        }
      })
      .catch(() => {})
  }, [demo, setAccounts])

  // emulator running 状态需要持续轮询 — 用户启停模拟器后 store 才能跟上
  useEffect(() => {
    if (demo) return
    let alive = true
    const load = () =>
      fetch('/api/emulators')
        .then((r) => r.json())
        .then((json) => {
          if (!alive || !json.instances) return
          setEmulators(
            json.instances.map((e: Record<string, unknown>) => ({
              index: e.index as number,
              name: e.name as string,
              running: e.running as boolean,
              adbSerial:
                (e.adb_serial as string) ||
                `emulator-${5554 + (e.index as number) * 2}`,
            })),
          )
        })
        .catch(() => {})
    load()
    const t = setInterval(load, 5000)
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [demo, setEmulators])

  const [actionLoading, setActionLoading] = useState(false)
  const onAction = async () => {
    setActionLoading(true)
    try {
      if (isRunning) {
        await fetch('/api/stop', { method: 'POST' })
        setIsRunning(false)
        setInstances({})
      } else {
        const r = await fetch('/api/start', { method: 'POST' })
        const json = await r.json()
        if (json.ok) setIsRunning(true)
      }
    } catch {
      // ignore
    } finally {
      setActionLoading(false)
    }
  }

  return (
    <div className="h-screen flex flex-col bg-background overflow-hidden">
      <Header onAction={onAction} loading={actionLoading} />

      <div className="flex-1 flex min-h-0">
        <main className="flex-1 min-h-0 flex flex-col overflow-hidden">
          {(currentView === 'console' || currentView === 'dashboard') && (
            <DashboardLayoutA onStart={onAction} onStop={onAction} />
          )}
          {currentView !== 'console' && currentView !== 'dashboard' && (
            <Suspense fallback={<LazyFallback />}>
              {currentView === 'data' && <DataShell />}
              {currentView === 'recognition' && <RecognitionShell />}
              {currentView === 'settings' && <SettingsView />}
              {currentView === 'perf' && <PerfView />}
            </Suspense>
          )}
        </main>

        {showLogPanel && <LogDrawer />}
      </div>
    </div>
  )
}

function LazyFallback() {
  return (
    <div
      style={{
        flex: 1,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        color: '#7d7869',
        fontSize: 13,
        fontFamily: "'IBM Plex Mono', monospace",
      }}
    >
      加载中…
    </div>
  )
}

export default App
