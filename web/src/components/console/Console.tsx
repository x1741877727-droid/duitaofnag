/**
 * 中控台 — 多选 + 分屏 + 阶段测试自动注入.
 *
 * 布局:
 *   顶 1 行 状态
 *   ↓
 *   实例总览 (多选, 可收起)
 *   ↓
 *   聚焦区:
 *     0 选 → 引导
 *     1 选 → 大图 + 右侧 Tier
 *     2-4 选 → 分屏 (每屏 mini LiveInspector + 实例号备注)
 *     >4 → 提示分屏上限
 *   ↓
 *   阶段测试 (折叠, 选了实例后自动注入)
 */
import { useState } from 'react'
import { ChevronDown, ChevronRight, EyeOff, Eye } from 'lucide-react'
import { useAppStore } from '@/lib/store'
import { InstancesOverview } from './InstancesOverview'
import { LiveInspector } from './LiveInspector'
import { RecognitionEvidence } from './RecognitionEvidence'
import { PhaseTester } from './PhaseTester'

const MAX_SPLIT = 4   // 最多 4 屏

export function Console() {
  const liveConnected = useAppStore((s) => s.liveConnected)
  const selectedInstances = useAppStore((s) => s.selectedInstances)
  const liveDecisions = useAppStore((s) => s.liveDecisions)
  const clearSel = useAppStore((s) => s.clearInstanceSelection)

  const [testerOpen, setTesterOpen] = useState(false)
  const [overviewOpen, setOverviewOpen] = useState(true)

  const liveCount = Object.keys(liveDecisions).length
  const sel = selectedInstances.slice(0, MAX_SPLIT)
  const overflow = selectedInstances.length > MAX_SPLIT

  // 分屏 grid: 1=1x1, 2=1x2, 3-4=2x2
  let gridCls = ''
  if (sel.length === 1) gridCls = 'grid grid-cols-1'
  else if (sel.length === 2) gridCls = 'grid grid-cols-2'
  else if (sel.length >= 3) gridCls = 'grid grid-cols-2 grid-rows-2'

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
        {sel.length > 0 && (
          <span className="ml-auto text-muted-foreground">
            聚焦 <span className="font-mono text-foreground font-semibold">{sel.length}</span> 实例
            <button
              onClick={() => clearSel()}
              className="ml-2 text-xs text-muted-foreground hover:text-foreground underline"
            >
              清空
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
      {sel.length === 0 ? (
        <div className="flex-1 rounded-lg border border-dashed border-border bg-card flex flex-col items-center justify-center gap-2 text-center p-8">
          <div className="text-base font-semibold text-foreground">选实例聚焦 (单选 / 多选分屏)</div>
          <div className="text-sm text-muted-foreground max-w-md">
            点上面任一实例卡 → 显示该实例的实时帧 (含 YOLO 框 / 点击点 / 黑名单点) + 5 层识别证据
          </div>
          <div className="text-xs text-muted-foreground mt-2">
            多选最多 {MAX_SPLIT} 个分屏, 每屏会标实例号
          </div>
        </div>
      ) : sel.length === 1 ? (
        // 单选: 大图 + 右侧 Tier
        <div className="flex-1 grid gap-3 min-h-0" style={{ gridTemplateColumns: '1fr 380px' }}>
          <LiveInspector instanceIdx={sel[0]} />
          <RecognitionEvidence instanceIdx={sel[0]} />
        </div>
      ) : (
        // 分屏 2-4
        <div className="flex-1 flex flex-col gap-2 min-h-0">
          <div className={`${gridCls} gap-2 flex-1 min-h-0`}>
            {sel.map((idx) => (
              <div key={idx} className="relative min-h-0 rounded-lg border border-border overflow-hidden">
                <div className="absolute top-2 left-2 z-10 px-2 py-0.5 bg-foreground/85 text-background text-xs font-mono font-bold rounded">
                  实例 #{idx}
                </div>
                <LiveInspector instanceIdx={idx} compact />
              </div>
            ))}
          </div>
          {overflow && (
            <div className="text-xs text-warning">
              ⚠ 已选 {selectedInstances.length} 实例, 分屏只显前 {MAX_SPLIT} 个 (其余仍在跑)
            </div>
          )}
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
            {sel.length > 0
              ? `(将在选中的 ${sel.length} 个实例上跑)`
              : '(选阶段 + 实例跑一次或多个串跑)'}
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
