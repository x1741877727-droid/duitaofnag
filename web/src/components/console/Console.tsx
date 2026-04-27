/**
 * 中控台 — 重设计 v2.
 *
 * 层次:
 *   顶 1 行 状态
 *   ↓
 *   实例总览 (紧凑卡片, 每卡含 50 帧动作色块条 + 状态)
 *   ↓
 *   聚焦区: 选了实例 → 大图 + Tier 抽屉; 不选 → 引导
 *   ↓
 *   阶段测试 (折叠, 默认收起)
 *
 * 风格: bg-card / border-border / text-foreground / text-muted-foreground
 *       状态色用 text-success / text-warning / text-destructive (跟项目 token 一致)
 */
import { useState } from 'react'
import { ChevronDown, ChevronRight, EyeOff, Eye } from 'lucide-react'
import { useAppStore } from '@/lib/store'
import { Button } from '@/components/ui/button'
import { InstancesOverview } from './InstancesOverview'
import { LiveInspector } from './LiveInspector'
import { RecognitionEvidence } from './RecognitionEvidence'
import { PhaseTester } from './PhaseTester'

export function Console() {
  const liveConnected = useAppStore((s) => s.liveConnected)
  const focusedInstance = useAppStore((s) => s.focusedInstance)
  const liveDecisions = useAppStore((s) => s.liveDecisions)
  const setFocused = useAppStore((s) => s.setFocusedInstance)

  const [testerOpen, setTesterOpen] = useState(false)
  const [overviewOpen, setOverviewOpen] = useState(true)

  // 在线实例数 = 有决策推流的实例
  const liveCount = Object.keys(liveDecisions).length

  return (
    <div className="flex flex-col gap-4 h-full min-h-0">
      {/* 顶部状态条 */}
      <div className="flex items-center gap-3 text-sm">
        <span className={`inline-flex items-center gap-1.5 ${liveConnected ? 'text-success' : 'text-muted-foreground'}`}>
          <span className={`inline-block w-2 h-2 rounded-full ${liveConnected ? 'bg-success animate-pulse' : 'bg-muted-foreground/40'}`} />
          {liveConnected ? '实时数据流 已连接' : '未连接 (重试中…)'}
        </span>
        {liveCount > 0 && (
          <span className="text-muted-foreground">
            · 在线 <span className="font-mono text-foreground font-semibold">{liveCount}</span> 实例正在决策
          </span>
        )}
        {focusedInstance !== null && (
          <span className="ml-auto text-muted-foreground">
            聚焦 <span className="font-mono text-foreground font-semibold">#{focusedInstance}</span>
            <button
              onClick={() => setFocused(null)}
              className="ml-2 text-xs text-muted-foreground hover:text-foreground underline"
            >
              取消
            </button>
          </span>
        )}
      </div>

      {/* 实例总览 (可收起) */}
      <div>
        <button
          onClick={() => setOverviewOpen((v) => !v)}
          className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground mb-1.5 transition"
        >
          {overviewOpen ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
          <span>{overviewOpen ? '收起实例总览' : '展开实例总览'}</span>
        </button>
        {overviewOpen && <InstancesOverview />}
      </div>

      {/* 聚焦区 */}
      {focusedInstance === null ? (
        <div className="flex-1 rounded-lg border border-dashed border-border bg-card flex flex-col items-center justify-center gap-2 text-center p-8">
          <div className="text-base font-semibold text-foreground">选个实例聚焦</div>
          <div className="text-sm text-muted-foreground max-w-md">
            点上面任一实例卡 → 这里显示该实例的实时帧 (含 YOLO 框 / 点击点 / 黑名单点) + 5 层识别证据
          </div>
          <div className="text-xs text-muted-foreground mt-2">
            没数据? 跑个 runner 或下面的「阶段测试」单跑一次, 这里就活了
          </div>
        </div>
      ) : (
        <div className="flex-1 grid gap-3 min-h-0" style={{ gridTemplateColumns: '1fr 380px' }}>
          <LiveInspector />
          <RecognitionEvidence />
        </div>
      )}

      {/* 阶段测试 (折叠) */}
      <div className="rounded-lg border border-border bg-card overflow-hidden">
        <button
          onClick={() => setTesterOpen((v) => !v)}
          className="w-full flex items-center gap-2 px-3 py-2 hover:bg-accent text-left text-sm font-semibold"
        >
          {testerOpen ? <ChevronDown className="w-4 h-4 text-muted-foreground" /> : <ChevronRight className="w-4 h-4 text-muted-foreground" />}
          <span>阶段测试</span>
          <span className="text-xs text-muted-foreground font-normal ml-1">
            (无需启动整套, 选阶段+实例跑一次或多个串跑)
          </span>
        </button>
        {testerOpen && (
          <div className="border-t border-border">
            <PhaseTester />
          </div>
        )}
      </div>
    </div>
  )
}
