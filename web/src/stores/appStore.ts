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
  activeView: "dashboard" | "settings";
  selectedInstance: number | null; // 展开截图的实例
  logFilter: number | null;
  showLogs: boolean;

  // actions
  setRunning: (running: boolean, paused?: boolean) => void;
  setInstances: (instances: Record<string, InstanceState>) => void;
  setStats: (stats: Stats) => void;
  addLog: (entry: LogEntry) => void;
  clearLogs: () => void;
  setActiveView: (view: AppState["activeView"]) => void;
  setSelectedInstance: (idx: number | null) => void;
  setLogFilter: (filter: number | null) => void;
  setShowLogs: (show: boolean) => void;
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
  activeView: "dashboard",
  selectedInstance: null,
  logFilter: null,
  showLogs: true,

  setRunning: (running, paused) =>
    set((s) => ({ running, paused: paused ?? s.paused })),

  setInstances: (instances) => set({ instances }),

  setStats: (stats) => set({ stats }),

  addLog: (entry) =>
    set((s) => ({
      logs: [...s.logs.slice(-(MAX_LOGS - 1)), entry],
    })),

  clearLogs: () => set({ logs: [] }),

  setActiveView: (activeView) => set({ activeView }),

  setSelectedInstance: (selectedInstance) => set({ selectedInstance }),

  setLogFilter: (logFilter) => set({ logFilter }),

  setShowLogs: (showLogs) => set({ showLogs }),

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
