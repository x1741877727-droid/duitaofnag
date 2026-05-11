import { useEffect, useMemo, useState } from 'react'
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
  /** 仅 dev 模式显示. */
  devOnly?: boolean
}

const NAV_ITEMS: NavItem[] = [
  { key: 'console', label: '中控台' },
  { key: 'data', label: '数据' },
  { key: 'recognition', label: '识别', badge: 'dev', devOnly: true },
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
  const phaseTester = useAppStore((s) => s.phaseTester)
  const setPhaseTester = useAppStore((s) => s.setPhaseTester)
  const currentView = useAppStore((s) => s.currentView)
  const setCurrentView = useAppStore((s) => s.setCurrentView)
  const instances = useAppStore((s) => s.instances)
  const accounts = useAppStore((s) => s.accounts)
  const runningDuration = useAppStore((s) => s.runningDuration)
  const devMode = useAppStore((s) => s.devMode)
  const setDevMode = useAppStore((s) => s.setDevMode)
  const visibleNav = NAV_ITEMS.filter((n) => devMode || !n.devOnly)

  // 工作台测试 (phaseTester.busy) 也算"在跑", 让顶部按钮变"全部停止" (能终止测试)
  const testerBusy = phaseTester.busy
  const treatRunning = isRunning || testerBusy

  const total = accounts.length || Object.keys(instances).length
  const errors = Object.values(instances).filter((i) => i.state === 'error').length
  const pageState = (() => {
    if (!total && !treatRunning) return 'zero' as const
    if (!treatRunning) return 'standby' as const
    return errors > 0 ? ('running-error' as const) : ('running' as const)
  })()

  // "全部停止" 语义: 不论是 main runner / phaseTester / 两者一起跑, 都立即全部停, 然后回中控台.
  // 1) 后端: 永远打 /api/runner/cancel (无副作用); 若 main runner 在跑, 也走 onAction → /api/stop.
  // 2) 前端: bump cancelToken → PhaseTester 跑中的 promise 链会在下一个 await 比 token 不一致直接抛退出.
  // 3) 视图: 清 results / runningTargets / busy → DashboardLayoutA.phaseTesterActive=false → 回 StandbyState.
  // 4) 路由: 强制 setCurrentView('console') — 用户在数据/识别/设置页按停止也回中控台.
  const handleAction = () => {
    if (treatRunning) {
      fetch('/api/runner/cancel', { method: 'POST' }).catch(() => {})
      setPhaseTester({
        busy: false,
        progress: '已中止',
        results: [],
        runningTargets: [],
        cancelToken: (phaseTester.cancelToken || 0) + 1,
      })
      setCurrentView('console')
      if (isRunning) onAction() // /api/stop
      return
    }
    onAction() // /api/start
  }

  // tick second uptime label (cosmetic)
  const [tick, setTick] = useState(0)
  useEffect(() => {
    if (!isRunning) return
    const id = setInterval(() => setTick((t) => t + 1), 1000)
    return () => clearInterval(id)
  }, [isRunning])
  void tick // re-render every second
  const uptime = fmtUptime(runningDuration)

  // 估算: 本会话累计 phase 转换次数 / 平均 8 phase per round (保守).
  // 准确数据等阶段 6 后端 /api/session/current 接上.
  const phaseHistory = useAppStore((s) => s.phaseHistory)
  const totalTransitions = useMemo(
    () => Object.values(phaseHistory).reduce((a, arr) => a + arr.length, 0),
    [phaseHistory],
  )
  const completed = isRunning ? Math.floor(totalTransitions / 8) : 0

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
        {visibleNav.map((item) => {
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

      {/* DEV mode toggle (角标式开关, 任何状态都显示) */}
      <button
        type="button"
        onClick={() => setDevMode(!devMode)}
        title={devMode ? '关闭 DEV 模式 (隐藏识别 / PhaseTester)' : '开启 DEV 模式 (显示识别 / PhaseTester)'}
        className={cn(
          'inline-flex items-center gap-1.5 px-2 py-1 rounded-md',
          'text-[10px] font-bold uppercase border cursor-pointer transition-colors',
          devMode
            ? 'bg-[#fef3c7] text-[#92400e] border-[#fcd34d]'
            : 'bg-secondary text-fainter border-border hover:text-muted-foreground',
        )}
        style={{ letterSpacing: '.08em' }}
      >
        <span
          aria-hidden
          className={cn(
            'inline-block w-1.5 h-1.5 rounded-full',
            devMode ? 'bg-[#92400e]' : 'bg-fainter',
          )}
        />
        DEV {devMode ? 'ON' : 'OFF'}
      </button>

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

      {(treatRunning || total > 0) && (
        <>
          <div className="w-px h-[26px] bg-border mx-1" />
          <EmergencyMenu />
          <PrimaryAction
            pageState={pageState}
            total={total}
            onAction={handleAction}
            loading={loading}
          />
        </>
      )}
    </header>
  )
}
