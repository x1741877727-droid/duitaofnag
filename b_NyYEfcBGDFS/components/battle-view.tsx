'use client'

import { PipelineBar } from './pipeline-bar'
import { TeamPanel } from './team-panel'

export function BattleView() {
  return (
    <div className="space-y-6">
      {/* 全局流水线进度 */}
      <PipelineBar />

      {/* 两队面板 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <TeamPanel team="A" />
        <TeamPanel team="B" />
      </div>
    </div>
  )
}
