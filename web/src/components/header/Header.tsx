import { useEffect, useState } from 'react'
import { cn } from '@/lib/utils'
import { useAppStore, type ConsoleView } from '@/lib/store'
import { fmtUptime } from '@/lib/design-tokens'
import { GamebotMark } from './GamebotMark'
import { Stat } from './Stat'
import { EmergencyMenu } from './EmergencyMenu'
import { PrimaryAction } from './PrimaryAction'

interface NavItem {
  key: ConsoleView
  label: string
  badge?: string
}

const NAV_ITEMS: NavItem[] = [
  { key: 'console', label: '中控台' },
  { key: 'data', label: '数据' },
  { key: 'recognition', label: '识别', badge: 'dev' },
  { key: 'settings', label: '设置' },
]

/**
 * 顶部 header: 品牌 + nav (4 项) + 状态摘要 + 应急 + 主操作.
 * 来源: layouts.jsx Layout A 顶部条 + recognition-shell.jsx GlobalTabs.
 */
export function Header({
  className,
  onAction,
  loading,
}: {
  className?: string
  onAction: () => void
  loading?: boolean
}) {
  const isRunning = useAppStore((s) => s.isRunning)
  const currentView = useAppStore((s) => s.currentView)
  const setCurrentView = useAppStore((s) => s.setCurrentView)
  const instances = useAppStore((s) => s.instances)
  const accounts = useAppStore((s) => s.accounts)
  const runningDuration = useAppStore((s) => s.runningDuration)

  const total = accounts.length || Object.keys(instances).length
  const errors = Object.values(instances).filter((i) => i.state === 'error').length
  const pageState = (() => {
    if (!total && !isRunning) return 'zero' as const
    if (!isRunning) return 'standby' as const
    return errors > 0 ? ('running-error' as const) : ('running' as const)
  })()

  // tick second uptime label (cosmetic)
  const [tick, setTick] = useState(0)
  useEffect(() => {
    if (!isRunning) return
    const id = setInterval(() => setTick((t) => t + 1), 1000)
    return () => clearInterval(id)
  }, [isRunning])
  void tick // re-render every second
  const uptime = fmtUptime(runningDuration)

  const completed = 0 // TODO: wire 本会话完成局数 from session API

  return (
    <header
      className={cn(
        'flex items-center gap-4 px-6 py-3.5 bg-card border-b border-border',
        className,
      )}
    >
      <GamebotMark />
      <div className="w-px h-[22px] bg-border mx-0.5" />

      {/* nav */}
      <nav className="flex items-center gap-0.5 min-w-0">
        {NAV_ITEMS.map((item) => {
          const active = currentView === item.key
          return (
            <button
              key={item.key}
              type="button"
              onClick={() => setCurrentView(item.key)}
              className={cn(
                'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md',
                'text-[13px] font-medium leading-none cursor-pointer transition-colors border-0',
                active
                  ? 'bg-secondary text-foreground'
                  : 'text-subtle hover:text-foreground hover:bg-secondary/50 bg-transparent',
              )}
            >
              {item.label}
              {item.badge && (
                <span
                  className="text-[9px] font-semibold uppercase px-1 py-px rounded-sm gb-mono"
                  style={{
                    color: active ? 'var(--color-accent)' : 'var(--color-fainter)',
                    background: active
                      ? 'var(--color-card)'
                      : 'var(--color-secondary)',
                    letterSpacing: '.06em',
                  }}
                >
                  {item.badge}
                </span>
              )}
            </button>
          )
        })}
      </nav>

      <span className="flex-1" />

      {/* stats — 仅运行中显示 */}
      {isRunning && (
        <div className="flex items-center gap-5">
          <Stat label="完成局" value={completed} mono />
          <Stat label="已运行" value={uptime} mono />
          {errors > 0 && (
            <Stat label="异常" value={errors} mono tone="error" />
          )}
        </div>
      )}

      {(isRunning || total > 0) && (
        <>
          <div className="w-px h-[26px] bg-border mx-1" />
          <EmergencyMenu />
          <PrimaryAction
            pageState={pageState}
            total={total}
            onAction={onAction}
            loading={loading}
          />
        </>
      )}
    </header>
  )
}
