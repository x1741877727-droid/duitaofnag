import { useEffect, useState, useMemo } from 'react'
import { useAppStore, squadTeams, SLOTS_PER_TEAM, type Emulator, type AccountAssignment, type TeamGroup } from '@/lib/store'
import { cn } from '@/lib/utils'
import { RefreshCw, Play, MonitorSmartphone, Server, ChevronDown, ChevronRight, AlertTriangle } from 'lucide-react'
import { Button } from '@/components/ui/button'

// 队伍颜色
const teamColorMap: Record<string, { text: string; bg: string; border: string }> = {
  A: { text: 'text-team-a', bg: 'bg-team-a', border: 'border-team-a/30' },
  B: { text: 'text-team-b', bg: 'bg-team-b', border: 'border-team-b/30' },
  C: { text: 'text-[var(--color-team-c)]', bg: 'bg-[var(--color-team-c)]', border: 'border-[var(--color-team-c)]/30' },
  D: { text: 'text-[var(--color-team-d)]', bg: 'bg-[var(--color-team-d)]', border: 'border-[var(--color-team-d)]/30' },
  E: { text: 'text-[var(--color-team-e)]', bg: 'bg-[var(--color-team-e)]', border: 'border-[var(--color-team-e)]/30' },
  F: { text: 'text-[var(--color-team-f)]', bg: 'bg-[var(--color-team-f)]', border: 'border-[var(--color-team-f)]/30' },
}

export function DeployView() {
  const { emulators, setEmulators, accounts, setIsRunning } = useAppStore()
  const [refreshing, setRefreshing] = useState(false)
  const [testingIdx, setTestingIdx] = useState<number | null>(null)
  const [expandedSquads, setExpandedSquads] = useState<Set<number>>(new Set([1, 2, 3]))
  const [startingSquad, setStartingSquad] = useState<number | null>(null)

  useEffect(() => {
    loadEmulators()
    const t = setInterval(loadEmulators, 5000)
    return () => clearInterval(t)
  }, [])

  async function loadEmulators() {
    try {
      const res = await fetch('/api/emulators')
      const json = await res.json()
      if (json.instances) {
        setEmulators(json.instances.map((e: Record<string, unknown>) => ({
          index: e.index as number,
          name: e.name as string,
          running: e.running as boolean,
          adbSerial: (e.adb_serial as string) || `emulator-${5554 + (e.index as number) * 2}`,
        })))
      }
    } catch {}
  }

  // 按大组/队伍分组
  const grouped = useMemo(() => {
    const assignedIndices = new Set(accounts.map(a => a.index))
    const result: {
      squadId: number
      teams: {
        team: TeamGroup
        online: { emulator: Emulator; account: AccountAssignment }[]
        offline: { emulator: Emulator; account: AccountAssignment }[]
      }[]
      totalOnline: number
      totalAssigned: number
    }[] = []

    for (const [idStr, teams] of Object.entries(squadTeams)) {
      const squadId = parseInt(idStr)
      const squadTeamData = teams.map(team => {
        const teamAccounts = accounts.filter(a => a.group === team)
        const online: { emulator: Emulator; account: AccountAssignment }[] = []
        const offline: { emulator: Emulator; account: AccountAssignment }[] = []

        teamAccounts.forEach(acc => {
          const emu = emulators.find(e => e.index === acc.index)
          if (emu) {
            if (emu.running) {
              online.push({ emulator: emu, account: acc })
            } else {
              offline.push({ emulator: emu, account: acc })
            }
          } else {
            // 模拟器未检测到，视为离线
            offline.push({
              emulator: { index: acc.index, name: acc.name || `模拟器-${acc.index}`, running: false, adbSerial: acc.adbSerial },
              account: acc,
            })
          }
        })

        return { team: team as TeamGroup, online, offline }
      })

      const totalAssigned = squadTeamData.reduce((s, t) => s + t.online.length + t.offline.length, 0)
      if (totalAssigned === 0) continue

      result.push({
        squadId,
        teams: squadTeamData,
        totalOnline: squadTeamData.reduce((s, t) => s + t.online.length, 0),
        totalAssigned,
      })
    }

    // 未分配的
    const unassigned = emulators.filter(e => !assignedIndices.has(e.index))

    return { squads: result, unassigned }
  }, [accounts, emulators])

  const handleRefresh = () => {
    setRefreshing(true)
    loadEmulators().finally(() => setRefreshing(false))
  }

  const handleTestRun = async (idx: number) => {
    setTestingIdx(idx)
    try {
      const res = await fetch(`/api/start/${idx}`, { method: 'POST' })
      const json = await res.json()
      if (json.ok) setIsRunning(true)
    } catch {}
    setTestingIdx(null)
  }

  const handleStartSquad = async (squadId: number) => {
    if (startingSquad !== null) return  // 防重复点击
    setStartingSquad(squadId)
    try {
      const res = await fetch('/api/start', { method: 'POST' })
      const json = await res.json()
      if (json.ok) setIsRunning(true)
    } catch {} finally {
      setStartingSquad(null)
    }
  }

  function toggleSquad(id: number) {
    const next = new Set(expandedSquads)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setExpandedSquads(next)
  }

  const totalOnline = emulators.filter(e => e.running).length
  const totalAssigned = accounts.length

  return (
    <div className="space-y-4">
      {/* 标题 */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <Server className="w-5 h-5 text-muted-foreground" />
            部署中心
          </h2>
          <p className="text-sm text-muted-foreground mt-1">
            {totalAssigned} 个已分配，{totalOnline} 个在线
          </p>
        </div>
        <Button variant="outline" size="sm" className="gap-2" onClick={handleRefresh}>
          <RefreshCw className={cn('w-4 h-4', refreshing && 'animate-spin')} />
          刷新
        </Button>
      </div>

      {/* 按大组展示 */}
      {grouped.squads.length === 0 && (
        <div className="bg-card border border-border rounded-lg p-8 text-center">
          <AlertTriangle className="w-8 h-8 text-warning mx-auto mb-3" />
          <p className="text-sm font-medium mb-1">尚未分配实例</p>
          <p className="text-xs text-muted-foreground">
            请前往「设置 → 大组分配」将模拟器分配到队伍后再部署
          </p>
        </div>
      )}

      {grouped.squads.map(squad => {
        const isExpanded = expandedSquads.has(squad.squadId)
        const allOnline = squad.totalOnline === squad.totalAssigned && squad.totalAssigned > 0
        const canStart = squad.totalOnline > 0

        return (
          <div key={squad.squadId} className="bg-card border border-border rounded-lg overflow-hidden">
            {/* 大组标题 */}
            <div className="flex items-center gap-3 px-4 py-3 border-b border-border/50">
              <button
                className="flex items-center gap-2 flex-1 text-left"
                onClick={() => toggleSquad(squad.squadId)}
              >
                {isExpanded ? (
                  <ChevronDown className="w-4 h-4 text-muted-foreground" />
                ) : (
                  <ChevronRight className="w-4 h-4 text-muted-foreground" />
                )}
                <span className="font-semibold text-sm">大组 {squad.squadId}</span>
                <span className="text-xs text-muted-foreground">
                  {squad.teams.map(t => t.team).join('+')} 队
                </span>
                <span className={cn(
                  'ml-2 text-xs font-medium px-2 py-0.5 rounded-full',
                  allOnline ? 'bg-success/10 text-success' : 'bg-muted text-muted-foreground'
                )}>
                  {squad.totalOnline}/{squad.totalAssigned} 在线
                </span>
              </button>

              {canStart && (
                <Button
                  size="sm"
                  className="gap-1.5 h-7 text-xs"
                  onClick={() => handleStartSquad(squad.squadId)}
                  disabled={startingSquad !== null}
                >
                  {startingSquad === squad.squadId ? (
                    <><RefreshCw className="w-3 h-3 animate-spin" />启动中...</>
                  ) : (
                    <><Play className="w-3 h-3" />启动大组</>
                  )}
                </Button>
              )}
            </div>

            {/* 队伍列表 */}
            {isExpanded && (
              <div className="p-4 animate-expand">
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                  {squad.teams.map(teamData => (
                    <TeamSection
                      key={teamData.team}
                      team={teamData.team}
                      online={teamData.online}
                      offline={teamData.offline}
                      testingIdx={testingIdx}
                      onTestRun={handleTestRun}
                    />
                  ))}
                </div>
              </div>
            )}
          </div>
        )
      })}

      {/* 未分配模拟器 */}
      {grouped.unassigned.length > 0 && (
        <div className="bg-card border border-dashed border-border rounded-lg p-4">
          <div className="flex items-center gap-2 mb-3">
            <MonitorSmartphone className="w-4 h-4 text-muted-foreground" />
            <span className="text-sm font-medium text-muted-foreground">未分配</span>
            <span className="text-xs text-muted-foreground/70">{grouped.unassigned.length} 个</span>
          </div>
          <div className="grid grid-cols-3 md:grid-cols-6 gap-2">
            {grouped.unassigned.map(emu => (
              <div
                key={emu.index}
                className={cn(
                  'rounded-lg p-2.5 border text-center',
                  emu.running ? 'border-border bg-muted/30' : 'border-border/50 bg-muted/10 opacity-50'
                )}
              >
                <div className="font-mono text-xs mb-1">#{emu.index}</div>
                <div className="text-[11px] text-muted-foreground truncate">{emu.name}</div>
                <div className="flex items-center justify-center gap-1 mt-1">
                  <div className={cn('w-1.5 h-1.5 rounded-full', emu.running ? 'bg-success' : 'bg-muted-foreground/40')} />
                  <span className="text-[10px] text-muted-foreground">{emu.running ? '在线' : '离线'}</span>
                </div>
              </div>
            ))}
          </div>
          <p className="text-xs text-muted-foreground/60 mt-2">
            未分配的模拟器无法启动，请在「设置 → 大组分配」中拖拽分配
          </p>
        </div>
      )}
    </div>
  )
}

// ── 队伍区块 ──

function TeamSection({
  team,
  online,
  offline,
  testingIdx,
  onTestRun,
}: {
  team: TeamGroup
  online: { emulator: Emulator; account: AccountAssignment }[]
  offline: { emulator: Emulator; account: AccountAssignment }[]
  testingIdx: number | null
  onTestRun: (idx: number) => void
}) {
  const colors = teamColorMap[team] || teamColorMap.A

  if (online.length === 0 && offline.length === 0) return null

  return (
    <div className={cn('rounded-lg border p-3', colors.border)}>
      <div className="flex items-center gap-2 mb-2.5">
        <div className={cn('w-1 h-4 rounded-full', colors.bg)} />
        <span className={cn('text-sm font-semibold', colors.text)}>{team} 队</span>
        <span className="text-xs text-muted-foreground">
          {online.length} 在线 / {online.length + offline.length} 分配
        </span>
      </div>

      {/* 在线 */}
      {online.length > 0 && (
        <div className="space-y-1.5 mb-2">
          {online.map(({ emulator, account }) => (
            <EmulatorRow
              key={emulator.index}
              emulator={emulator}
              account={account}
              online
              testing={testingIdx === emulator.index}
              onTestRun={() => onTestRun(emulator.index)}
            />
          ))}
        </div>
      )}

      {/* 离线 */}
      {offline.length > 0 && (
        <div className="space-y-1.5">
          {offline.map(({ emulator, account }) => (
            <EmulatorRow
              key={emulator.index}
              emulator={emulator}
              account={account}
              online={false}
            />
          ))}
        </div>
      )}
    </div>
  )
}

// ── 单行模拟器 ──

function EmulatorRow({
  emulator,
  account,
  online,
  testing,
  onTestRun,
}: {
  emulator: Emulator
  account: AccountAssignment
  online: boolean
  testing?: boolean
  onTestRun?: () => void
}) {
  return (
    <div className={cn(
      'flex items-center gap-2.5 px-2.5 py-2 rounded-md',
      online ? 'bg-secondary/40' : 'bg-muted/20 opacity-60',
    )}>
      <div className={cn(
        'w-1.5 h-1.5 rounded-full shrink-0',
        online ? 'bg-success' : 'bg-muted-foreground/40',
      )} />
      <span className="font-mono text-xs text-muted-foreground shrink-0">#{emulator.index}</span>
      <span className="text-xs truncate flex-1">{emulator.name}</span>
      {account.role === 'captain' && (
        <span className="text-[10px] font-medium text-warning bg-warning/10 px-1.5 py-0.5 rounded">队长</span>
      )}
      <span className="text-[10px] text-muted-foreground shrink-0">{emulator.adbSerial}</span>

      {online && onTestRun && (
        <Button
          size="sm"
          variant="outline"
          className="h-6 gap-1 text-[11px] px-2"
          onClick={onTestRun}
          disabled={testing}
        >
          <Play className="w-3 h-3" />
          {testing ? '...' : '测试'}
        </Button>
      )}
      {!online && (
        <span className="text-[10px] text-muted-foreground/50">离线</span>
      )}
    </div>
  )
}
