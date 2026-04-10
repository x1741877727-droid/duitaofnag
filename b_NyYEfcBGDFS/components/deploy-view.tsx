'use client'

import { useAppStore } from '@/lib/store'
import { cn } from '@/lib/utils'
import { RefreshCw, Play, MonitorSmartphone, Server } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useState } from 'react'

export function DeployView() {
  const { emulators, setIsRunning } = useAppStore()
  const [refreshing, setRefreshing] = useState(false)

  const onlineEmulators = emulators.filter(e => e.running)
  const offlineEmulators = emulators.filter(e => !e.running)

  const handleRefresh = () => {
    setRefreshing(true)
    setTimeout(() => setRefreshing(false), 1000)
  }

  const handleTestRun = () => {
    setIsRunning(true)
  }

  return (
    <div className="space-y-6">
      {/* 标题栏 */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <Server className="w-5 h-5 text-muted-foreground" />
            部署中心
          </h2>
          <p className="text-sm text-muted-foreground mt-1">
            {emulators.length} 个模拟器，{onlineEmulators.length} 个在线
          </p>
        </div>
        <Button 
          variant="outline" 
          size="sm" 
          className="gap-2"
          onClick={handleRefresh}
        >
          <RefreshCw className={cn('w-4 h-4', refreshing && 'animate-spin')} />
          刷新
        </Button>
      </div>

      {/* 在线实例 */}
      {onlineEmulators.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center gap-2 text-sm">
            <span className="h-2 w-2 rounded-full bg-success" />
            <span className="font-medium">在线</span>
            <span className="text-muted-foreground">可操作</span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
            {onlineEmulators.map(emulator => (
              <EmulatorCard 
                key={emulator.index} 
                emulator={emulator} 
                online 
                onTestRun={handleTestRun}
              />
            ))}
          </div>
        </div>
      )}

      {/* 离线实例 */}
      {offlineEmulators.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center gap-2 text-sm">
            <span className="h-2 w-2 rounded-full bg-muted-foreground" />
            <span className="font-medium text-muted-foreground">离线</span>
            <span className="text-muted-foreground">需在雷电多开器中启动</span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
            {offlineEmulators.map(emulator => (
              <EmulatorCard 
                key={emulator.index} 
                emulator={emulator} 
                online={false}
              />
            ))}
          </div>
        </div>
      )}

      {/* 提示信息 */}
      <div className="bg-muted/50 border border-border rounded-lg p-4 text-sm text-muted-foreground">
        点击「测试运行」单独测试一个实例的完整流程。全部准备好后，在「设置」中配置分组，再点击「启动」批量运行。
      </div>
    </div>
  )
}

interface EmulatorCardProps {
  emulator: {
    index: number
    name: string
    running: boolean
    adbSerial: string
  }
  online: boolean
  onTestRun?: () => void
}

function EmulatorCard({ emulator, online, onTestRun }: EmulatorCardProps) {
  return (
    <div className={cn(
      'bg-card border border-border rounded-lg p-4',
      !online && 'opacity-50'
    )}>
      <div className="flex items-start gap-3 mb-3">
        <div className={cn(
          'w-10 h-10 rounded-lg flex items-center justify-center',
          online ? 'bg-success-muted' : 'bg-muted'
        )}>
          <MonitorSmartphone className={cn(
            'w-5 h-5',
            online ? 'text-success' : 'text-muted-foreground'
          )} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="font-medium text-sm">#{emulator.index}</div>
          <div className="text-xs text-muted-foreground truncate">
            {emulator.name}
          </div>
        </div>
      </div>

      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <div className={cn(
            'w-1.5 h-1.5 rounded-full',
            online ? 'bg-success' : 'bg-muted-foreground'
          )} />
          <span className="text-xs text-muted-foreground">
            {online ? emulator.adbSerial : '离线'}
          </span>
        </div>
        
        {online && onTestRun && (
          <Button 
            size="sm" 
            variant="outline"
            className="h-7 gap-1.5 text-xs"
            onClick={onTestRun}
          >
            <Play className="w-3 h-3" />
            测试
          </Button>
        )}
      </div>
    </div>
  )
}
