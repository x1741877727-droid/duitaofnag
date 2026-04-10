import { useAppStore, getSquadTeams, squadTeams } from '@/lib/store'
import { PipelineBar } from './pipeline-bar'
import { TeamPanel } from './team-panel'
import { SquadOverviewBar } from './squad-overview-bar'

export function BattleView() {
  const { instances, activeSquad, setActiveSquad } = useAppStore()

  // 检测有哪些大组有实例
  const allGroups = [...new Set(Object.values(instances).map(i => i.group))]
  const squads: number[] = []
  for (const [idStr, teams] of Object.entries(squadTeams)) {
    if (teams.some(t => allGroups.includes(t))) squads.push(parseInt(idStr))
  }
  if (squads.length === 0) squads.push(1)

  const currentTeams = getSquadTeams(activeSquad)

  return (
    <div className="space-y-4">
      {/* 大组总览条（多大组时显示） */}
      <SquadOverviewBar
        activeSquad={activeSquad}
        onSelectSquad={setActiveSquad}
      />

      {/* 流水线进度 */}
      <PipelineBar filterTeams={currentTeams} />

      {/* 当前大组的两队面板 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 animate-page" key={activeSquad}>
        <TeamPanel team={currentTeams[0]} />
        <TeamPanel team={currentTeams[1]} />
      </div>
    </div>
  )
}
