import { useMemo, useState } from 'react'
import { useAppStore, getStageIndex, type TeamGroup } from '@/lib/store'
import { cn } from '@/lib/utils'

interface PipelineBarProps {
  filterTeams?: TeamGroup[]
}

export function PipelineBar({ filterTeams }: PipelineBarProps) {
  const { instances, settings, runningDuration } = useAppStore()
  const stages = settings.pipelineStages
  const [hoveredStage, setHoveredStage] = useState<string | null>(null)

  const filtered = Object.values(instances).filter(i =>
    !filterTeams || filterTeams.includes(i.group)
  )

  const { stageCounts, stageMaxTimes, totalInstances, avgProgress } = useMemo(() => {
    const counts = stages.map((stage, stageIdx) => {
      let inProgress = 0
      let completed = 0
      filtered.forEach(instance => {
        if (instance.state === 'error' || instance.state === 'init') return
        const instanceStageIdx = getStageIndex(instance.state, stages)
        if (stage.states.includes(instance.state)) inProgress++
        else if (instanceStageIdx > stageIdx || instance.state === 'done') completed++
      })
      return { inProgress, completed }
    })

    const maxTimes = stages.map(stage => {
      let maxTime = 0
      filtered.forEach(inst => {
        if (!inst.stageTimes) return
        for (const stateKey of stage.states) {
          const t = inst.stageTimes[stateKey]
          if (t !== undefined && t > maxTime) maxTime = t
        }
      })
      return maxTime
    })

    const total = filtered.filter(i => i.state !== 'error' && i.state !== 'init').length
    let progress = 0
    filtered.forEach(instance => {
      if (instance.state === 'error' || instance.state === 'init') return
      const idx = getStageIndex(instance.state, stages)
      if (idx >= 0) progress += (idx + 1) / stages.length
      else if (instance.state === 'done') progress += 1
    })

    return { stageCounts: counts, stageMaxTimes: maxTimes, totalInstances: total, avgProgress: total > 0 ? (progress / total) * 100 : 0 }
  }, [filtered, stages])

  const formatTime = (s: number) => {
    if (s <= 0) return ''
    if (s < 60) return `${s.toFixed(1)}s`
    const m = Math.floor(s / 60)
    const sec = Math.round(s % 60)
    return `${m}m${sec}s`
  }

  return (
    <div className="rounded-lg bg-card border border-border p-3.5">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-foreground">流水线进度</h3>
        <div className="flex items-center gap-3 text-sm text-muted-foreground">
          <span>{totalInstances} 个实例运行中</span>
          {runningDuration > 0 && (
            <span className="font-mono text-xs bg-secondary px-2 py-0.5 rounded">
              总耗时 {formatTime(runningDuration)}
            </span>
          )}
        </div>
      </div>

      {/* 进度条 */}
      <div className="relative h-1.5 bg-secondary rounded-full mb-3 overflow-hidden">
        <div
          className="absolute inset-y-0 left-0 bg-primary rounded-full transition-all duration-500"
          style={{ width: `${avgProgress}%` }}
        />
      </div>

      {/* 阶段标签 */}
      <div className="flex flex-wrap gap-2">
        {stages.map((stage, idx) => {
          const { inProgress, completed } = stageCounts[idx]
          const maxTime = stageMaxTimes[idx]
          const isActive = inProgress > 0
          const allPassed = completed === totalInstances && totalInstances > 0
          const isHovered = hoveredStage === stage.key

          return (
            <div
              key={stage.key}
              className="relative"
              onMouseEnter={() => setHoveredStage(stage.key)}
              onMouseLeave={() => setHoveredStage(null)}
            >
              <div
                className={cn(
                  'px-3 py-1.5 rounded-md text-sm font-medium transition-colors cursor-default',
                  allPassed && 'bg-success/10 text-success',
                  isActive && !allPassed && 'bg-info/10 text-info',
                  !isActive && !allPassed && 'bg-secondary text-muted-foreground',
                )}
              >
                {stage.key === 'team' && <span className="text-[10px] opacity-60 mr-0.5">⑂</span>}
                {stage.label}
                {(isActive || allPassed) && (
                  <span className="ml-1.5 opacity-70">
                    {allPassed ? completed : inProgress}/{totalInstances || filtered.length}
                  </span>
                )}
              </div>

              {/* Hover tooltip */}
              {isHovered && (maxTime > 0 || (stage.key === 'team' && filtered.length > 0)) && (
                <div className="absolute top-full left-1/2 -translate-x-1/2 mt-1.5 z-50 animate-fade">
                  <div className="bg-card border border-border rounded-lg shadow-lg px-3 py-2 min-w-[140px]">
                    {/* 耗时 */}
                    {maxTime > 0 && (
                      <div className="text-xs text-muted-foreground mb-1">
                        耗时 <span className="font-mono text-foreground">{formatTime(maxTime)}</span>
                      </div>
                    )}

                    {/* 组队分支详情 */}
                    {stage.key === 'team' && (
                      <div className="space-y-1.5 mt-1">
                        {/* 迷你分支图 */}
                        <div className="text-[10px] text-muted-foreground/70 flex items-center gap-1">
                          <span>大厅</span>
                          <span className="text-muted-foreground/40">─┬─</span>
                          <span>创建</span>
                        </div>
                        <div className="text-[10px] text-muted-foreground/70 flex items-center gap-1 pl-[26px]">
                          <span className="text-muted-foreground/40">└─</span>
                          <span>加入</span>
                        </div>

                        {/* 实例详情 */}
                        {filtered.map(inst => {
                          if (inst.state === 'init') return null
                          const isCreating = inst.state === 'team_create'
                          const isJoining = inst.state === 'team_join'
                          const createTime = inst.stageTimes?.team_create
                          const joinTime = inst.stageTimes?.team_join
                          const stageIdx = getStageIndex(inst.state, stages)
                          const teamStageIdx = stages.findIndex(s => s.key === 'team')
                          const passed = stageIdx > teamStageIdx || inst.state === 'done'

                          return (
                            <div key={inst.index} className="flex items-center gap-1.5 text-[11px]">
                              <span className="font-mono text-muted-foreground">#{inst.index}</span>
                              {inst.role === 'captain' && <span className="text-warning">★</span>}
                              <span className={cn(
                                passed ? 'text-success' : (isCreating || isJoining) ? 'text-info' : 'text-muted-foreground'
                              )}>
                                {inst.role === 'captain' ? '创建' : '加入'}
                              </span>
                              {createTime != null && <span className="font-mono text-muted-foreground">{createTime}s</span>}
                              {joinTime != null && <span className="font-mono text-muted-foreground">{joinTime}s</span>}
                              {passed && <span className="text-success">✓</span>}
                              {(isCreating || isJoining) && <span className="text-info animate-pulse">⟳</span>}
                            </div>
                          )
                        })}
                      </div>
                    )}

                    {/* 非组队阶段：显示各实例耗时 */}
                    {stage.key !== 'team' && maxTime > 0 && filtered.length > 1 && (
                      <div className="space-y-0.5 mt-1 border-t border-border/50 pt-1">
                        {filtered.map(inst => {
                          const t = stage.states.reduce((max, s) => {
                            const v = inst.stageTimes?.[s]
                            return v != null && v > max ? v : max
                          }, 0)
                          if (t <= 0) return null
                          return (
                            <div key={inst.index} className="flex items-center gap-1.5 text-[11px]">
                              <span className="font-mono text-muted-foreground">#{inst.index}</span>
                              <span className="font-mono">{t}s</span>
                            </div>
                          )
                        })}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
