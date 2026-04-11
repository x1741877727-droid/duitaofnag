import { useMemo, useState } from 'react'
import { useAppStore } from '@/lib/store'
import { Button } from '@/components/ui/button'
import { Play, Square, Settings, LayoutDashboard, FileText, ChevronDown, ChevronUp } from 'lucide-react'
import { cn } from '@/lib/utils'

export function Header() {
  const {
    isRunning,
    setIsRunning,
    currentView,
    setCurrentView,
    showLogPanel,
    setShowLogPanel,
    instances,
    setInstances,
  } = useAppStore()
  const [loading, setLoading] = useState(false)

  const statusCounts = useMemo(() => {
    const instanceList = Object.values(instances)
    const total = instanceList.length
    const running = instanceList.filter(i => 
      !['init', 'done', 'error', 'ready', 'in_game'].includes(i.state)
    ).length
    const ready = instanceList.filter(i => 
      ['lobby', 'done', 'ready', 'in_game'].includes(i.state)
    ).length
    const errors = instanceList.filter(i => i.state === 'error').length
    return { total, running, ready, errors }
  }, [instances])

  return (
    <header className="h-12 border-b border-border bg-card flex items-center justify-between px-4 shrink-0">
      <div className="flex items-center gap-8">
        <h1 className="text-lg font-bold text-foreground tracking-tight">FightMaster</h1>
        
        <nav className="flex items-center gap-1">
          <Button
            variant={currentView === 'dashboard' ? 'secondary' : 'ghost'}
            size="sm"
            onClick={() => setCurrentView('dashboard')}
            className="gap-2"
          >
            <LayoutDashboard className="h-4 w-4" />
            控制台
          </Button>
          <Button
            variant={currentView === 'settings' ? 'secondary' : 'ghost'}
            size="sm"
            onClick={() => setCurrentView('settings')}
            className="gap-2"
          >
            <Settings className="h-4 w-4" />
            设置
          </Button>
        </nav>
      </div>

      <div className="flex items-center gap-5">
        {/* 状态统计 */}
        <div className="flex items-center gap-5 text-sm">
          <div className="flex items-center gap-2">
            <span className="h-2 w-2 rounded-full bg-info" />
            <span className="text-muted-foreground">运行中</span>
            <span className="font-semibold text-foreground">{statusCounts.running}</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="h-2 w-2 rounded-full bg-success" />
            <span className="text-muted-foreground">已就绪</span>
            <span className="font-semibold text-foreground">{statusCounts.ready}</span>
          </div>
          {statusCounts.errors > 0 && (
            <div className="flex items-center gap-2">
              <span className="h-2 w-2 rounded-full bg-error" />
              <span className="text-muted-foreground">错误</span>
              <span className="font-semibold text-foreground">{statusCounts.errors}</span>
            </div>
          )}
        </div>

        <div className="h-5 w-px bg-border" />

        {/* 日志面板切换 */}
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setShowLogPanel(!showLogPanel)}
          className={cn('gap-2', showLogPanel && 'bg-secondary')}
        >
          <FileText className="h-4 w-4" />
          日志
          {showLogPanel ? (
            <ChevronDown className="h-4 w-4" />
          ) : (
            <ChevronUp className="h-4 w-4" />
          )}
        </Button>

        {/* 运行控制 */}
        <Button
          variant={isRunning ? 'destructive' : 'default'}
          size="sm"
          onClick={async () => {
            setLoading(true)
            try {
              if (isRunning) {
                await fetch('/api/stop', { method: 'POST' })
                setIsRunning(false)
                setInstances({})
              } else {
                const res = await fetch('/api/start', { method: 'POST' })
                const json = await res.json()
                if (json.ok) setIsRunning(true)
              }
            } catch {}
            setLoading(false)
          }}
          disabled={loading}
          className="gap-2 min-w-24"
        >
          {isRunning ? (
            <><Square className="h-4 w-4" />停止</>
          ) : (
            <><Play className="h-4 w-4" />{loading ? '启动中...' : '启动'}</>
          )}
        </Button>
      </div>
    </header>
  )
}
