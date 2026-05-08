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

/** 加速器状态 — 只保留 TUN 模式 (本地虚拟网卡), 删 APK 时代的 3 层校验.
 *  TUN 模式下校验 = wintun 适配器在跑 + gameproxy 转发健康. */
export interface AcceleratorStatus {
  running: boolean
  mode: 'TUN' | 'OFF'
  uptime_seconds?: number
  /** 累计改写包数 = rule1_hits + rule2_hits (gameproxy 转发统计). */
  rewrites?: number
  pid?: number
  message?: string
}

export interface AccelInstanceState {
  loading: boolean
  status: AcceleratorStatus | null
  error: string | null
}

// ─── 中控台 v3 — 实时事件 / 决策 ───

// 顶层 nav (重构后 11→7)
export type ConsoleView =
  | 'dashboard'      // 运行控制
  | 'console'        // 中控台
  | 'accelerator'    // 加速器 (TUN 状态 + 实时计数)
  | 'recognition'    // 识别 (含 sub: templates / template-tuner / yolo / ocr)
  | 'data'           // 数据 (含 sub: archive / memory / oracle)
  | 'perf'           // 性能
  | 'settings'

// 识别页内的 4 个 sub-tab
export type RecognitionSubView = 'templates' | 'template-tuner' | 'yolo' | 'ocr'

// 数据页内的 3 个 sub-tab
export type DataSubView = 'archive' | 'memory' | 'oracle'

export interface LiveDecisionEvent {
  // 单条决策摘要 (从 /ws/live decision 事件来)
  id: string
  type: 'decision'
  ts: number
  instance: number
  phase: string
  round: number
  outcome: string
  tap_method?: string
  tap_target?: string
  tap_xy?: [number, number] | null
  verify_success?: boolean | null
  tier_count?: number
}

export interface PhaseChangeEvent {
  type: 'phase_change'
  instance: number
  from: string
  to: string
  ts: number
}

export type LiveEvent =
  | LiveDecisionEvent
  | PhaseChangeEvent
  | { type: 'hello' | 'pong'; ts: number; version?: string }
  | { type: 'intervene_ack'; command: string; instance: number; token: string; result: string; ts: number }
  | { type: 'perf'; ts: number; global?: Record<string, number>; instances?: Record<number, Record<string, unknown>> }

interface AppState {
  isRunning: boolean
  setIsRunning: (running: boolean) => void

  // dev / 客户 模式切换 (持久化 localStorage)
  // dev=true → 显示识别 nav + PhaseTester + 设置里 dev 模块
  devMode: boolean
  setDevMode: (b: boolean) => void

  currentView: ConsoleView
  setCurrentView: (view: ConsoleView) => void

  // 识别 / 数据 子页 (新 nav 重构)
  recognitionSubView: RecognitionSubView
  setRecognitionSubView: (sub: RecognitionSubView) => void
  dataSubView: DataSubView
  setDataSubView: (sub: DataSubView) => void

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

  // ── 中控台 v3 ──
  liveConnected: boolean
  setLiveConnected: (c: boolean) => void

  // 每实例最近 N 条决策事件 (中控台 ActionLog / 实时观察台用)
  liveDecisions: Record<number, LiveDecisionEvent[]>
  pushLiveDecision: (ev: LiveDecisionEvent) => void

  // 每实例阶段变迁列表 (PhaseTimeline 用)
  phaseHistory: Record<number, { from: string; to: string; ts: number }[]>
  pushPhaseChange: (ev: PhaseChangeEvent) => void

  // 选中实例 (中控台聚焦哪一个; 兼容字段)
  focusedInstance: number | null
  setFocusedInstance: (idx: number | null) => void

  // 多选实例 (中控台分屏 + 阶段测试自动注入)
  selectedInstances: number[]
  toggleInstanceSelection: (idx: number) => void
  setSelectedInstances: (list: number[]) => void
  clearInstanceSelection: () => void

  // 阶段测试持久状态 (切页面不丢)
  phaseTester: {
    selKeys: string[]               // 选中阶段 (按勾选顺序)
    selInst: number | null          // 没多选时的备用单实例
    selRole: 'captain' | 'member'
    expectedId: string              // P5 用: 等待真人入队的 10 位玩家 ID
    keepGoing: boolean
    busy: boolean
    progress: string
    results: { phase: string; phase_name: string; ok: boolean; duration_ms?: number; error?: string }[]
  }
  setPhaseTester: (patch: Partial<AppState['phaseTester']>) => void
  addPhaseResult: (r: AppState['phaseTester']['results'][number]) => void
  clearPhaseResults: () => void
}

const MAX_LIVE_PER_INSTANCE = 50

const MAX_LOGS = 500
// MAX_LIVE_PER_INSTANCE defined above near interface

// dev mode 持久化 — 客户机上 false, dev 机上 true
const DEV_MODE_KEY = 'gamebot.devMode'
function readDevMode(): boolean {
  try {
    return localStorage.getItem(DEV_MODE_KEY) === '1'
  } catch {
    return false
  }
}
function writeDevMode(b: boolean) {
  try {
    localStorage.setItem(DEV_MODE_KEY, b ? '1' : '0')
  } catch {
    /* ignore */
  }
}

export const useAppStore = create<AppState>((set) => ({
  isRunning: false,
  setIsRunning: (running) => set({ isRunning: running }),

  devMode: readDevMode(),
  setDevMode: (b) => {
    writeDevMode(b)
    set({ devMode: b })
  },

  currentView: 'dashboard',
  setCurrentView: (view) => set({ currentView: view }),

  recognitionSubView: 'templates',
  setRecognitionSubView: (sub) => set({ recognitionSubView: sub }),
  dataSubView: 'archive',
  setDataSubView: (sub) => set({ dataSubView: sub }),

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

  // ── 中控台 v3 ──
  liveConnected: false,
  setLiveConnected: (c) => set({ liveConnected: c }),

  liveDecisions: {},
  pushLiveDecision: (ev) => set((state) => {
    const cur = state.liveDecisions[ev.instance] || []
    const next = [...cur, ev]
    if (next.length > MAX_LIVE_PER_INSTANCE) next.splice(0, next.length - MAX_LIVE_PER_INSTANCE)
    return {
      liveDecisions: {
        ...state.liveDecisions,
        [ev.instance]: next,
      },
    }
  }),

  phaseHistory: {},
  pushPhaseChange: (ev) => set((state) => {
    const cur = state.phaseHistory[ev.instance] || []
    const next = [...cur, { from: ev.from, to: ev.to, ts: ev.ts }]
    if (next.length > 30) next.splice(0, next.length - 30)
    return {
      phaseHistory: {
        ...state.phaseHistory,
        [ev.instance]: next,
      },
    }
  }),

  focusedInstance: null,
  setFocusedInstance: (idx) => set((state) => {
    // 设单选 = 同步 selected (单元素); idx=null 清空
    return {
      focusedInstance: idx,
      selectedInstances: idx === null ? [] : [idx],
    }
  }),

  selectedInstances: [],
  toggleInstanceSelection: (idx) => set((state) => {
    const cur = state.selectedInstances
    const has = cur.includes(idx)
    const next = has ? cur.filter((x) => x !== idx) : [...cur, idx].sort((a, b) => a - b)
    return {
      selectedInstances: next,
      // focusedInstance 兼容: 单选 = 该 idx, 多/空 = null
      focusedInstance: next.length === 1 ? next[0] : null,
    }
  }),
  setSelectedInstances: (list) => set({
    selectedInstances: [...list].sort((a, b) => a - b),
    focusedInstance: list.length === 1 ? list[0] : null,
  }),
  clearInstanceSelection: () => set({ selectedInstances: [], focusedInstance: null }),

  phaseTester: {
    selKeys: [],
    selInst: null,
    selRole: 'captain',
    expectedId: '',
    keepGoing: false,
    busy: false,
    progress: '',
    results: [],
  },
  setPhaseTester: (patch) => set((state) => ({
    phaseTester: { ...state.phaseTester, ...patch },
  })),
  addPhaseResult: (r) => set((state) => ({
    phaseTester: { ...state.phaseTester, results: [...state.phaseTester.results, r] },
  })),
  clearPhaseResults: () => set((state) => ({
    phaseTester: { ...state.phaseTester, results: [] },
  })),
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
