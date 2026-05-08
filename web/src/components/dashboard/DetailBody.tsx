import { useAppStore, type Instance } from '@/lib/store'
import { DetailHeader } from './detail/DetailHeader'
import { DetailLive } from './detail/DetailLive'
import { DetailDecisions } from './detail/DetailDecisions'
import { DetailPhases } from './detail/DetailPhases'
import { EmptyDetail } from './detail/EmptyDetail'
import { MultiBrief } from './detail/MultiBrief'

/**
 * DetailBody — 抽屉内容容器, 根据选中数选择布局.
 *   0  → EmptyDetail
 *   1  → 完整 (Live + Tiers + Decisions + Phases)
 *   2-4→ MultiBrief
 *
 * 来源: layouts.jsx DetailBody.
 */
export function DetailBody({
  insts,
  onClose,
}: {
  insts: Instance[]
  onClose: () => void
}) {
  const emulators = useAppStore((s) => s.emulators)
  const liveDecisions = useAppStore((s) => s.liveDecisions)
  const phaseHistory = useAppStore((s) => s.phaseHistory)

  if (insts.length === 0) return <EmptyDetail />

  if (insts.length > 1) {
    return (
      <div className="h-full overflow-y-auto">
        <DetailHeader
          inst={insts[0]}
          ldName={emulators.find((e) => e.index === insts[0].index)?.name}
          onClose={onClose}
        />
        <MultiBrief insts={insts} emulators={emulators} onClear={onClose} />
      </div>
    )
  }

  const inst = insts[0]
  const ldName = emulators.find((e) => e.index === inst.index)?.name
  const decisions = liveDecisions[inst.index] ?? []
  const phases = phaseHistory[inst.index] ?? []

  return (
    <div className="h-full overflow-y-auto">
      <DetailHeader inst={inst} ldName={ldName} onClose={onClose} />
      <DetailLive inst={inst} />
      <DetailPhases inst={inst} history={phases} />
      <DetailDecisions decisions={decisions} />
    </div>
  )
}
