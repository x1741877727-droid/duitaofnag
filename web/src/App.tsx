import { useEffect } from 'react'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useLiveStream } from '@/lib/useLiveStream'
import { useAppStore, type TeamGroup, type TeamRole } from '@/lib/store'
import { Header } from '@/components/Header'
import { Dashboard } from '@/components/dashboard'
import { SettingsView } from '@/components/settings-view'
import { LogDrawer } from '@/components/log-panel'
import { Console } from '@/components/console/Console'
import { Archive } from '@/components/archive/Archive'
import { TemplateLibrary } from '@/components/templates/TemplateLibrary'
import { YoloView } from '@/components/yolo/YoloView'
import { PerfView } from '@/components/perf/PerfView'

function App() {
  useWebSocket()
  useLiveStream()
  const { currentView, showLogPanel, setAccounts, setEmulators } = useAppStore()

  // 启动时加载账号和模拟器（不用等进设置页）
  useEffect(() => {
    fetch('/api/accounts').then(r => r.json()).then((data: Record<string, unknown>[]) => {
      if (Array.isArray(data) && data.length > 0) {
        setAccounts(data.map(a => ({
          index: (a.instance_index as number) ?? 0,
          name: (a.nickname as string) || '',
          running: false,
          adbSerial: `emulator-${5554 + ((a.instance_index as number) ?? 0) * 2}`,
          group: (a.group as TeamGroup) || 'A',
          role: (a.role as TeamRole) || 'member',
          nickname: (a.nickname as string) || '',
          gameId: (a.game_id as string) || '',
        })))
      }
    }).catch(() => {})
    fetch('/api/emulators').then(r => r.json()).then(json => {
      if (json.instances) {
        setEmulators(json.instances.map((e: Record<string, unknown>) => ({
          index: e.index as number,
          name: e.name as string,
          running: e.running as boolean,
          adbSerial: (e.adb_serial as string) || `emulator-${5554 + (e.index as number) * 2}`,
        })))
      }
    }).catch(() => {})
  }, [])

  return (
    <div className="h-screen flex flex-col bg-background overflow-hidden">
      <Header />

      <div className="flex-1 flex min-h-0">
        {/* 主内容 */}
        <main className="flex-1 overflow-y-auto p-4">
          <div
            className={
              currentView === 'console' || currentView === 'yolo' || currentView === 'templates'
                ? 'h-full animate-page'
                : 'max-w-5xl mx-auto animate-page'
            }
            key={currentView}
          >
            {currentView === 'dashboard' && <Dashboard />}
            {currentView === 'console' && <Console />}
            {currentView === 'archive' && <Archive />}
            {currentView === 'templates' && <TemplateLibrary />}
            {currentView === 'yolo' && <YoloView />}
            {currentView === 'perf' && <PerfView />}
            {currentView === 'settings' && <SettingsView />}
          </div>
        </main>

        {/* 日志侧边抽屉 */}
        {showLogPanel && <LogDrawer />}
      </div>
    </div>
  )
}

export default App
