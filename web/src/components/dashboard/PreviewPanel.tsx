/**
 * PreviewPanel + StandbyTile — 待启动态右侧实例预览.
 * 完全照搬 /tmp/design_dl/gameautomation/project/states.jsx
 *   PreviewPanel (1024-1075)
 *   StandbyTile  (1077-1116)
 */

import { useState } from 'react'
import { C } from '@/lib/design-tokens'
import type { Instance, TeamGroup } from '@/lib/store'

type ViewFilter = 'all' | 'g1' | 'g2' | 'g3'

const SQUAD_TEAMS: Record<'g1' | 'g2' | 'g3', TeamGroup[]> = {
  g1: ['A', 'B'],
  g2: ['C', 'D'],
  g3: ['E', 'F'],
}

function makeCounts(insts: Instance[]) {
  const inGroups = (gs: TeamGroup[]) =>
    insts.filter((i) => gs.includes(i.group)).length
  return {
    all: insts.length,
    g1: inGroups(SQUAD_TEAMS.g1),
    g2: inGroups(SQUAD_TEAMS.g2),
    g3: inGroups(SQUAD_TEAMS.g3),
  }
}

function filterInstances(
  insts: Instance[],
  view: ViewFilter,
  errorOnly: boolean,
): Instance[] {
  let list = insts
  if (view !== 'all') list = list.filter((i) => SQUAD_TEAMS[view].includes(i.group))
  if (errorOnly) list = list.filter((i) => i.state === 'error')
  return list
}

export function PreviewPanel({
  instances,
  testerSel,
  onToggleTester,
}: {
  instances: Instance[]
  /** dev mode 时传 selected idx 数组; 非 dev 传 null. */
  testerSel: number[] | null
  onToggleTester: ((idx: number) => void) | null
}) {
  const [viewFilter, setViewFilter] = useState<ViewFilter>('all')
  const [errorOnly, setErrorOnly] = useState(false)
  const counts = makeCounts(instances)
  const filtered = filterInstances(instances, viewFilter, errorOnly)
  const isTester = testerSel != null

  return (
    <div
      style={{
        background: C.surface,
        borderRadius: 14,
        border: `1px solid ${C.border}`,
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
        boxShadow: '0 1px 0 rgba(0,0,0,.02)',
      }}
    >
      {/* header */}
      <div
        style={{
          padding: '14px 16px',
          borderBottom: `1px solid ${C.borderSoft}`,
          display: 'flex',
          alignItems: 'center',
          gap: 12,
        }}
      >
        <span style={{ fontSize: 13, fontWeight: 600, color: C.ink }}>
          实例预览
        </span>
        <span style={{ fontSize: 11.5, color: C.ink3 }}>
          {filtered.length} / {instances.length} 显示中
          {isTester && (
            <span style={{ color: '#a16207', marginLeft: 8 }}>
              · 测试将在{' '}
              <b className="gb-mono" style={{ fontWeight: 600 }}>
                {testerSel!.length}
              </b>{' '}
              台运行
            </span>
          )}
        </span>
        <span style={{ flex: 1 }} />
        <FilterTabsMini
          value={viewFilter}
          onChange={setViewFilter}
          errorOnly={errorOnly}
          onErrorOnly={setErrorOnly}
          errorCount={0}
          counts={counts}
        />
      </div>

      {/* tile grid */}
      <div
        className="gb-scroll"
        style={{
          flex: 1,
          padding: 12,
          overflowY: 'auto',
          display: 'grid',
          gridTemplateColumns:
            filtered.length <= 6
              ? 'repeat(auto-fit, minmax(200px, 240px))'
              : 'repeat(auto-fill, minmax(170px, 1fr))',
          gap: 8,
          alignContent: 'start',
          minHeight: 270,
        }}
      >
        {filtered.map((i) => (
          <StandbyTile
            key={i.index}
            inst={i}
            picked={isTester ? testerSel!.includes(i.index) : null}
            onPick={onToggleTester ? () => onToggleTester(i.index) : null}
          />
        ))}
        {filtered.length === 0 && (
          <div
            style={{
              gridColumn: '1 / -1',
              padding: 30,
              textAlign: 'center',
              color: C.ink4,
              fontSize: 12,
            }}
          >
            当前筛选下没有实例
          </div>
        )}
      </div>
    </div>
  )
}

function StandbyTile({
  inst,
  picked,
  onPick,
}: {
  inst: Instance
  picked: boolean | null
  onPick: (() => void) | null
}) {
  const interactive = !!onPick
  const captain = inst.role === 'captain'
  return (
    <div
      onClick={onPick ?? undefined}
      style={{
        border: `1px solid ${picked ? C.ink : C.border}`,
        borderRadius: 8,
        background: picked ? '#f4f1ea' : C.surface,
        padding: '10px 11px',
        display: 'flex',
        flexDirection: 'column',
        gap: 5,
        cursor: interactive ? 'pointer' : 'default',
        boxShadow: picked ? `0 0 0 1px ${C.ink}` : 'none',
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}
      >
        <span
          className="gb-mono"
          style={{ fontSize: 11, fontWeight: 600, color: C.ink3 }}
        >
          #{String(inst.index).padStart(2, '0')}
        </span>
        <span
          style={{
            fontSize: 10,
            fontWeight: 600,
            padding: '1px 6px',
            borderRadius: 3,
            background: captain ? C.ink : C.surface2,
            color: captain ? '#fff' : C.ink2,
            letterSpacing: '.06em',
          }}
        >
          {inst.group}
          {captain ? '·队长' : ''}
        </span>
      </div>
      <div
        style={{
          fontSize: 12.5,
          fontWeight: 500,
          color: C.ink,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
      >
        {inst.nickname}
      </div>
      <div
        style={{
          display: 'flex',
          gap: 8,
          alignItems: 'center',
          marginTop: 1,
        }}
      >
        <span
          style={{
            display: 'inline-flex',
            gap: 3,
            fontSize: 10.5,
            color: C.live,
            fontWeight: 500,
            alignItems: 'center',
          }}
        >
          <span>✓</span>
          <span>就绪</span>
        </span>
        {interactive && picked && (
          <span
            style={{
              marginLeft: 'auto',
              fontSize: 10,
              color: C.ink,
              fontWeight: 600,
            }}
          >
            已选
          </span>
        )}
      </div>
    </div>
  )
}

// 简化版 FilterTabs (PreviewPanel 用), 跟 dashboard FilterTabs 视觉一致但
// 内嵌. 主页 dashboard FilterTabs 在 running 态用; 这是 standby 态的 mini 版.
function FilterTabsMini({
  value,
  onChange,
  errorOnly,
  onErrorOnly,
  errorCount,
  counts,
}: {
  value: ViewFilter
  onChange: (v: ViewFilter) => void
  errorOnly: boolean
  onErrorOnly: (b: boolean) => void
  errorCount: number
  counts: { all: number; g1: number; g2: number; g3: number }
}) {
  const items: Array<[ViewFilter, string, number]> = [
    ['all', '全部', counts.all],
    ['g1', '大组 1', counts.g1],
    ['g2', '大组 2', counts.g2],
    ['g3', '大组 3', counts.g3],
  ]
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      {items.map(([k, l, n]) => {
        const active = value === k && !errorOnly
        const empty = n === 0
        return (
          <button
            key={k}
            type="button"
            disabled={empty}
            onClick={() => {
              onErrorOnly(false)
              onChange(k)
            }}
            style={{
              padding: '4px 9px',
              borderRadius: 5,
              fontSize: 11.5,
              fontWeight: 500,
              border: 'none',
              cursor: empty ? 'not-allowed' : 'pointer',
              fontFamily: 'inherit',
              color: empty ? C.ink4 : active ? C.ink : C.ink3,
              background: active ? C.surface2 : 'transparent',
              display: 'inline-flex',
              alignItems: 'center',
              gap: 4,
            }}
          >
            <span>{l}</span>
            <span
              className="gb-mono"
              style={{ fontSize: 10, color: C.ink4 }}
            >
              {n}
            </span>
          </button>
        )
      })}
      <button
        type="button"
        onClick={() => onErrorOnly(!errorOnly)}
        style={{
          padding: '4px 9px',
          borderRadius: 5,
          fontSize: 11.5,
          fontWeight: 500,
          border: 'none',
          cursor: 'pointer',
          fontFamily: 'inherit',
          color: errorOnly
            ? '#fff'
            : errorCount > 0
              ? C.error
              : C.ink4,
          background: errorOnly
            ? C.error
            : errorCount > 0
              ? C.errorSoft
              : 'transparent',
          display: 'inline-flex',
          alignItems: 'center',
          gap: 4,
        }}
      >
        <span
          style={{
            width: 5,
            height: 5,
            borderRadius: '50%',
            background: errorOnly
              ? '#fff'
              : errorCount > 0
                ? C.error
                : C.ink4,
          }}
        />
        <span>异常</span>
        <span
          className="gb-mono"
          style={{ fontSize: 10, opacity: 0.8 }}
        >
          {errorCount}
        </span>
      </button>
    </div>
  )
}
