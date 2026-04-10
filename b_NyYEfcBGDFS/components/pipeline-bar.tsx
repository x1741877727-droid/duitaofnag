'use client'

import { useAppStore, getStageIndex } from '@/lib/store'
import { cn } from '@/lib/utils'

export function PipelineBar() {
  const { instances, settings } = useAppStore()
  const stages = settings.pipelineStages

  // 计算每个阶段的实例数量
  const stageCounts = stages.map((stage, stageIdx) => {
    let inProgress = 0
    let completed = 0
    
    Object.values(instances).forEach(instance => {
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

  const totalInstances = Object.values(instances).filter(
    i => i.state !== 'error' && i.state !== 'init'
  ).length

  // 计算总体进度
  let totalProgress = 0
  Object.values(instances).forEach(instance => {
    if (instance.state === 'error' || instance.state === 'init') return
    const idx = getStageIndex(instance.state, stages)
    if (idx >= 0) {
      totalProgress += (idx + 1) / stages.length
    } else if (instance.state === 'done') {
      totalProgress += 1
    }
  })
  const avgProgress = totalInstances > 0 ? (totalProgress / totalInstances) * 100 : 0

  return (
    <div className="rounded-xl bg-card shadow-sm p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-semibold text-foreground">流水线进度</h3>
        <span className="text-sm text-muted-foreground">
          {totalInstances} 个实例运行中
        </span>
      </div>

      {/* 进度条 */}
      <div className="relative h-2 bg-secondary rounded-full mb-5 overflow-hidden">
        <div 
          className="absolute inset-y-0 left-0 bg-primary rounded-full transition-all duration-500"
          style={{ width: `${avgProgress}%` }}
        />
      </div>

      {/* 阶段标签 */}
      <div className="flex flex-wrap gap-2">
        {stages.map((stage, idx) => {
          const { inProgress, completed } = stageCounts[idx]
          const isActive = inProgress > 0
          const allPassed = completed === totalInstances && totalInstances > 0

          return (
            <div
              key={stage.key}
              className={cn(
                'px-3 py-1.5 rounded-md text-sm font-medium transition-colors',
                allPassed && 'bg-success/10 text-success',
                isActive && !allPassed && 'bg-info/10 text-info',
                !isActive && !allPassed && 'bg-secondary text-muted-foreground'
              )}
            >
              {stage.label}
              {(isActive || allPassed) && (
                <span className="ml-1.5 opacity-70">
                  {allPassed ? completed : inProgress}
                </span>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
