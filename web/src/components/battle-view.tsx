import { useAppStore, getSquadTeams } from '@/lib/store'
import { cn } from '@/lib/utils'
import { PipelineBar } from './pipeline-bar'
import { TeamPanel } from './team-panel'

export function BattleView() {
  const { instances, activeSquad, setActiveSquad } = useAppStore()

  // 检测有哪些大组有实例
  const allGroups = [...new Set(Object.values(instances).map(i => i.group))]
  const squads: number[] = []
  if (allGroups.some(g => g === 'A' || g === 'B')) squads.push(1)
  if (allGroups.some(g => g === 'C' || g === 'D')) squads.push(2)
  if (squads.length === 0) squads.push(1)

  const currentTeams = getSquadTeams(activeSquad)

  return (
    <div className="space-y-4">
      {/* 大组切换 + 流水线进度 */}
      <div className="flex items-center gap-3">
        {squads.length > 1 && (
          <div className="flex items-center gap-1 shrink-0">
            {squads.map(s => (
              <button
                key={s}
                onClick={() => setActiveSquad(s)}
                className={cn(
                  'px-3 py-1.5 text-xs font-medium rounded-md transition-colors',
                  activeSquad === s
                    ? 'bg-primary text-primary-foreground'
                    : 'text-muted-foreground hover:bg-secondary'
                )}
              >
                大组 {s}
              </button>
            ))}
          </div>
        )}
        <div className="flex-1">
          <PipelineBar filterTeams={currentTeams} />
        </div>
      </div>

      {/* 当前大组的两队面板 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 animate-page" key={activeSquad}>
        <TeamPanel team={currentTeams[0]} />
        <TeamPanel team={currentTeams[1]} />
      </div>
    </div>
  )
}
