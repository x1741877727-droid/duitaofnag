/**
 * 中控台 主视图 — Grid 布局.
 *
 * ┌────────────────────── InstancesOverview ──────────────────────┐
 * │ 顶部: 所有实例当前状态色块条                                  │
 * ├──────────┬───────────────────────┬───────────────────────────┤
 * │ Phase    │  LiveInspector        │  RecognitionEvidence      │
 * │ Timeline │  (当前帧 + 标注)      │  (5 层 Tier 卡片)         │
 * │ (聚焦    │                       │                           │
 * │  实例)   │                       │                           │
 * ├──────────┴───────────────────────┴───────────────────────────┤
 * │ ActionLog: 最近 N 帧色块条                                    │
 * └────────────────────────────────────────────────────────────────┘
 *
 * 反向控制底栏 (Day 3 加).
 */
import { useAppStore } from '@/lib/store'
import { InstancesOverview } from './InstancesOverview'
import { PhaseTimeline } from './PhaseTimeline'
import { LiveInspector } from './LiveInspector'
import { RecognitionEvidence } from './RecognitionEvidence'
import { ActionLog } from './ActionLog'

export function Console() {
  const liveConnected = useAppStore((s) => s.liveConnected)
  const focusedInstance = useAppStore((s) => s.focusedInstance)

  return (
    <div className="flex flex-col gap-3 h-full">
      {/* 实时连接状态 */}
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <span
          className={`inline-block w-2 h-2 rounded-full ${
            liveConnected ? 'bg-emerald-500 animate-pulse' : 'bg-zinc-400'
          }`}
        />
        <span>{liveConnected ? '实时数据流 已连接' : '实时数据流 未连接 (重试中…)'}</span>
        {focusedInstance !== null && (
          <span className="ml-3 text-foreground font-medium">
            · 聚焦实例 #{focusedInstance}
          </span>
        )}
      </div>

      {/* 顶部: 实例总览 */}
      <InstancesOverview />

      {/* 中间: 三栏 */}
      <div className="grid gap-3 flex-1 min-h-0"
           style={{ gridTemplateColumns: '200px 1fr 360px' }}>
        <PhaseTimeline />
        <LiveInspector />
        <RecognitionEvidence />
      </div>

      {/* 底部: 动作记录 */}
      <ActionLog />
    </div>
  )
}
