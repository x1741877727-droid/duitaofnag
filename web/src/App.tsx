import { useWebSocket } from '@/hooks/useWebSocket'
import { useAppStore } from '@/lib/store'
import { Header } from '@/components/header'
import { Dashboard } from '@/components/dashboard'
import { SettingsView } from '@/components/settings-view'
import { LogDrawer } from '@/components/log-panel'

function App() {
  useWebSocket()
  const { currentView, showLogPanel } = useAppStore()

  return (
    <div className="h-screen flex flex-col bg-background overflow-hidden">
      <Header />

      <div className="flex-1 flex min-h-0">
        {/* 主内容 */}
        <main className="flex-1 overflow-y-auto p-4">
          <div className="max-w-5xl mx-auto animate-page" key={currentView}>
            {currentView === 'dashboard' ? <Dashboard /> : <SettingsView />}
          </div>
        </main>

        {/* 日志侧边抽屉 */}
        {showLogPanel && <LogDrawer />}
      </div>
    </div>
  )
}

export default App
