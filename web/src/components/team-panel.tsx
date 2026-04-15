

import { useMemo, useState } from 'react'
import { useAppStore, stateConfig, getStageIndex, type Instance, type TeamGroup } from '@/lib/store'
import { cn } from '@/lib/utils'
import { ChevronDown, ChevronUp, AlertCircle, CheckCircle2, Star, User } from 'lucide-react'

interface TeamPanelProps {
  team: TeamGroup
}

export function TeamPanel({ team }: TeamPanelProps) {
  const { instances, settings } = useAppStore()
  const stages = settings.pipelineStages

  const teamInstances = useMemo(() => {
    return Object.values(instances)
      .filter(i => i.group === team)
      .sort((a, b) => {
        if (a.role === 'captain') return -1
        if (b.role === 'captain') return 1
        return a.index - b.index
      })
  }, [instances, team])

  const captain = teamInstances.find(i => i.role === 'captain')
  const members = teamInstances.filter(i => i.role === 'member')
  
  const isAllReady = teamInstances.every(i => 
    ['lobby', 'done', 'ready', 'in_game'].includes(i.state)
  )
  const hasError = teamInstances.some(i => i.state === 'error')

  const teamColorMap: Record<string, string> = {
    A: 'text-team-a', B: 'text-team-b',
    C: 'text-[var(--color-team-c)]', D: 'text-[var(--color-team-d)]',
    E: 'text-[var(--color-team-e)]', F: 'text-[var(--color-team-f)]',
  }
  const teamBgMap: Record<string, string> = {
    A: 'bg-team-a', B: 'bg-team-b',
    C: 'bg-[var(--color-team-c)]', D: 'bg-[var(--color-team-d)]',
    E: 'bg-[var(--color-team-e)]', F: 'bg-[var(--color-team-f)]',
  }
  const teamColorClass = teamColorMap[team] || 'text-team-a'
  const teamBgClass = teamBgMap[team] || 'bg-team-a'

  return (
    <div className="rounded-lg bg-card border border-border">
      {/* 队伍标题栏 */}
      <div className="flex items-center gap-3 px-5 py-4 border-b border-border/50">
        <div className={cn('w-1 h-5 rounded-full', teamBgClass)} />
        <span className={cn('font-semibold', teamColorClass)}>{team} 队</span>
        <span className="text-sm text-muted-foreground">
          {teamInstances.length} 个实例
        </span>
        
        {isAllReady && (
          <div className="ml-auto flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-success/10 text-success text-xs font-medium">
            <CheckCircle2 className="w-3.5 h-3.5" />
            全员就绪
          </div>
        )}
        {hasError && (
          <div className="ml-auto flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-error/10 text-error text-xs font-medium">
            <AlertCircle className="w-3.5 h-3.5" />
            存在错误
          </div>
        )}
      </div>

      <div className="p-5 space-y-4">
        {/* 队长卡片 */}
        {captain && (
          <CaptainCard 
            instance={captain} 
            team={team} 
            stages={stages}
          />
        )}

        {/* 队员列表 */}
        {members.length > 0 && (
          <div className="grid grid-cols-2 gap-3">
            {members.map(member => (
              <MemberCard 
                key={member.index} 
                instance={member} 
                team={team}
                stages={stages}
              />
            ))}
          </div>
        )}

      </div>
    </div>
  )
}

function CaptainCard({ 
  instance, 
  team,
  stages 
}: { 
  instance: Instance
  team: TeamGroup
  stages: { key: string; label: string; states: string[] }[]
}) {
  const config = stateConfig[instance.state]
  const isError = instance.state === 'error'
  const isReady = ['lobby', 'done', 'ready', 'in_game'].includes(instance.state)
  const isWorking = !isError && !isReady && instance.state !== 'init'

  const currentStageIdx = getStageIndex(instance.state, stages)

  return (
    <div className={cn(
      'rounded-lg p-4',
      isError ? 'bg-error/5 ring-1 ring-error/20' : 'bg-secondary/50'
    )}>
      <div className="flex gap-4">
        {/* 截图区域 */}
        <div className="relative w-36 h-22 rounded-md bg-muted/80 flex items-center justify-center flex-shrink-0 overflow-hidden">
          <img
            src={`/api/screenshot/${instance.index}?w=320&t=${Math.floor(Date.now() / 3000)}`}
            className="w-full h-full object-cover"
            alt=""
            loading="lazy"
            onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
          />
          <div className={cn(
            'absolute top-2 right-2 w-2.5 h-2.5 rounded-full ring-2 ring-white',
            isError ? 'bg-error' : isWorking ? 'bg-info' : isReady ? 'bg-success' : 'bg-muted-foreground'
          )} />
        </div>

        {/* 信息区域 */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-2">
            <span className="font-mono text-xs text-muted-foreground">#{instance.index}</span>
            <div className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-warning/15 text-warning text-xs font-medium">
              <Star className="w-3 h-3" />
              队长
            </div>
            <span className="font-medium truncate">{instance.nickname}</span>
          </div>

          {/* 状态 */}
          <div className={cn(
            'text-sm font-medium mb-3',
            isError ? 'text-error' : isReady ? 'text-success' : 'text-info'
          )}>
            {config.label}
            {isError && instance.error && (
              <span className="font-normal text-error/80 ml-2">- {instance.error}</span>
            )}
          </div>

          {/* 阶段进度 */}
          <div className="flex gap-1.5">
            {stages.slice(0, 6).map((stage, idx) => {
              const isPassed = currentStageIdx > idx || isReady
              const isCurrent = stage.states.includes(instance.state)
              
              return (
                <div 
                  key={stage.key}
                  className={cn(
                    'flex-1 h-1.5 rounded-full transition-colors',
                    isPassed ? 'bg-success' : isCurrent ? 'bg-info' : 'bg-border'
                  )}
                  title={stage.label}
                />
              )
            })}
          </div>

          {/* 底部信息 */}
          <div className="flex items-center gap-2 text-xs text-muted-foreground mt-3">
            <span className="font-mono">{instance.adbSerial || `emulator-${5554 + instance.index * 2}`}</span>
            <span className="text-border">|</span>
            <span>{instance.stateDuration}s</span>
          </div>
        </div>
      </div>
    </div>
  )
}

function MemberCard({ 
  instance, 
  team,
  stages
}: { 
  instance: Instance
  team: TeamGroup
  stages: { key: string; label: string; states: string[] }[]
}) {
  const [expanded, setExpanded] = useState(false)
  const config = stateConfig[instance.state]
  const isError = instance.state === 'error'
  const isReady = ['lobby', 'done', 'ready', 'in_game'].includes(instance.state)
  const isWorking = !isError && !isReady && instance.state !== 'init'

  const currentStageIdx = getStageIndex(instance.state, stages)

  return (
    <div className={cn(
      'rounded-lg overflow-hidden transition-shadow',
      isError ? 'bg-error/5 ring-1 ring-error/20' : 'bg-secondary/50 hover:shadow-sm'
    )}>
      <div 
        className="p-3 cursor-pointer"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-2 mb-2">
          <span className="font-mono text-xs text-muted-foreground">#{instance.index}</span>
          <User className="w-3.5 h-3.5 text-muted-foreground" />
          <span className="text-sm font-medium truncate flex-1">{instance.nickname}</span>
          {expanded ? (
            <ChevronUp className="w-4 h-4 text-muted-foreground" />
          ) : (
            <ChevronDown className="w-4 h-4 text-muted-foreground" />
          )}
        </div>

        {/* 状态 */}
        <div className={cn(
          'text-xs font-medium mb-2',
          isError ? 'text-error' : isReady ? 'text-success' : 'text-info'
        )}>
          {config.label}
        </div>

        {/* 进度条 */}
        <div className="flex gap-1">
          {stages.slice(0, 6).map((stage, idx) => {
            const isPassed = currentStageIdx > idx || isReady
            const isCurrent = stage.states.includes(instance.state)
            
            return (
              <div 
                key={stage.key}
                className={cn(
                  'flex-1 h-1 rounded-full transition-colors',
                  isError ? 'bg-error/30' : isPassed ? 'bg-success' : isCurrent ? 'bg-info' : 'bg-border'
                )}
              />
            )
          })}
        </div>
      </div>

      {/* 展开详情 */}
      {expanded && (
        <div className="px-3 pb-3 pt-2 border-t border-border/30 animate-expand">
          <img
            src={`/api/screenshot/${instance.index}?t=${Date.now()}`}
            className="w-full rounded-md bg-muted/80"
            alt=""
            onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
          />
          {isError && instance.error && (
            <div className="mt-2 p-2 rounded-md bg-error/10 text-error text-xs">
              {instance.error}
            </div>
          )}
          <div className="mt-2 text-xs text-muted-foreground">
            {instance.adbSerial || `emulator-${5554 + instance.index * 2}`} | {instance.stateDuration}s
          </div>
        </div>
      )}
    </div>
  )
}

