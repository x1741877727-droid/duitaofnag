import { useMemo } from 'react'
import {
  useAppStore,
  squadTeams,
  getStageIndex,
  type TeamGroup,
  type Instance,
} from '@/lib/store'
import { cn } from '@/lib/utils'

interface SquadOverviewBarProps {
  onSelectSquad: (squad: number) => void
  activeSquad: number
}

export function SquadOverviewBar({ onSelectSquad, activeSquad }: SquadOverviewBarProps) {
  const { instances, settings } = useAppStore()
  const stages = settings.pipelineStages

  // 只显示有实例的大组
  const squadStats = useMemo(() => {
    const stats: {
      id: number
      teams: TeamGroup[]
      instances: Instance[]
      progress: number
      readyCount: number
      errorCount: number
      total: number
    }[] = []

    for (const [idStr, teams] of Object.entries(squadTeams)) {
      const id = parseInt(idStr)
      const squadInstances = Object.values(instances).filter(i =>
        teams.includes(i.group)
      )
      if (squadInstances.length === 0) continue

      let totalProgress = 0
      let activeCount = 0
      let readyCount = 0
      let errorCount = 0

      squadInstances.forEach(inst => {
        if (inst.state === 'error') {
          errorCount++
          return
        }
        if (inst.state === 'init') return
        activeCount++
        if (['lobby', 'done', 'ready', 'in_game'].includes(inst.state)) {
          readyCount++
          totalProgress += 1
        } else {
          const idx = getStageIndex(inst.state, stages)
          if (idx >= 0) totalProgress += (idx + 1) / stages.length
        }
      })

      stats.push({
        id,
        teams: teams as TeamGroup[],
        instances: squadInstances,
        progress: activeCount > 0 ? (totalProgress / activeCount) * 100 : 0,
        readyCount,
        errorCount,
        total: squadInstances.length,
      })
    }

    return stats
  }, [instances, stages])

  if (squadStats.length <= 1) return null

  return (
    <div className="flex gap-2">
      {squadStats.map(squad => (
        <button
          key={squad.id}
          onClick={() => onSelectSquad(squad.id)}
          className={cn(
            'flex-1 rounded-lg border px-3.5 py-2.5 text-left transition-all duration-200',
            activeSquad === squad.id
              ? 'border-primary/40 bg-primary/5 ring-1 ring-primary/15'
              : 'border-border hover:border-border/80 hover:bg-muted/30',
          )}
        >
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-semibold">大组 {squad.id}</span>
            <span className="text-xs text-muted-foreground">
              {squad.readyCount}/{squad.total}
            </span>
          </div>

          {/* 进度条 */}
          <div className="h-1.5 bg-secondary rounded-full overflow-hidden mb-2">
            <div
              className={cn(
                'h-full rounded-full transition-all duration-500',
                squad.errorCount > 0 ? 'bg-error' :
                squad.readyCount === squad.total ? 'bg-success' : 'bg-primary'
              )}
              style={{ width: `${squad.progress}%` }}
            />
          </div>

          {/* Mini 状态圆点 */}
          <div className="flex items-center gap-1">
            {squad.teams.map(team => {
              const teamInsts = squad.instances.filter(i => i.group === team)
              return (
                <div key={team} className="flex items-center gap-0.5">
                  <span className="text-[10px] text-muted-foreground mr-0.5">{team}</span>
                  {teamInsts.map(inst => (
                    <StatusDot key={inst.index} state={inst.state} />
                  ))}
                  {teamInsts.length === 0 && (
                    <span className="w-2 h-2 rounded-full bg-muted" />
                  )}
                </div>
              )
            })}
          </div>
        </button>
      ))}
    </div>
  )
}

function StatusDot({ state }: { state: string }) {
  const isReady = ['lobby', 'done', 'ready', 'in_game'].includes(state)
  const isError = state === 'error'
  const isWorking = !isReady && !isError && state !== 'init'

  return (
    <span
      className={cn(
        'w-2 h-2 rounded-full transition-colors',
        isReady && 'bg-success',
        isError && 'bg-error',
        isWorking && 'bg-info animate-pulse',
        state === 'init' && 'bg-muted-foreground/30',
      )}
    />
  )
}
