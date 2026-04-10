import { useEffect, useRef } from 'react'
import { useAppStore, type LogEntry } from '@/lib/store'
import { cn } from '@/lib/utils'
import { X, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'

export function LogDrawer() {
  const {
    logs, clearLogs, logFilter, setLogFilter,
    setShowLogPanel, instances,
  } = useAppStore()

  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [logs])

  const filteredLogs = logFilter === 'all'
    ? logs
    : logs.filter(log => log.instance === logFilter)

  const instanceIndices = Object.keys(instances).map(Number).sort((a, b) => a - b)

  const formatTime = (timestamp: number) => {
    const date = new Date(timestamp)
    return date.toLocaleTimeString('zh-CN', {
      hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false
    })
  }

  const getLogColor = (level: LogEntry['level']) => {
    switch (level) {
      case 'error': return 'text-error'
      case 'warn': return 'text-warning'
      default: return 'text-foreground/80'
    }
  }

  const getLabel = (instance: LogEntry['instance']) => {
    if (instance === 'SYS') return 'SYS'
    if (instance === 'GUARD') return 'GRD'
    return `#${instance}`
  }

  return (
    <div className="w-72 border-l border-border bg-card flex flex-col shrink-0 animate-fade">
      {/* 标题 */}
      <div className="flex items-center justify-between h-10 px-3 border-b border-border shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium">日志</span>
          <span className="text-[10px] text-muted-foreground">{filteredLogs.length}</span>
        </div>
        <div className="flex items-center gap-0.5">
          <Button variant="ghost" size="icon" className="h-6 w-6" onClick={clearLogs}>
            <Trash2 className="w-3 h-3" />
          </Button>
          <Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => setShowLogPanel(false)}>
            <X className="w-3 h-3" />
          </Button>
        </div>
      </div>

      {/* 过滤器 */}
      <div className="flex items-center gap-0.5 px-2 py-1.5 border-b border-border shrink-0 flex-wrap">
        <FilterBtn active={logFilter === 'all'} onClick={() => setLogFilter('all')}>全部</FilterBtn>
        {instanceIndices.slice(0, 8).map(idx => (
          <FilterBtn key={idx} active={logFilter === idx} onClick={() => setLogFilter(idx)}>
            #{idx}
          </FilterBtn>
        ))}
      </div>

      {/* 日志内容 */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-2 py-1 font-mono text-[11px] leading-relaxed">
        {filteredLogs.length === 0 ? (
          <div className="flex items-center justify-center h-20 text-muted-foreground text-xs">
            暂无日志
          </div>
        ) : (
          filteredLogs.map(log => (
            <div
              key={log.id}
              className={cn(
                'flex gap-1.5 py-0.5 px-1 rounded-sm',
                log.level === 'error' && 'bg-error/5'
              )}
            >
              <span className="text-muted-foreground shrink-0">{formatTime(log.timestamp)}</span>
              <span className={cn(
                'shrink-0 font-medium',
                log.instance === 'SYS' ? 'text-info' :
                log.instance === 'GUARD' ? 'text-success' : 'text-muted-foreground'
              )}>
                {getLabel(log.instance)}
              </span>
              <span className={cn('break-all', getLogColor(log.level))}>
                {log.message}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  )
}

function FilterBtn({ children, active, onClick }: {
  children: React.ReactNode; active: boolean; onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'px-1.5 py-0.5 text-[10px] rounded transition-colors',
        active
          ? 'bg-primary text-primary-foreground'
          : 'text-muted-foreground hover:text-foreground hover:bg-secondary'
      )}
    >
      {children}
    </button>
  )
}
