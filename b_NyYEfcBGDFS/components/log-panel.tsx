'use client'

import { useEffect, useRef } from 'react'
import { useAppStore, type LogEntry } from '@/lib/store'
import { cn } from '@/lib/utils'
import { X, Trash2, ChevronUp, ChevronDown } from 'lucide-react'
import { Button } from '@/components/ui/button'

export function LogPanel() {
  const { 
    logs, 
    clearLogs, 
    logFilter, 
    setLogFilter,
    setShowLogPanel,
    instances,
    logPanelExpanded,
    setLogPanelExpanded
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
      hour: '2-digit', 
      minute: '2-digit', 
      second: '2-digit',
      hour12: false 
    })
  }

  const getLogColor = (level: LogEntry['level']) => {
    switch (level) {
      case 'error': return 'text-error'
      case 'warn': return 'text-warning'
      default: return 'text-foreground'
    }
  }

  const getInstanceLabel = (instance: LogEntry['instance']) => {
    if (instance === 'SYS') return 'SYS'
    if (instance === 'GUARD') return 'GRD'
    return `#${instance}`
  }

  return (
    <div className={cn(
      'border-t border-border bg-card transition-all duration-200 flex flex-col',
      logPanelExpanded ? 'h-48' : 'h-10'
    )}>
      {/* 标题栏 */}
      <div className="flex items-center justify-between h-10 px-4 flex-shrink-0">
        <div className="flex items-center gap-3">
          <button
            onClick={() => setLogPanelExpanded(!logPanelExpanded)}
            className="flex items-center gap-2 text-sm font-medium hover:text-primary transition-colors"
          >
            {logPanelExpanded ? (
              <ChevronDown className="w-4 h-4" />
            ) : (
              <ChevronUp className="w-4 h-4" />
            )}
            <span>日志</span>
          </button>
          <span className="text-xs text-muted-foreground">
            {filteredLogs.length} 条
          </span>
          
          {/* 过滤按钮 */}
          <div className="flex items-center gap-1 ml-3">
            <FilterButton 
              active={logFilter === 'all'} 
              onClick={() => setLogFilter('all')}
            >
              全部
            </FilterButton>
            {instanceIndices.slice(0, 6).map(idx => (
              <FilterButton 
                key={idx}
                active={logFilter === idx} 
                onClick={() => setLogFilter(idx)}
              >
                #{idx}
              </FilterButton>
            ))}
          </div>
        </div>
        
        <div className="flex items-center gap-1">
          <Button 
            variant="ghost" 
            size="icon" 
            className="h-7 w-7"
            onClick={clearLogs}
          >
            <Trash2 className="w-3.5 h-3.5" />
          </Button>
          <Button 
            variant="ghost" 
            size="icon" 
            className="h-7 w-7"
            onClick={() => setShowLogPanel(false)}
          >
            <X className="w-3.5 h-3.5" />
          </Button>
        </div>
      </div>

      {/* 日志内容 */}
      {logPanelExpanded && (
        <div 
          ref={scrollRef}
          className="flex-1 overflow-y-auto px-4 pb-2 font-mono text-xs"
        >
          {filteredLogs.length === 0 ? (
            <div className="flex items-center justify-center h-full text-muted-foreground">
              暂无日志
            </div>
          ) : (
            <div className="space-y-px">
              {filteredLogs.map(log => (
                <div 
                  key={log.id}
                  className={cn(
                    'flex items-start gap-3 py-1 px-2 rounded-sm',
                    log.level === 'error' && 'bg-error/5'
                  )}
                >
                  <span className="text-muted-foreground w-16 flex-shrink-0">
                    {formatTime(log.timestamp)}
                  </span>
                  <span className={cn(
                    'w-8 flex-shrink-0 font-medium',
                    log.instance === 'SYS' ? 'text-info' : 
                    log.instance === 'GUARD' ? 'text-success' : 'text-muted-foreground'
                  )}>
                    {getInstanceLabel(log.instance)}
                  </span>
                  <span className={cn('flex-1', getLogColor(log.level))}>
                    {log.message}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function FilterButton({ 
  children, 
  active, 
  onClick 
}: { 
  children: React.ReactNode
  active: boolean
  onClick: () => void 
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'px-2 py-0.5 text-xs rounded-md transition-colors',
        active 
          ? 'bg-primary text-primary-foreground' 
          : 'text-muted-foreground hover:text-foreground hover:bg-secondary'
      )}
    >
      {children}
    </button>
  )
}
