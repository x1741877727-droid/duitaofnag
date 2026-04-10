import { useState, useMemo } from 'react'
import {
  DndContext,
  DragOverlay,
  closestCenter,
  PointerSensor,
  useSensor,
  useSensors,
  type DragStartEvent,
  type DragEndEvent,
  type DragOverEvent,
} from '@dnd-kit/core'
import {
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import {
  useAppStore,
  squadTeams,
  SLOTS_PER_TEAM,
  type TeamGroup,
  type AccountAssignment,
} from '@/lib/store'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  Star,
  Users,
  GripVertical,
  MonitorSmartphone,
  Wand2,
  Save,
  Undo2,
  ChevronDown,
  ChevronRight,
  UserCircle,
} from 'lucide-react'

// 队伍颜色映射
const teamColors: Record<string, { text: string; bg: string; border: string; muted: string }> = {
  A: { text: 'text-team-a', bg: 'bg-team-a', border: 'border-team-a/30', muted: 'bg-team-a-muted' },
  B: { text: 'text-team-b', bg: 'bg-team-b', border: 'border-team-b/30', muted: 'bg-team-b-muted' },
  C: { text: 'text-[var(--color-team-c)]', bg: 'bg-[var(--color-team-c)]', border: 'border-[var(--color-team-c)]/30', muted: 'bg-[var(--color-team-c-muted)]' },
  D: { text: 'text-[var(--color-team-d)]', bg: 'bg-[var(--color-team-d)]', border: 'border-[var(--color-team-d)]/30', muted: 'bg-[var(--color-team-d-muted)]' },
  E: { text: 'text-[var(--color-team-e)]', bg: 'bg-[var(--color-team-e)]', border: 'border-[var(--color-team-e)]/30', muted: 'bg-[var(--color-team-e-muted)]' },
  F: { text: 'text-[var(--color-team-f)]', bg: 'bg-[var(--color-team-f)]', border: 'border-[var(--color-team-f)]/30', muted: 'bg-[var(--color-team-f-muted)]' },
}

function getTeamColor(team: string) {
  return teamColors[team] || teamColors.A
}

export function SquadBuilder() {
  const { accounts, setAccounts, emulators } = useAppStore()
  const [activeId, setActiveId] = useState<string | null>(null)
  const [localAccounts, setLocalAccounts] = useState<AccountAssignment[]>(accounts)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [expandedSquads, setExpandedSquads] = useState<Set<number>>(new Set([1]))

  // 同步 store → local
  useMemo(() => {
    if (!dirty) setLocalAccounts(accounts)
  }, [accounts, dirty])

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } })
  )

  // 已分配到各队的实例
  const assigned = useMemo(() => {
    const map: Record<string, AccountAssignment[]> = {}
    for (const [, teams] of Object.entries(squadTeams)) {
      for (const team of teams) {
        map[team] = localAccounts
          .filter(a => a.group === team)
          .sort((a, b) => {
            if (a.role === 'captain') return -1
            if (b.role === 'captain') return 1
            return a.index - b.index
          })
      }
    }
    return map
  }, [localAccounts])

  // 未分配的模拟器
  const unassigned = useMemo(() => {
    const assignedIndices = new Set(localAccounts.map(a => a.index))
    return emulators.filter(e => !assignedIndices.has(e.index))
  }, [localAccounts, emulators])

  const activeItem = useMemo(() => {
    if (!activeId) return null
    const idx = parseInt(activeId.replace('emu-', ''))
    return localAccounts.find(a => a.index === idx) ||
      emulators.find(e => e.index === idx)
  }, [activeId, localAccounts, emulators])

  function handleDragStart(event: DragStartEvent) {
    setActiveId(String(event.active.id))
  }

  function handleDragOver(_event: DragOverEvent) {
    // Visual feedback is handled by CSS
  }

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event
    setActiveId(null)
    if (!over) return

    const emuIndex = parseInt(String(active.id).replace('emu-', ''))
    const overId = String(over.id)

    // Drop onto a team slot (e.g., "slot-A-0")
    if (overId.startsWith('slot-')) {
      const [, team, slotStr] = overId.split('-')
      const slotIdx = parseInt(slotStr)
      const role = slotIdx === 0 ? 'captain' : 'member'

      // Check if slot is already full
      const teamAssigned = assigned[team] || []
      if (teamAssigned.length >= SLOTS_PER_TEAM) {
        // If dropping on an occupied slot, we could swap — for now just ignore
        if (!teamAssigned.find(a => a.index === emuIndex)) return
      }

      const emulator = emulators.find(e => e.index === emuIndex)
      const existing = localAccounts.find(a => a.index === emuIndex)

      let newAccounts: AccountAssignment[]

      if (existing) {
        // Moving from one team to another
        // If becoming captain, demote existing captain
        newAccounts = localAccounts.map(a => {
          if (a.index === emuIndex) {
            return { ...a, group: team as TeamGroup, role }
          }
          if (role === 'captain' && a.group === team && a.role === 'captain') {
            return { ...a, role: 'member' }
          }
          return a
        })
      } else if (emulator) {
        // New assignment from unassigned pool
        const newAccount: AccountAssignment = {
          index: emulator.index,
          name: emulator.name,
          running: emulator.running,
          adbSerial: emulator.adbSerial,
          group: team as TeamGroup,
          role,
          nickname: emulator.name,
          gameId: '',
        }
        // Demote existing captain if needed
        newAccounts = localAccounts.map(a => {
          if (role === 'captain' && a.group === team && a.role === 'captain') {
            return { ...a, role: 'member' }
          }
          return a
        })
        newAccounts.push(newAccount)
      } else {
        return
      }

      setLocalAccounts(newAccounts)
      setDirty(true)
      return
    }

    // Drop onto unassigned area
    if (overId === 'unassigned-zone') {
      setLocalAccounts(localAccounts.filter(a => a.index !== emuIndex))
      setDirty(true)
      return
    }
  }

  function handleAutoAssign() {
    const onlineEmulators = emulators.filter(e => e.running)
    const newAccounts: AccountAssignment[] = []
    let idx = 0
    for (const [, teams] of Object.entries(squadTeams)) {
      for (const team of teams) {
        for (let slot = 0; slot < SLOTS_PER_TEAM; slot++) {
          if (idx >= onlineEmulators.length) break
          const emu = onlineEmulators[idx]
          newAccounts.push({
            index: emu.index,
            name: emu.name,
            running: emu.running,
            adbSerial: emu.adbSerial,
            group: team as TeamGroup,
            role: slot === 0 ? 'captain' : 'member',
            nickname: emu.name,
            gameId: '',
          })
          idx++
        }
      }
    }
    setLocalAccounts(newAccounts)
    setDirty(true)
  }

  async function handleSave() {
    setSaving(true)
    setAccounts(localAccounts)
    await fetch('/api/accounts', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(localAccounts.map(a => ({
        qq: '', nickname: a.nickname, game_id: a.gameId,
        group: a.group, role: a.role, instance_index: a.index,
      }))),
    }).catch(() => {})
    setDirty(false)
    setSaving(false)
  }

  function handleReset() {
    setLocalAccounts(accounts)
    setDirty(false)
  }

  function toggleSquad(squadId: number) {
    const next = new Set(expandedSquads)
    if (next.has(squadId)) next.delete(squadId)
    else next.add(squadId)
    setExpandedSquads(next)
  }

  return (
    <section className="bg-card border border-border rounded-lg">
      <div className="flex items-center justify-between p-4 border-b border-border">
        <div className="flex items-center gap-2">
          <Users className="w-4 h-4 text-muted-foreground" />
          <h2 className="font-medium">大组分配</h2>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" className="gap-1.5" onClick={handleAutoAssign}>
            <Wand2 className="w-3.5 h-3.5" />
            自动分配
          </Button>
          {dirty && (
            <>
              <Button variant="outline" size="sm" className="gap-1.5" onClick={handleReset}>
                <Undo2 className="w-3.5 h-3.5" />
                撤销
              </Button>
              <Button size="sm" className="gap-1.5" onClick={handleSave} disabled={saving}>
                <Save className="w-3.5 h-3.5" />
                {saving ? '保存中...' : '保存'}
              </Button>
            </>
          )}
        </div>
      </div>

      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragStart={handleDragStart}
        onDragOver={handleDragOver}
        onDragEnd={handleDragEnd}
      >
        <div className="p-4 space-y-3">
          {/* 大组面板 */}
          {Object.entries(squadTeams).map(([squadIdStr, teams]) => {
            const squadId = parseInt(squadIdStr)
            const squadAssigned = teams.flatMap(t => assigned[t] || [])
            const isExpanded = expandedSquads.has(squadId)
            const hasInstances = squadAssigned.length > 0

            return (
              <div
                key={squadId}
                className={cn(
                  'rounded-lg border transition-colors',
                  hasInstances ? 'border-border' : 'border-dashed border-border/60'
                )}
              >
                {/* 大组标题 */}
                <button
                  className="w-full flex items-center gap-3 px-4 py-3 hover:bg-muted/30 transition-colors rounded-t-lg"
                  onClick={() => toggleSquad(squadId)}
                >
                  {isExpanded ? (
                    <ChevronDown className="w-4 h-4 text-muted-foreground" />
                  ) : (
                    <ChevronRight className="w-4 h-4 text-muted-foreground" />
                  )}
                  <span className="font-medium text-sm">大组 {squadId}</span>
                  <span className="text-xs text-muted-foreground">
                    {teams[0]}队 + {teams[1]}队
                  </span>
                  <span className="ml-auto text-xs text-muted-foreground">
                    {squadAssigned.length} / {SLOTS_PER_TEAM * 2} 个实例
                  </span>
                </button>

                {/* 展开内容 */}
                {isExpanded && (
                  <div className="px-4 pb-4 animate-expand">
                    <div className="grid grid-cols-2 gap-4">
                      {teams.map(team => (
                        <TeamDropZone
                          key={team}
                          team={team}
                          assigned={assigned[team] || []}
                          activeId={activeId}
                        />
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )
          })}

          {/* 未分配模拟器池 */}
          <UnassignedPool
            emulators={unassigned}
            activeId={activeId}
          />
        </div>

        {/* 拖拽跟随 */}
        <DragOverlay dropAnimation={{ duration: 200, easing: 'ease' }}>
          {activeItem ? (
            <EmulatorChip
              index={'index' in activeItem ? activeItem.index : 0}
              name={'name' in activeItem ? activeItem.name : ''}
              running={'running' in activeItem ? activeItem.running : false}
              isDragOverlay
            />
          ) : null}
        </DragOverlay>
      </DndContext>

      {/* 未保存提示 */}
      {dirty && (
        <div className="border-t border-warning/30 bg-warning/5 px-4 py-2.5 flex items-center justify-between rounded-b-lg animate-fade">
          <span className="text-xs text-warning font-medium">有未保存的更改</span>
          <div className="flex gap-2">
            <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={handleReset}>
              放弃
            </Button>
            <Button size="sm" className="h-7 text-xs" onClick={handleSave} disabled={saving}>
              保存更改
            </Button>
          </div>
        </div>
      )}
    </section>
  )
}

// ── Team Drop Zone ──

function TeamDropZone({
  team,
  assigned,
  activeId,
}: {
  team: TeamGroup
  assigned: AccountAssignment[]
  activeId: string | null
}) {
  const colors = getTeamColor(team)
  const slots = Array.from({ length: SLOTS_PER_TEAM }, (_, i) => ({
    id: `slot-${team}-${i}`,
    label: i === 0 ? '队长' : `队员 ${i}`,
    isCaptain: i === 0,
    account: assigned[i] || null,
  }))

  return (
    <div className={cn('rounded-lg border p-3 space-y-2', colors.border)}>
      {/* 队伍标题 */}
      <div className="flex items-center gap-2 mb-1">
        <div className={cn('w-1 h-4 rounded-full', colors.bg)} />
        <span className={cn('text-sm font-semibold', colors.text)}>{team} 队</span>
        <span className="text-xs text-muted-foreground">{assigned.length}/{SLOTS_PER_TEAM}</span>
      </div>

      {/* Bot 槽位 */}
      <SortableContext items={slots.map(s => s.id)} strategy={verticalListSortingStrategy}>
        {slots.map(slot => (
          <SlotDropZone
            key={slot.id}
            id={slot.id}
            label={slot.label}
            isCaptain={slot.isCaptain}
            account={slot.account}
            team={team}
            isActive={!!activeId}
          />
        ))}
      </SortableContext>

      {/* 真人占位 */}
      <div className="border-t border-dashed border-border/50 pt-2 mt-2 space-y-1.5">
        {[1, 2].map(i => (
          <div key={i} className="flex items-center gap-2 px-2 py-1.5 rounded-md bg-muted/30">
            <UserCircle className="w-4 h-4 text-muted-foreground/50" />
            <span className="text-xs text-muted-foreground/70">真人位 {i}</span>
            <span className="text-xs text-muted-foreground/40 ml-auto">待接入</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Single Slot ──

function SlotDropZone({
  id,
  label,
  isCaptain,
  account,
  team,
  isActive,
}: {
  id: string
  label: string
  isCaptain: boolean
  account: AccountAssignment | null
  team: TeamGroup
  isActive: boolean
}) {
  const { setNodeRef, isOver } = useSortable({ id })

  return (
    <div
      ref={setNodeRef}
      className={cn(
        'rounded-md border transition-all duration-200 min-h-[42px]',
        account
          ? 'border-border bg-secondary/40'
          : 'border-dashed border-border/50 bg-muted/20',
        isOver && 'border-primary/50 bg-primary/5 ring-1 ring-primary/20',
        isActive && !account && !isOver && 'border-border/70 bg-muted/10',
      )}
    >
      {account ? (
        <DraggableEmulator account={account} isCaptain={isCaptain} />
      ) : (
        <div className="flex items-center gap-2 px-3 py-2">
          {isCaptain && <Star className="w-3.5 h-3.5 text-warning/40" />}
          <span className="text-xs text-muted-foreground/50">
            {isActive ? '拖放到此处' : label}
          </span>
        </div>
      )}
    </div>
  )
}

// ── Draggable Emulator in slot ──

function DraggableEmulator({
  account,
  isCaptain,
}: {
  account: AccountAssignment
  isCaptain: boolean
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: `emu-${account.index}` })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  }

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={cn(
        'flex items-center gap-2 px-2 py-1.5 rounded-md cursor-grab active:cursor-grabbing',
        isDragging && 'opacity-30',
      )}
      {...attributes}
      {...listeners}
    >
      <GripVertical className="w-3.5 h-3.5 text-muted-foreground/40 shrink-0" />
      {isCaptain && <Star className="w-3.5 h-3.5 text-warning shrink-0" />}
      <MonitorSmartphone className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
      <span className="font-mono text-xs text-muted-foreground">#{account.index}</span>
      <span className="text-xs truncate flex-1">{account.nickname}</span>
      <div className={cn(
        'w-1.5 h-1.5 rounded-full shrink-0',
        account.running ? 'bg-success' : 'bg-muted-foreground/40',
      )} />
    </div>
  )
}

// ── Unassigned Pool ──

function UnassignedPool({
  emulators,
  activeId,
}: {
  emulators: { index: number; name: string; running: boolean; adbSerial: string }[]
  activeId: string | null
}) {
  const { setNodeRef, isOver } = useSortable({ id: 'unassigned-zone' })

  return (
    <div
      ref={setNodeRef}
      className={cn(
        'rounded-lg border border-dashed p-3 transition-colors',
        isOver
          ? 'border-destructive/40 bg-destructive/5'
          : 'border-border/60',
      )}
    >
      <div className="flex items-center gap-2 mb-2">
        <MonitorSmartphone className="w-4 h-4 text-muted-foreground" />
        <span className="text-sm font-medium text-muted-foreground">未分配</span>
        <span className="text-xs text-muted-foreground/70">{emulators.length} 个模拟器</span>
      </div>

      {emulators.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          <SortableContext items={emulators.map(e => `emu-${e.index}`)} strategy={verticalListSortingStrategy}>
            {emulators.map(emu => (
              <UnassignedChip key={emu.index} index={emu.index} name={emu.name} running={emu.running} />
            ))}
          </SortableContext>
        </div>
      ) : (
        <div className="text-xs text-muted-foreground/50 text-center py-2">
          所有模拟器已分配
        </div>
      )}
    </div>
  )
}

function UnassignedChip({
  index,
  name,
  running,
}: {
  index: number
  name: string
  running: boolean
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: `emu-${index}` })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  }

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={cn(
        'flex items-center gap-1.5 px-2.5 py-1.5 rounded-md border border-border bg-card',
        'cursor-grab active:cursor-grabbing hover:shadow-sm transition-shadow',
        isDragging && 'opacity-30',
      )}
      {...attributes}
      {...listeners}
    >
      <MonitorSmartphone className="w-3.5 h-3.5 text-muted-foreground" />
      <span className="font-mono text-xs">#{index}</span>
      <span className="text-xs truncate max-w-20">{name}</span>
      <div className={cn(
        'w-1.5 h-1.5 rounded-full',
        running ? 'bg-success' : 'bg-muted-foreground/40',
      )} />
    </div>
  )
}

// ── Drag Overlay Chip ──

function EmulatorChip({
  index,
  name,
  running,
  isDragOverlay,
}: {
  index: number
  name: string
  running: boolean
  isDragOverlay?: boolean
}) {
  return (
    <div
      className={cn(
        'flex items-center gap-1.5 px-3 py-2 rounded-md border bg-card shadow-lg',
        isDragOverlay && 'ring-2 ring-primary/30',
      )}
    >
      <MonitorSmartphone className="w-4 h-4 text-muted-foreground" />
      <span className="font-mono text-xs">#{index}</span>
      <span className="text-xs">{name}</span>
      <div className={cn(
        'w-2 h-2 rounded-full',
        running ? 'bg-success' : 'bg-muted-foreground/40',
      )} />
    </div>
  )
}
