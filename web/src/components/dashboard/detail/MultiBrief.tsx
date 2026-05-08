import type { Instance } from '@/lib/store'
import type { Emulator } from '@/lib/store'
import { DetailHeader } from './DetailHeader'
import { DetailLive } from './DetailLive'

/**
 * MultiBrief — 多选 (2-4) 时, 每屏只显示画面 + state 简要.
 * 来源: detail.jsx MultiBrief.
 */
export function MultiBrief({
  insts,
  emulators,
  onClear,
}: {
  insts: Instance[]
  emulators: Emulator[]
  onClear?: () => void
}) {
  const cols = insts.length === 1 ? '1fr' : insts.length === 2 ? '1fr 1fr' : '1fr 1fr'
  return (
    <div className="p-3">
      <div className="flex items-center justify-between mb-2.5">
        <div className="text-[12px] font-semibold text-muted-foreground">
          多选对比 · {insts.length} 台
        </div>
        {onClear && (
          <button
            type="button"
            onClick={onClear}
            className="text-[11px] text-subtle px-2 py-0.5 rounded-sm cursor-pointer border-0 bg-transparent hover:bg-secondary"
          >
            清空
          </button>
        )}
      </div>
      <div
        className="grid gap-2.5"
        style={{ gridTemplateColumns: cols }}
      >
        {insts.map((inst) => {
          const emu = emulators.find((e) => e.index === inst.index)
          return (
            <div
              key={inst.index}
              className="rounded-lg overflow-hidden border border-border bg-card"
            >
              <DetailHeader inst={inst} ldName={emu?.name} compact />
              <DetailLive inst={inst} />
            </div>
          )
        })}
      </div>
      <div className="mt-2.5 text-[11px] text-fainter text-center">
        多选模式仅显示画面 · 选 1 台查看完整证据/日志/时间线
      </div>
    </div>
  )
}
