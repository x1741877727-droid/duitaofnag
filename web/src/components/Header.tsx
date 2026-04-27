import { useMemo, useState } from 'react'
import { useAppStore } from '@/lib/store'
import { Button } from '@/components/ui/button'
import { Play, Square, Settings, LayoutDashboard, FileText, ChevronDown, ChevronUp, Activity, Archive, Image, BarChart3, Crosshair, Brain } from 'lucide-react'
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
    <header className="h-12 border-b border-border bg-card flex items-center justify-between px-4 shrink-0 gap-3">
      <div className="flex items-center gap-4 min-w-0">
        <h1 className="text-lg font-bold text-foreground tracking-tight shrink-0">FightMaster</h1>

        <nav className="flex items-center gap-0.5 min-w-0 overflow-x-auto">
          {([
            { key: 'dashboard', label: '运行', Icon: LayoutDashboard, title: '运行控制' },
            { key: 'console', label: '中控台', Icon: Activity, title: '中控台' },
            { key: 'archive', label: '档案', Icon: Archive, title: '决策档案' },
            { key: 'templates', label: '模版', Icon: Image, title: '模版库' },
            { key: 'yolo', label: 'YOLO', Icon: Crosshair, title: 'YOLO' },
            { key: 'memory', label: '记忆', Icon: Brain, title: '记忆库' },
            { key: 'perf', label: '性能', Icon: BarChart3, title: '性能监控' },
            { key: 'settings', label: '设置', Icon: Settings, title: '设置' },
          ] as const).map(({ key, label, Icon, title }) => {
            const active = currentView === key
            return (
              <Button
                key={key}
                variant={active ? 'secondary' : 'ghost'}
                size="sm"
                onClick={() => setCurrentView(key)}
                className={cn(
                  'gap-1.5 px-2 shrink-0 transition-all duration-200 ease-out',
                  active && 'px-3 font-semibold',
                )}
                title={title}
              >
                <Icon className="h-4 w-4" />
                {/* 选中: 永远显文字; 未选中: xl 屏才显 */}
                <span
                  className={cn(
                    'overflow-hidden transition-all duration-200',
                    active
                      ? 'max-w-[80px] opacity-100'
                      : 'max-w-0 opacity-0 xl:max-w-[80px] xl:opacity-100',
                  )}
                >
                  {label}
                </span>
              </Button>
            )
          })}
        </nav>
      </div>

      <div className="flex items-center gap-3 shrink-0">
        {/* 状态统计 */}
        <div className="flex items-center gap-3 text-sm whitespace-nowrap">
          <div className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-info" />
            <span className="text-muted-foreground">运行</span>
            <span className="font-semibold text-foreground">{statusCounts.running}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-success" />
            <span className="text-muted-foreground">就绪</span>
            <span className="font-semibold text-foreground">{statusCounts.ready}</span>
          </div>
          {statusCounts.errors > 0 && (
            <div className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full bg-destructive" />
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
