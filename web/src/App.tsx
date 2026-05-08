import { useEffect, useState } from 'react'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useLiveStream } from '@/lib/useLiveStream'
import { useAppStore, type TeamGroup, type TeamRole } from '@/lib/store'
import { Header } from '@/components/header/Header'
import { DashboardLayoutA } from '@/components/dashboard/DashboardLayoutA'
import { SettingsView } from '@/components/settings-view'
import { LogDrawer } from '@/components/log-panel'
import { PerfView } from '@/components/perf/PerfView'
import { AcceleratorView } from '@/components/accelerator-view'
import { RecognitionView } from '@/components/recognition/RecognitionView'
import { DataView } from '@/components/data/DataView'

function App() {
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

  // 启动时加载账号 + 模拟器
  useEffect(() => {
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

    fetch('/api/emulators')
      .then((r) => r.json())
      .then((json) => {
        if (json.instances) {
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
        }
      })
      .catch(() => {})
  }, [setAccounts, setEmulators])

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
          {currentView === 'console' && (
            <DashboardLayoutA onStart={onAction} onStop={onAction} />
          )}
          {currentView === 'dashboard' && (
            <DashboardLayoutA onStart={onAction} onStop={onAction} />
          )}
          {currentView === 'data' && <DataView />}
          {currentView === 'recognition' && <RecognitionView />}
          {currentView === 'settings' && <SettingsView />}
          {/* 临时保留: perf / accelerator (阶段 5/6 删) */}
          {currentView === 'perf' && <PerfView />}
          {currentView === 'accelerator' && <AcceleratorView />}
        </main>

        {showLogPanel && <LogDrawer />}
      </div>
    </div>
  )
}

export default App
