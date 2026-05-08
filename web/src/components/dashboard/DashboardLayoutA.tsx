import { useEffect, useMemo, useState } from 'react'
import { cn } from '@/lib/utils'
import {
  useAppStore,
  type Instance,
  type AccountAssignment,
} from '@/lib/store'
import { Pill } from '@/components/ui/pill'
import { ExceptionBanner } from './ExceptionBanner'
import { ExceptionToast } from './ExceptionToast'
import {
  FilterTabs,
  filterInstances,
  makeFilterCounts,
  type ViewFilter,
} from './FilterTabs'
import { InstanceCard, type InstanceCardData } from './InstanceCard'
import { DetailBody } from './DetailBody'
import { ZeroState } from './ZeroState'
import { StandbyState } from './StandbyState'
import { KbdHint } from './KbdHint'

const MAX_SELECT = 4

/**
 * 派生上次决策时间字符串. 来自 liveDecisions[idx][0].ts.
 */
function decisionAgo(now: number, ts?: number): string | undefined {
  if (!ts) return undefined
  const sec = Math.max(0, Math.floor((now - ts) / 1000))
  if (sec < 5) return '刚刚'
  if (sec < 60) return `${sec}s 前`
  const min = Math.floor(sec / 60)
  return `${min} 分钟前`
}

function buildCardData(
  insts: Instance[],
  decisions: Record<number, { ts: number }[] | undefined>,
  now: number,
): InstanceCardData[] {
  return insts.map((i) => {
    const lastTs = decisions[i.index]?.[0]?.ts
    return {
      ...i,
      decisionAgo: decisionAgo(now, lastTs),
      phaseSec: Math.round(i.stateDuration ?? 0),
      captain: i.role === 'captain',
    }
  })
}

/**
 * DashboardLayoutA — 中控台 Layout A · Wall + Drawer.
 * 来源: layouts.jsx LayoutA.
 *
 * 状态机:
 *   - accounts.length === 0          → ZeroState
 *   - accounts > 0 && !isRunning      → StandbyState
 *   - isRunning && error count > 0    → 监控墙 + ExceptionBanner + Toast
 *   - isRunning && no error           → 监控墙
 */
export function DashboardLayoutA({
  onStart,
  onStop,
}: {
  onStart: () => void
  onStop: () => void
}) {
  const isRunning = useAppStore((s) => s.isRunning)
  const accounts = useAppStore((s) => s.accounts)
  const setCurrentView = useAppStore((s) => s.setCurrentView)
  const instancesRecord = useAppStore((s) => s.instances)
  const liveDecisions = useAppStore((s) => s.liveDecisions)
  const selectedInstances = useAppStore((s) => s.selectedInstances)
  const toggleInstanceSelection = useAppStore(
    (s) => s.toggleInstanceSelection,
  )
  const clearInstanceSelection = useAppStore((s) => s.clearInstanceSelection)
  const setSelectedInstances = useAppStore((s) => s.setSelectedInstances)

  const instances = useMemo<Instance[]>(
    () =>
      Object.values(instancesRecord).sort((a, b) => a.index - b.index),
    [instancesRecord],
  )

  const [viewFilter, setViewFilter] = useState<ViewFilter>('all')
  const [errorOnly, setErrorOnly] = useState(false)

  // tick for decisionAgo refresh
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!isRunning) return
    const id = setInterval(() => setNow(Date.now()), 5000)
    return () => clearInterval(id)
  }, [isRunning])

  // keyboard shortcuts
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const meta = e.metaKey || e.ctrlKey
      if (e.key === 'Escape') {
        clearInstanceSelection()
      } else if (meta && e.key === '0') {
        setViewFilter('all')
        setErrorOnly(false)
        e.preventDefault()
      } else if (meta && e.key === '1') {
        setViewFilter('g1')
        setErrorOnly(false)
        e.preventDefault()
      } else if (meta && e.key === '2') {
        setViewFilter('g2')
        setErrorOnly(false)
        e.preventDefault()
      } else if (meta && e.key === '3') {
        setViewFilter('g3')
        setErrorOnly(false)
        e.preventDefault()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [clearInstanceSelection])

  // ── ZeroState ──
  if (accounts.length === 0) {
    return (
      <ZeroState
        onAddAccount={() => setCurrentView('settings')}
        onDocs={() => window.open('/docs/QUICK_START.md', '_blank')}
      />
    )
  }

  // ── StandbyState ──
  if (!isRunning) {
    return (
      <StandbyState
        accounts={accounts as AccountAssignment[]}
        onStart={onStart}
        onSettings={() => setCurrentView('settings')}
      />
    )
  }

  // ── Running ──
  const errors = instances.filter((i) => i.state === 'error')
  const counts = makeFilterCounts(instances)
  const visible = filterInstances(instances, viewFilter, errorOnly)
  const cards = buildCardData(visible, liveDecisions, now)
  const selectedSet = new Set(selectedInstances)
  const selectedInsts = selectedInstances
    .map((idx) => instancesRecord[String(idx)])
    .filter((i): i is Instance => Boolean(i))
  const drawerOpen = selectedInsts.length > 0

  const onCardClick = (idx: number) => {
    if (selectedSet.has(idx)) {
      toggleInstanceSelection(idx)
      return
    }
    if (selectedInstances.length >= MAX_SELECT) {
      // overflow — keep newest 4
      setSelectedInstances([
        ...selectedInstances.slice(1, MAX_SELECT),
        idx,
      ])
      return
    }
    toggleInstanceSelection(idx)
  }

  return (
    <div className="flex flex-col h-full bg-background overflow-hidden relative">
      {errors.length > 0 && (
        <ExceptionBanner
          errors={errors}
          onView={(idx) => setSelectedInstances([idx])}
          onRestart={() => {
            // TODO: POST /api/start/{idx}
          }}
        />
      )}

      <div className="flex-1 flex min-h-0">
        {/* 主墙 */}
        <div className="flex-1 flex flex-col min-w-0">
          {/* sub-bar */}
          <div className="flex items-center gap-3.5 px-6 py-3.5 bg-card border-b border-border">
            <FilterTabs
              value={viewFilter}
              onChange={setViewFilter}
              errorOnly={errorOnly}
              onErrorOnly={setErrorOnly}
              errorCount={errors.length}
              counts={counts}
            />
            <span className="flex-1" />
            <span className="text-[11px] text-fainter">
              <span className="gb-mono text-muted-foreground">
                {visible.length}
              </span>
              {' / '}
              <span className="gb-mono">{instances.length}</span>
              {' 台显示中'}
            </span>
            {selectedInsts.length > 0 && (
              <Pill tone="accent">已选 {selectedInsts.length} / {MAX_SELECT}</Pill>
            )}
          </div>

          {/* grid */}
          <div className="flex-1 overflow-y-auto px-6 py-5 bg-background">
            <div
              className={cn('grid gap-4')}
              style={{
                gridTemplateColumns: drawerOpen
                  ? 'repeat(auto-fill, minmax(260px, 1fr))'
                  : 'repeat(auto-fill, minmax(300px, 1fr))',
              }}
            >
              {cards.map((c) => (
                <InstanceCard
                  key={c.index}
                  inst={c}
                  selected={selectedSet.has(c.index)}
                  onClick={() => onCardClick(c.index)}
                />
              ))}
              {cards.length === 0 && (
                <div className="col-span-full p-10 text-center text-fainter text-[12px]">
                  当前筛选下没有实例
                </div>
              )}
            </div>
          </div>
        </div>

        {/* drawer */}
        <aside
          className={cn(
            'flex-none bg-card overflow-hidden',
            drawerOpen ? 'border-l border-border' : 'border-l-0',
          )}
          style={{
            width: drawerOpen ? 460 : 0,
            transition: 'width .25s ease',
          }}
        >
          <DetailBody
            insts={selectedInsts}
            onClose={clearInstanceSelection}
          />
        </aside>
      </div>

      {errors.length > 0 && (
        <ExceptionToast
          errors={errors}
          onView={(idx) => setSelectedInstances([idx])}
          onRestart={() => {
            // TODO: POST /api/start/{idx}
          }}
        />
      )}

      <KbdHint />
      {/* onStop reference for future emergency menu wire */}
      <span hidden onClick={onStop} />
    </div>
  )
}
