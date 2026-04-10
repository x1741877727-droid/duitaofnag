'use client'

import { Header } from '@/components/header'
import { Dashboard } from '@/components/dashboard'
import { SettingsView } from '@/components/settings-view'
import { LogPanel } from '@/components/log-panel'
import { useAppStore } from '@/lib/store'

export default function GameBotApp() {
  const { currentView, showLogPanel } = useAppStore()

  return (
    <div className="h-screen flex flex-col bg-background">
      {/* Header */}
      <Header />

      {/* 主内容区 */}
      <main className="flex-1 overflow-y-auto p-6">
        <div className="max-w-6xl mx-auto">
          {currentView === 'dashboard' ? (
            <Dashboard />
          ) : (
            <SettingsView />
          )}
        </div>
      </main>

      {/* 底部日志面板 */}
      {showLogPanel && <LogPanel />}
    </div>
  )
}
