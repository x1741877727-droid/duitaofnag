/**
 * 大组联动弹窗 — 阶段测试选了 P3a/P3b/P4 + 多实例时弹.
 *
 * 默认按「运行控制」(store.accounts) 的 group/role 配置回显.
 * 用户可点击行下拉切实例号 (a 方案: 队长/队员可换).
 * 不在任何大组的实例标红警告 (b 方案: 让用户拖进某组).
 * 不让弹窗里临时新建大组 (要新建去运行控制).
 */
import { useMemo, useState } from 'react'
import { useAppStore, type TeamGroup } from '@/lib/store'
import { Button } from '@/components/ui/button'

export interface SquadAssignment {
  group: TeamGroup
  leader: number          // 实例号
  members: number[]
}

interface Props {
  open: boolean
  selectedInstances: number[]
  selKeys: string[]
  onCancel: () => void
  onConfirm: (squads: SquadAssignment[]) => void
}

export function SquadDialog({ open, selectedInstances, selKeys, onCancel, onConfirm }: Props) {
  const accounts = useAppStore((s) => s.accounts)

  // 默认从 dashboard 读: 把已选实例按 group/role 自动分组 (回显)
  const initialSquads = useMemo<SquadAssignment[]>(() => {
    const byGroup = new Map<TeamGroup, { leader: number | null; members: number[] }>()
    for (const idx of selectedInstances) {
      const acc = accounts.find((a) => a.index === idx)
      if (!acc) continue
      const g = acc.group
      if (!byGroup.has(g)) byGroup.set(g, { leader: null, members: [] })
      const slot = byGroup.get(g)!
      if (acc.role === 'captain') {
        // 已有队长? 后来的当队员
        if (slot.leader === null) slot.leader = idx
        else slot.members.push(idx)
      } else {
        slot.members.push(idx)
      }
    }
    const out: SquadAssignment[] = []
    for (const [g, slot] of byGroup) {
      // 如果某组有队员没队长, 提升第一个队员当队长 (让组合法)
      let leader = slot.leader
      let members = slot.members
      if (leader === null && members.length > 0) {
        leader = members[0]
        members = members.slice(1)
      }
      if (leader !== null) {
        out.push({ group: g, leader, members })
      }
    }
    return out.sort((a, b) => a.group.localeCompare(b.group))
  }, [selectedInstances, accounts])

  const [squads, setSquads] = useState<SquadAssignment[]>(initialSquads)

  // 已分实例 = 所有大组里出现过的
  const assignedSet = useMemo(() => {
    const s = new Set<number>()
    for (const sq of squads) {
      s.add(sq.leader)
      for (const m of sq.members) s.add(m)
    }
    return s
  }, [squads])

  // 未分组实例 (用户多选了 dashboard 里没配过的实例)
  const unassigned = selectedInstances.filter((i) => !assignedSet.has(i))

  function moveToSquad(idx: number, group: TeamGroup, asLeader: boolean) {
    setSquads((cur) => {
      // 先从所有组里移除这个 idx
      const cleaned = cur.map((sq) => {
        const newLeader = sq.leader === idx ? null : sq.leader
        const newMembers = sq.members.filter((m) => m !== idx)
        // 如果队长被移走了, 队员里第一个顶上 (保证每组合法)
        if (newLeader === null && newMembers.length > 0) {
          return { ...sq, leader: newMembers[0], members: newMembers.slice(1) }
        }
        return { ...sq, leader: newLeader as number, members: newMembers }
      }).filter((sq) => sq.leader !== null) as SquadAssignment[]

      // 加到目标组
      const target = cleaned.find((sq) => sq.group === group)
      if (target) {
        if (asLeader) {
          // 把当前队长降为队员
          if (target.leader !== idx) {
            target.members.push(target.leader)
          }
          target.leader = idx
        } else {
          if (!target.members.includes(idx) && target.leader !== idx) {
            target.members.push(idx)
          }
        }
      }
      return cleaned
    })
  }

  function removeFromSquad(idx: number) {
    setSquads((cur) => cur.map((sq) => {
      const newLeader = sq.leader === idx ? null : sq.leader
      const newMembers = sq.members.filter((m) => m !== idx)
      if (newLeader === null && newMembers.length > 0) {
        return { ...sq, leader: newMembers[0], members: newMembers.slice(1) }
      }
      return { ...sq, leader: newLeader as number, members: newMembers }
    }).filter((sq) => sq.leader !== null) as SquadAssignment[])
  }

  if (!open) return null

  // 可用大组: 已存在的 (现有 squads + accounts 里出现过的所有 group)
  const existingGroups = new Set<TeamGroup>([
    ...squads.map((s) => s.group),
    ...accounts.map((a) => a.group),
  ])
  const groupList = Array.from(existingGroups).sort()

  const canConfirm = squads.length > 0 && unassigned.length === 0

  return (
    <div className="fixed inset-0 z-50 bg-foreground/60 flex items-center justify-center p-4"
         onClick={onCancel}>
      <div className="bg-card rounded-lg shadow-xl w-[min(640px,95vw)] max-h-[88vh] flex flex-col"
           onClick={(e) => e.stopPropagation()}>
        <div className="px-4 py-3 border-b border-border">
          <div className="font-semibold">多实例大组联动</div>
          <div className="text-[11px] text-muted-foreground mt-0.5">
            选了 P3/P4 阶段, 必须按大组联动跑. 默认按「运行控制」配置回显, 可改.
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          <div className="text-xs">
            已选 <b className="font-mono">{selectedInstances.length}</b> 实例 ·
            <b className="font-mono ml-1">{selKeys.length}</b> 阶段 ({selKeys.join(' → ')})
          </div>

          {/* 未分组警告 (b 方案) */}
          {unassigned.length > 0 && (
            <div className="rounded border border-destructive/40 bg-destructive/10 p-3 space-y-2">
              <div className="text-xs font-semibold text-destructive">
                ⚠ 这些实例不在任何大组, 必须分配后才能开始:
              </div>
              <div className="flex flex-wrap gap-2">
                {unassigned.map((idx) => (
                  <UnassignedRow
                    key={idx}
                    idx={idx}
                    groups={groupList}
                    onAssign={(g, asLeader) => moveToSquad(idx, g, asLeader)}
                  />
                ))}
              </div>
            </div>
          )}

          {/* 大组列表 */}
          {squads.length === 0 ? (
            <div className="rounded border border-dashed border-border p-6 text-center text-xs text-muted-foreground">
              当前没有大组配置 — 去「运行控制」配好账号 + 大组分组再来
            </div>
          ) : (
            squads.map((sq) => (
              <SquadCard
                key={sq.group}
                squad={sq}
                groupList={groupList}
                onChangeRole={(idx, asLeader) => moveToSquad(idx, sq.group, asLeader)}
                onMoveTo={(idx, g, asLeader) => moveToSquad(idx, g, asLeader)}
                onRemove={(idx) => removeFromSquad(idx)}
              />
            ))
          )}

          <div className="text-[11px] text-muted-foreground space-y-1 pt-1 border-t border-border">
            <div>跑法: 同组并发 P0→P1→P2 → 队长 P3a 拿 scheme → 队员 P3b 加入 → 队长 P4 选地图 (等队员都齐).</div>
            <div>不同大组之间互不干扰, 全部并发跑.</div>
            <div>要新建大组 / 给某实例配账号: 去「运行控制」.</div>
          </div>
        </div>

        <div className="px-4 py-3 border-t border-border flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={onCancel}>取消</Button>
          <Button
            size="sm"
            disabled={!canConfirm}
            className="ml-auto"
            onClick={() => onConfirm(squads)}
          >
            按此分组跑 ({squads.length} 大组)
          </Button>
        </div>
      </div>
    </div>
  )
}


function SquadCard({
  squad, groupList, onChangeRole, onMoveTo, onRemove,
}: {
  squad: SquadAssignment
  groupList: TeamGroup[]
  onChangeRole: (idx: number, asLeader: boolean) => void
  onMoveTo: (idx: number, g: TeamGroup, asLeader: boolean) => void
  onRemove: (idx: number) => void
}) {
  return (
    <div className="rounded border border-border bg-secondary/40 p-3 space-y-2">
      <div className="text-xs font-semibold text-foreground">大组 {squad.group}</div>
      <div className="space-y-1">
        <SquadRow
          idx={squad.leader} role="leader" group={squad.group} groupList={groupList}
          onMoveTo={onMoveTo} onRemove={onRemove} onChangeRole={onChangeRole}
        />
        {squad.members.map((m) => (
          <SquadRow
            key={m} idx={m} role="member" group={squad.group} groupList={groupList}
            onMoveTo={onMoveTo} onRemove={onRemove} onChangeRole={onChangeRole}
          />
        ))}
      </div>
    </div>
  )
}


function SquadRow({
  idx, role, group, groupList, onMoveTo, onRemove, onChangeRole,
}: {
  idx: number
  role: 'leader' | 'member'
  group: TeamGroup
  groupList: TeamGroup[]
  onMoveTo: (idx: number, g: TeamGroup, asLeader: boolean) => void
  onRemove: (idx: number) => void
  onChangeRole: (idx: number, asLeader: boolean) => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <div className="relative flex items-center gap-2 px-2 py-1 rounded bg-card">
      <span className="text-[11px]">{role === 'leader' ? '👑 队长' : '👤 队员'}</span>
      <span className="font-mono font-bold">#{idx}</span>
      <button
        onClick={() => setOpen(!open)}
        className="ml-auto text-[11px] text-info hover:underline"
      >
        {open ? '关' : '改'}
      </button>
      {open && (
        <div className="absolute right-2 top-full mt-1 z-10 bg-card border border-border rounded shadow-lg p-2 space-y-1 text-[11px] min-w-[160px]">
          <div className="text-muted-foreground">改角色:</div>
          <button
            onClick={() => { onChangeRole(idx, true); setOpen(false) }}
            disabled={role === 'leader'}
            className="block w-full text-left px-2 py-1 rounded hover:bg-accent disabled:opacity-50"
          >
            👑 设为队长
          </button>
          <button
            onClick={() => { onChangeRole(idx, false); setOpen(false) }}
            disabled={role === 'member'}
            className="block w-full text-left px-2 py-1 rounded hover:bg-accent disabled:opacity-50"
          >
            👤 设为队员
          </button>
          <div className="border-t border-border my-1" />
          <div className="text-muted-foreground">挪到其他大组:</div>
          {groupList.filter((g) => g !== group).map((g) => (
            <button
              key={g}
              onClick={() => { onMoveTo(idx, g, false); setOpen(false) }}
              className="block w-full text-left px-2 py-1 rounded hover:bg-accent"
            >
              → 大组 {g} (作为队员)
            </button>
          ))}
          <div className="border-t border-border my-1" />
          <button
            onClick={() => { onRemove(idx); setOpen(false) }}
            className="block w-full text-left px-2 py-1 rounded hover:bg-accent text-destructive"
          >
            从所有组移除
          </button>
        </div>
      )}
    </div>
  )
}


function UnassignedRow({
  idx, groups, onAssign,
}: {
  idx: number
  groups: TeamGroup[]
  onAssign: (g: TeamGroup, asLeader: boolean) => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="px-2 py-1 rounded bg-destructive/15 border border-destructive/40 text-destructive text-xs font-mono hover:bg-destructive/25"
      >
        #{idx} ▾
      </button>
      {open && (
        <div className="absolute left-0 top-full mt-1 z-10 bg-card border border-border rounded shadow-lg p-2 space-y-0.5 text-[11px] min-w-[160px]">
          {groups.length === 0 ? (
            <div className="text-muted-foreground px-2 py-1">没有可加入的大组</div>
          ) : (
            groups.map((g) => (
              <div key={g}>
                <button
                  onClick={() => { onAssign(g, false); setOpen(false) }}
                  className="block w-full text-left px-2 py-1 rounded hover:bg-accent"
                >
                  → 大组 {g} (队员)
                </button>
                <button
                  onClick={() => { onAssign(g, true); setOpen(false) }}
                  className="block w-full text-left px-2 py-1 rounded hover:bg-accent"
                >
                  → 大组 {g} (队长)
                </button>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  )
}
