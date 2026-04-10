import { useMemo } from 'react'
import { useAppStore, getStageIndex, type TeamGroup } from '@/lib/store'
import { cn } from '@/lib/utils'

interface PipelineBarProps {
  filterTeams?: TeamGroup[]
}

export function PipelineBar({ filterTeams }: PipelineBarProps) {
  const { instances, settings, runningDuration } = useAppStore()
  const stages = settings.pipelineStages

  // 只看指定大组的实例
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
        if (stage.states.includes(instance.state)) {
          inProgress++
        } else if (instanceStageIdx > stageIdx || instance.state === 'done') {
          completed++
        }
      })

      return { inProgress, completed }
    })

    // 每个阶段的最大耗时（大组内最慢实例）
    const maxTimes = stages.map(stage => {
      let maxTime = 0
      filtered.forEach(inst => {
        if (!inst.stageTimes) return
        // 合并阶段（"组队"包含 team_create 和 team_join）
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
      if (idx >= 0) {
        progress += (idx + 1) / stages.length
      } else if (instance.state === 'done') {
        progress += 1
      }
    })
    const avg = total > 0 ? (progress / total) * 100 : 0

    return { stageCounts: counts, stageMaxTimes: maxTimes, totalInstances: total, avgProgress: avg }
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

      {/* 阶段标签 + 耗时 */}
      <div className="flex flex-wrap gap-2">
        {stages.map((stage, idx) => {
          const { inProgress, completed } = stageCounts[idx]
          const maxTime = stageMaxTimes[idx]
          const isActive = inProgress > 0
          const allPassed = completed === totalInstances && totalInstances > 0

          return (
            <div
              key={stage.key}
              className={cn(
                'px-3 py-1.5 rounded-md text-sm font-medium transition-colors relative',
                allPassed && 'bg-success/10 text-success',
                isActive && !allPassed && 'bg-info/10 text-info',
                !isActive && !allPassed && 'bg-secondary text-muted-foreground'
              )}
            >
              <div className="flex items-center gap-1.5">
                {stage.key === 'team' && <span className="text-[10px] opacity-60">⑂</span>}
                {stage.label}
                {(isActive || allPassed) && (
                  <span className="opacity-70">
                    {allPassed ? completed : inProgress}/{totalInstances || filtered.length}
                  </span>
                )}
              </div>
              {maxTime > 0 && (
                <div className="text-[10px] opacity-60 font-normal mt-0.5">
                  {formatTime(maxTime)}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
