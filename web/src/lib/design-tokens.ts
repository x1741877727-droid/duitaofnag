/**
 * Design tokens — Claude Design "warm cream" 系统的 TS 镜像.
 *
 * CSS 真值在 [index.css](../index.css) 的 @theme 里定义,
 * 这里导出 TS 常量供少数需要 inline style 的地方用 (canvas / 动画).
 * 颜色 token 名跟 index.css `--color-*` 对齐.
 *
 * 来源: /tmp/design_dl/gameautomation/project/parts.jsx (GB_C)
 */

export const C = {
  bg: '#f7f6f2',
  surface: '#ffffff',
  surface2: '#f1efe9',
  border: '#e7e3da',
  borderSoft: '#efece4',
  ink: '#1a1a17',
  ink2: '#4a463e',
  ink3: '#7d7869',
  ink4: '#aca695',
  accent: '#3b3a36',
  live: '#16a34a',
  liveSoft: '#dcfce7',
  warn: '#d97706',
  warnSoft: '#fef3c7',
  idle: '#71717a',
  idleSoft: '#f4f4f5',
  error: '#dc2626',
  errorSoft: '#fee2e2',
  errorBg: '#fff1f2',
  fontUi:
    "'Inter','PingFang SC','Source Han Sans SC',ui-sans-serif,system-ui,sans-serif",
  fontMono:
    "'IBM Plex Mono','JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,monospace",
} as const

/** 5 层识别证据颜色 — 跟 [archive-view.jsx](/tmp/design_dl/gameautomation/project/archive-view.jsx) 实际代码锁死. */
export const TIER_COLOR = {
  T1: { fg: '#16a34a', soft: '#dcfce7', label: '模板' },
  T2: { fg: '#d97706', soft: '#fef3c7', label: 'OCR' },
  T3: { fg: '#1a1a17', soft: '#efece4', label: 'YOLO' },
  T4: { fg: '#7d7869', soft: '#efece4', label: 'Memory' },
  T5: { fg: '#aca695', soft: '#f4f4f5', label: '兜底' },
} as const

export type TierKey = keyof typeof TIER_COLOR

/** 状态 tone — 决定 StatusDot / Pill 染色. */
export type Tone = 'live' | 'warn' | 'error' | 'idle'

/** InstanceState → Tone 映射. 跟 [store.ts](./store.ts) InstanceState 13 值对齐. */
export const STATE_TONE: Record<string, Tone> = {
  init: 'idle',
  accelerator: 'warn',
  launch_game: 'warn',
  wait_login: 'idle',
  dismiss_popups: 'warn',
  lobby: 'live',
  map_setup: 'warn',
  team_create: 'warn',
  team_join: 'warn',
  ready: 'live',
  in_game: 'live',
  done: 'live',
  error: 'error',
}

/** InstanceState → 中文 label. */
export const STATE_LABEL: Record<string, string> = {
  init: '等待启动',
  accelerator: '启动代理',
  launch_game: '启动游戏',
  wait_login: '等待登录',
  dismiss_popups: '清理弹窗',
  lobby: '大厅就绪',
  map_setup: '设置地图',
  team_create: '创建队伍',
  team_join: '加入队伍',
  ready: '准备就绪',
  in_game: '战斗中',
  done: '完成',
  error: '出错',
}

export function toneColor(tone: Tone): string {
  switch (tone) {
    case 'live':
      return C.live
    case 'warn':
      return C.warn
    case 'error':
      return C.error
    default:
      return C.idle
  }
}

/** uptime 格式化, HH:MM:SS. */
export function fmtUptime(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  const hh = String(Math.floor(s / 3600)).padStart(2, '0')
  const mm = String(Math.floor((s % 3600) / 60)).padStart(2, '0')
  const ss = String(s % 60).padStart(2, '0')
  return `${hh}:${mm}:${ss}`
}

/** phase 已停留时长, 例 "8s" / "2m04s". */
export function fmtPhase(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)}s`
  const m = Math.floor(seconds / 60)
  const r = Math.floor(seconds % 60)
  return `${m}m${String(r).padStart(2, '0')}s`
}
