import { create } from 'zustand'

// 流水线阶段
export interface PipelineStage {
  key: string
  label: string
  states: string[]
}

export const defaultPipelineStages: PipelineStage[] = [
  { key: 'accelerator', label: '加速器', states: ['accelerator'] },
  { key: 'launch_game', label: '启动游戏', states: ['launch_game'] },
  { key: 'dismiss_popups', label: '清理弹窗', states: ['wait_login', 'dismiss_popups'] },
  { key: 'lobby', label: '进入大厅', states: ['lobby'] },
  { key: 'team', label: '组队', states: ['team_create', 'team_join'] },
  { key: 'map_setup', label: '设置地图', states: ['map_setup'] },
  { key: 'ready', label: '准备就绪', states: ['ready'] },
  { key: 'in_game', label: '游戏中', states: ['in_game'] },
]

export type InstanceState =
  | 'init' | 'accelerator' | 'launch_game' | 'wait_login'
  | 'dismiss_popups' | 'lobby' | 'map_setup' | 'team_create'
  | 'team_join' | 'ready' | 'in_game' | 'done' | 'error'

export type TeamGroup = 'A' | 'B' | 'C' | 'D' | 'E' | 'F'
export type TeamRole = 'captain' | 'member'

export interface Instance {
  index: number
  group: TeamGroup
  role: TeamRole
  state: InstanceState
  nickname: string
  error: string
  stateDuration: number
  adbSerial?: string
  stageTimes?: Record<string, number>  // {"accelerator": 12.3, "launch_game": 8.1, ...}
}

export interface Emulator {
  index: number
  name: string
  running: boolean
  adbSerial: string
}

export interface LogEntry {
  id: string
  timestamp: number
  instance: number | 'SYS' | 'GUARD'
  level: 'info' | 'warn' | 'error'
  message: string
  state?: InstanceState
}

export interface Settings {
  ldPlayerPath: string
  adbPath: string
  gamePackage: string
  targetMap: string
  pipelineStages: PipelineStage[]
}

export interface AccountAssignment {
  index: number
  name: string
  running: boolean
  adbSerial: string
  group: TeamGroup
  role: TeamRole
  nickname: string
  gameId: string
}

export interface AcceleratorStatus {
  running: boolean
  level1_proxy: boolean
  level2_mitm: boolean
  level3_rules: boolean
  proxy_ip?: string
  proxy_detail?: Record<string, unknown>
  pid?: number
  message?: string
}

export interface AccelInstanceState {
  loading: boolean
  status: AcceleratorStatus | null
  error: string | null
}

interface AppState {
  isRunning: boolean
  setIsRunning: (running: boolean) => void

  currentView: 'dashboard' | 'settings'
  setCurrentView: (view: 'dashboard' | 'settings') => void

  showLogPanel: boolean
  setShowLogPanel: (show: boolean) => void
  logPanelExpanded: boolean
  setLogPanelExpanded: (expanded: boolean) => void

  instances: Record<string, Instance>
  setInstances: (instances: Record<string, Instance>) => void
  updateInstance: (index: string, data: Partial<Instance>) => void

  emulators: Emulator[]
  setEmulators: (emulators: Emulator[]) => void

  accelStates: Record<number, AccelInstanceState>
  setAccelState: (index: number, patch: Partial<AccelInstanceState>) => void

  logs: LogEntry[]
  addLog: (log: Omit<LogEntry, 'id'>) => void
  clearLogs: () => void
  logFilter: number | 'all'
  setLogFilter: (filter: number | 'all') => void

  settings: Settings
  setSettings: (settings: Settings) => void

  accounts: AccountAssignment[]
  setAccounts: (accounts: AccountAssignment[]) => void

  // 大组切换 (大组1=A+B, 大组2=C+D, ...)
  activeSquad: number
  setActiveSquad: (squad: number) => void

  // 运行时长
  runningDuration: number
  setRunningDuration: (d: number) => void
}

const MAX_LOGS = 500

export const useAppStore = create<AppState>((set) => ({
  isRunning: false,
  setIsRunning: (running) => set({ isRunning: running }),

  currentView: 'dashboard',
  setCurrentView: (view) => set({ currentView: view }),

  showLogPanel: true,
  setShowLogPanel: (show) => set({ showLogPanel: show }),
  logPanelExpanded: true,
  setLogPanelExpanded: (expanded) => set({ logPanelExpanded: expanded }),

  instances: {},
  setInstances: (instances) => set({ instances }),
  updateInstance: (index, data) => set((state) => ({
    instances: {
      ...state.instances,
      [index]: { ...state.instances[index], ...data }
    }
  })),

  emulators: [],
  setEmulators: (emulators) => set({ emulators }),

  accelStates: {},
  setAccelState: (index, patch) => set((state) => {
    const prev = state.accelStates[index] ?? { loading: false, status: null, error: null }
    return {
      accelStates: {
        ...state.accelStates,
        [index]: { ...prev, ...patch },
      },
    }
  }),

  logs: [],
  addLog: (log) => set((state) => ({
    logs: [...state.logs.slice(-(MAX_LOGS - 1)), { ...log, id: `${Date.now()}-${Math.random()}` }]
  })),
  clearLogs: () => set({ logs: [] }),
  logFilter: 'all',
  setLogFilter: (filter) => set({ logFilter: filter }),

  settings: {
    ldPlayerPath: 'D:\\leidian\\LDPlayer9',
    adbPath: '',
    gamePackage: 'com.tencent.tmgp.pubgmhd',
    targetMap: '狙击团竞',
    pipelineStages: defaultPipelineStages,
  },
  setSettings: (settings) => set({ settings }),

  accounts: [],
  setAccounts: (accounts) => set({ accounts }),

  activeSquad: 1,
  setActiveSquad: (squad) => set({ activeSquad: squad }),

  runningDuration: 0,
  setRunningDuration: (d) => set({ runningDuration: d }),
}))

// 状态配置
export const stateConfig: Record<InstanceState, { label: string; status: 'idle' | 'running' | 'success' | 'warning' | 'error' }> = {
  init: { label: '等待启动', status: 'idle' },
  accelerator: { label: '启动加速器', status: 'running' },
  launch_game: { label: '启动游戏', status: 'running' },
  wait_login: { label: '等待登录', status: 'running' },
  dismiss_popups: { label: '清理弹窗', status: 'warning' },
  lobby: { label: '大厅就绪', status: 'success' },
  map_setup: { label: '设置地图', status: 'running' },
  team_create: { label: '创建队伍', status: 'running' },
  team_join: { label: '加入队伍', status: 'running' },
  ready: { label: '准备就绪', status: 'success' },
  in_game: { label: '游戏中', status: 'success' },
  done: { label: '完成', status: 'success' },
  error: { label: '出错', status: 'error' },
}

// 大组 → 队伍映射: 大组1=A+B, 大组2=C+D, 大组3=E+F
export const squadTeams: Record<number, TeamGroup[]> = {
  1: ['A', 'B'],
  2: ['C', 'D'],
  3: ['E', 'F'],
}

export const allSquadIds = [1, 2, 3] as const
export const MAX_SQUADS = 3
export const SLOTS_PER_TEAM = 3

export function getSquadTeams(squad: number): TeamGroup[] {
  return squadTeams[squad] || ['A', 'B']
}

export function getStageIndex(state: InstanceState, stages: PipelineStage[]): number {
  for (let i = 0; i < stages.length; i++) {
    if (stages[i].states.includes(state)) {
      return i
    }
  }
  return -1
}
