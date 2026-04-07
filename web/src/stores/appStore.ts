import { create } from "zustand";

export interface InstanceState {
  index: number;
  group: string;
  role: string;
  state: string;
  nickname: string;
  error: string;
  state_duration: number;
}

export interface LogEntry {
  timestamp: number;
  instance: number;
  level: string;
  message: string;
  state: string;
}

export interface Stats {
  total_attempts: number;
  success_count: number;
  abort_count: number;
  error_count: number;
  running_duration: number;
}

interface AppState {
  // 运行状态
  running: boolean;
  paused: boolean;
  instances: Record<string, InstanceState>;
  stats: Stats;
  logs: LogEntry[];

  // UI 状态
  activeTab: "dashboard" | "settings" | "debug";
  logFilter: number | null; // null = 全部, 数字 = 按实例过滤

  // actions
  setRunning: (running: boolean, paused?: boolean) => void;
  setInstances: (instances: Record<string, InstanceState>) => void;
  setStats: (stats: Stats) => void;
  addLog: (entry: LogEntry) => void;
  clearLogs: () => void;
  setActiveTab: (tab: AppState["activeTab"]) => void;
  setLogFilter: (filter: number | null) => void;
  updateInstance: (index: number, old: string, next: string) => void;
}

const MAX_LOGS = 500;

export const useAppStore = create<AppState>((set) => ({
  running: false,
  paused: false,
  instances: {},
  stats: {
    total_attempts: 0,
    success_count: 0,
    abort_count: 0,
    error_count: 0,
    running_duration: 0,
  },
  logs: [],
  activeTab: "dashboard",
  logFilter: null,

  setRunning: (running, paused) =>
    set((s) => ({ running, paused: paused ?? s.paused })),

  setInstances: (instances) => set({ instances }),

  setStats: (stats) => set({ stats }),

  addLog: (entry) =>
    set((s) => ({
      logs: [...s.logs.slice(-(MAX_LOGS - 1)), entry],
    })),

  clearLogs: () => set({ logs: [] }),

  setActiveTab: (activeTab) => set({ activeTab }),

  setLogFilter: (logFilter) => set({ logFilter }),

  updateInstance: (index, _old, next) =>
    set((s) => {
      const key = String(index);
      const inst = s.instances[key];
      if (!inst) return s;
      return {
        instances: {
          ...s.instances,
          [key]: { ...inst, state: next, state_duration: 0 },
        },
      };
    }),
}));
