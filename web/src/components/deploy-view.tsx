import { useEffect, useState } from 'react'
import { useAppStore, type Emulator } from '@/lib/store'
import { cn } from '@/lib/utils'
import { RefreshCw, Play, MonitorSmartphone, Server } from 'lucide-react'
import { Button } from '@/components/ui/button'

export function DeployView() {
  const { emulators, setEmulators, setIsRunning } = useAppStore()
  const [refreshing, setRefreshing] = useState(false)
  const [testingIdx, setTestingIdx] = useState<number | null>(null)

  const onlineEmulators = emulators.filter(e => e.running)
  const offlineEmulators = emulators.filter(e => !e.running)

  // 自动加载模拟器列表
  useEffect(() => {
    loadEmulators()
    const t = setInterval(loadEmulators, 5000)
    return () => clearInterval(t)
  }, [])

  async function loadEmulators() {
    try {
      const res = await fetch('/api/emulators')
      const json = await res.json()
      if (json.instances) {
        setEmulators(json.instances.map((e: Record<string, unknown>) => ({
          index: e.index as number,
          name: e.name as string,
          running: e.running as boolean,
          adbSerial: (e.adb_serial as string) || `emulator-${5554 + (e.index as number) * 2}`,
        })))
      }
    } catch {}
  }

  const handleRefresh = () => {
    setRefreshing(true)
    loadEmulators().finally(() => setRefreshing(false))
  }

  const handleTestRun = async (idx: number) => {
    setTestingIdx(idx)
    try {
      const res = await fetch(`/api/start/${idx}`, { method: 'POST' })
      const json = await res.json()
      if (json.ok) {
        setIsRunning(true)
      }
    } catch {}
    setTestingIdx(null)
  }

  return (
    <div className="space-y-6">
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
                testing={testingIdx === emulator.index}
                onTestRun={() => handleTestRun(emulator.index)}
              />
            ))}
          </div>
        </div>
      )}

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

      <div className="bg-muted/50 border border-border rounded-lg p-4 text-sm text-muted-foreground">
        点击「测试运行」单独测试一个实例的完整流程。全部准备好后，在「设置」中配置分组，再点击「启动」批量运行。
      </div>
    </div>
  )
}

function EmulatorCard({ emulator, online, testing, onTestRun }: {
  emulator: Emulator
  online: boolean
  testing?: boolean
  onTestRun?: () => void
}) {
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
          <div className="text-xs text-muted-foreground truncate">{emulator.name}</div>
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
            disabled={testing}
          >
            <Play className="w-3 h-3" />
            {testing ? '启动中...' : '测试'}
          </Button>
        )}
      </div>
    </div>
  )
}
