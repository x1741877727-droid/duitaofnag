import { create } from 'zustand'

// 流水线阶段 - 可扩展配置
export interface PipelineStage {
  key: string
  label: string
  states: string[]
}

// 默认流水线阶段
export const defaultPipelineStages: PipelineStage[] = [
  { key: 'accelerator', label: '加速器', states: ['accelerator'] },
  { key: 'launch_game', label: '启动游戏', states: ['launch_game'] },
  { key: 'wait_login', label: '等待登录', states: ['wait_login'] },
  { key: 'dismiss_popups', label: '清理弹窗', states: ['dismiss_popups'] },
  { key: 'lobby', label: '进入大厅', states: ['lobby'] },
  { key: 'map_setup', label: '设置地图', states: ['map_setup'] },
  { key: 'team_create', label: '创建队伍', states: ['team_create'] },
  { key: 'team_join', label: '加入队伍', states: ['team_join'] },
  { key: 'ready', label: '准备就绪', states: ['ready'] },
  { key: 'in_game', label: '游戏中', states: ['in_game'] },
]

// 实例状态类型
export type InstanceState = 
  | 'init' 
  | 'accelerator' 
  | 'launch_game' 
  | 'wait_login' 
  | 'dismiss_popups' 
  | 'lobby' 
  | 'map_setup' 
  | 'team_create' 
  | 'team_join' 
  | 'ready'
  | 'in_game'
  | 'done' 
  | 'error'

export type TeamGroup = 'A' | 'B'
export type TeamRole = 'captain' | 'member'

// 实例数据
export interface Instance {
  index: number
  group: TeamGroup
  role: TeamRole
  state: InstanceState
  nickname: string
  error: string
  stateDuration: number
  adbSerial?: string
}

// 模拟器信息
export interface Emulator {
  index: number
  name: string
  running: boolean
  adbSerial: string
}

// 日志条目
export interface LogEntry {
  id: string
  timestamp: number
  instance: number | 'SYS' | 'GUARD'
  level: 'info' | 'warn' | 'error'
  message: string
  state?: InstanceState
}

// 设置
export interface Settings {
  ldPlayerPath: string
  adbPath: string
  gamePackage: string
  targetMap: string
  pipelineStages: PipelineStage[]
}

// 账号分配
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

interface AppState {
  // 运行状态
  isRunning: boolean
  setIsRunning: (running: boolean) => void
  
  // 当前视图
  currentView: 'dashboard' | 'settings'
  setCurrentView: (view: 'dashboard' | 'settings') => void
  
  // 日志面板
  showLogPanel: boolean
  setShowLogPanel: (show: boolean) => void
  logPanelExpanded: boolean
  setLogPanelExpanded: (expanded: boolean) => void
  
  // 实例数据
  instances: Record<string, Instance>
  setInstances: (instances: Record<string, Instance>) => void
  updateInstance: (index: string, data: Partial<Instance>) => void
  
  // 模拟器列表
  emulators: Emulator[]
  setEmulators: (emulators: Emulator[]) => void
  
  // 日志
  logs: LogEntry[]
  addLog: (log: Omit<LogEntry, 'id'>) => void
  clearLogs: () => void
  logFilter: number | 'all'
  setLogFilter: (filter: number | 'all') => void
  
  // 设置
  settings: Settings
  setSettings: (settings: Settings) => void
  
  // 账号分配
  accounts: AccountAssignment[]
  setAccounts: (accounts: AccountAssignment[]) => void
}

// Mock 数据
const mockEmulators: Emulator[] = [
  { index: 0, name: '雷电模拟器', running: true, adbSerial: 'emulator-5554' },
  { index: 1, name: '雷电模拟器-1', running: true, adbSerial: 'emulator-5556' },
  { index: 2, name: '雷电模拟器-2', running: true, adbSerial: 'emulator-5558' },
  { index: 3, name: '雷电模拟器-3', running: true, adbSerial: 'emulator-5560' },
  { index: 4, name: '雷电模拟器-4', running: false, adbSerial: 'emulator-5562' },
  { index: 5, name: '雷电模拟器-5', running: false, adbSerial: 'emulator-5564' },
]

const mockInstances: Record<string, Instance> = {
  '0': { index: 0, group: 'A', role: 'captain', state: 'lobby', nickname: '雷电模拟器', error: '', stateDuration: 12 },
  '1': { index: 1, group: 'A', role: 'member', state: 'dismiss_popups', nickname: '雷电模拟器-1', error: '', stateDuration: 5 },
  '2': { index: 2, group: 'A', role: 'member', state: 'map_setup', nickname: '雷电模拟器-2', error: '', stateDuration: 0 },
  '3': { index: 3, group: 'B', role: 'captain', state: 'accelerator', nickname: '雷电模拟器-3', error: '', stateDuration: 3 },
  '4': { index: 4, group: 'B', role: 'member', state: 'error', nickname: '雷电模拟器-4', error: 'ADB连接超时', stateDuration: 8 },
  '5': { index: 5, group: 'B', role: 'member', state: 'init', nickname: '雷电模拟器-5', error: '', stateDuration: 0 },
}

const mockAccounts: AccountAssignment[] = [
  { index: 0, name: '雷电模拟器', running: true, adbSerial: 'emulator-5554', group: 'A', role: 'captain', nickname: '玩家A1', gameId: '12345678' },
  { index: 1, name: '雷电模拟器-1', running: true, adbSerial: 'emulator-5556', group: 'A', role: 'member', nickname: '玩家A2', gameId: '12345679' },
  { index: 2, name: '雷电模拟器-2', running: true, adbSerial: 'emulator-5558', group: 'A', role: 'member', nickname: '玩家A3', gameId: '12345680' },
  { index: 3, name: '雷电模拟器-3', running: true, adbSerial: 'emulator-5560', group: 'B', role: 'captain', nickname: '玩家B1', gameId: '12345681' },
  { index: 4, name: '雷电模拟器-4', running: false, adbSerial: 'emulator-5562', group: 'B', role: 'member', nickname: '玩家B2', gameId: '12345682' },
  { index: 5, name: '雷电模拟器-5', running: false, adbSerial: 'emulator-5564', group: 'B', role: 'member', nickname: '玩家B3', gameId: '12345683' },
]

const mockLogs: LogEntry[] = [
  { id: '1', timestamp: Date.now() - 30000, instance: 0, level: 'info', message: '启动加速器', state: 'accelerator' },
  { id: '2', timestamp: Date.now() - 28000, instance: 0, level: 'info', message: '加速器已连接', state: 'accelerator' },
  { id: '3', timestamp: Date.now() - 25000, instance: 1, level: 'info', message: '启动游戏', state: 'launch_game' },
  { id: '4', timestamp: Date.now() - 22000, instance: 0, level: 'info', message: '进入大厅', state: 'lobby' },
  { id: '5', timestamp: Date.now() - 15000, instance: 'SYS', level: 'info', message: '系统检测正常' },
  { id: '6', timestamp: Date.now() - 12000, instance: 1, level: 'warn', message: '清理弹窗中...', state: 'dismiss_popups' },
  { id: '7', timestamp: Date.now() - 10000, instance: 'GUARD', level: 'info', message: '自动清除弹窗' },
  { id: '8', timestamp: Date.now() - 5000, instance: 2, level: 'info', message: '设置地图', state: 'map_setup' },
  { id: '9', timestamp: Date.now() - 3000, instance: 4, level: 'error', message: 'ADB连接超时', state: 'error' },
]

export const useAppStore = create<AppState>((set) => ({
  isRunning: true,
  setIsRunning: (running) => set({ isRunning: running }),
  
  currentView: 'dashboard',
  setCurrentView: (view) => set({ currentView: view }),
  
  showLogPanel: true,
  setShowLogPanel: (show) => set({ showLogPanel: show }),
  logPanelExpanded: true,
  setLogPanelExpanded: (expanded) => set({ logPanelExpanded: expanded }),
  
  instances: mockInstances,
  setInstances: (instances) => set({ instances }),
  updateInstance: (index, data) => set((state) => ({
    instances: {
      ...state.instances,
      [index]: { ...state.instances[index], ...data }
    }
  })),
  
  emulators: mockEmulators,
  setEmulators: (emulators) => set({ emulators }),
  
  logs: mockLogs,
  addLog: (log) => set((state) => ({
    logs: [...state.logs, { ...log, id: `${Date.now()}-${Math.random()}` }]
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
  
  accounts: mockAccounts,
  setAccounts: (accounts) => set({ accounts }),
}))

// 状态配置 - 无emoji
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

// 获取状态在流水线中的位置
export function getStageIndex(state: InstanceState, stages: PipelineStage[]): number {
  for (let i = 0; i < stages.length; i++) {
    if (stages[i].states.includes(state)) {
      return i
    }
  }
  return -1
}
