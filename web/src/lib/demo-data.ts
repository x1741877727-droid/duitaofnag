/**
 * Demo mode mock data — 用 ?demo=running URL 参数触发, 注入 12 mock instances
 * 让 user 不启动 backend 也能看 running 视觉.
 *
 * 来源: /tmp/design_dl/gameautomation/project/data.js INSTANCES + INSTANCE_STATES
 */

import type {
  Instance,
  InstanceState,
  TeamGroup,
  TeamRole,
  AccountAssignment,
  LiveDecisionEvent,
} from '@/lib/store'

const NICKS = [
  '北境无双',
  '苍蓝引擎',
  '雾都拾遗',
  '夜航星',
  '荒原浪客',
  '潮汐律者',
  '银杏书生',
  '回声方舟',
  '寒鸦渡口',
  '青砂赋格',
  '霁月清辉',
  '弦外之音',
]

const TEAM_LAYOUT: Array<[TeamGroup, boolean]> = [
  ['A', true],
  ['A', false],
  ['A', false],
  ['B', true],
  ['B', false],
  ['B', false],
  ['C', true],
  ['C', false],
  ['C', false],
  ['D', true],
  ['D', false],
  ['D', false],
]

const INSTANCE_STATES: Array<{
  state: InstanceState
  phaseSec: number
  error?: string
}> = [
  { state: 'lobby', phaseSec: 18 },
  { state: 'in_game', phaseSec: 312 },
  { state: 'in_game', phaseSec: 312 },
  { state: 'ready', phaseSec: 9 },
  { state: 'dismiss_popups', phaseSec: 4 },
  { state: 'error', phaseSec: 142, error: '设备已掉线 · ADB 连接超时' },
  { state: 'in_game', phaseSec: 487 },
  { state: 'in_game', phaseSec: 487 },
  { state: 'in_game', phaseSec: 487 },
  { state: 'launch_game', phaseSec: 22 },
  { state: 'wait_login', phaseSec: 41 },
  { state: 'in_game', phaseSec: 201 },
]

export function getDemoInstances(): Record<string, Instance> {
  const out: Record<string, Instance> = {}
  TEAM_LAYOUT.forEach(([team, captain], i) => {
    out[String(i)] = {
      index: i,
      group: team,
      role: captain ? 'captain' : 'member',
      state: INSTANCE_STATES[i].state,
      nickname: NICKS[i],
      error: INSTANCE_STATES[i].error ?? '',
      stateDuration: INSTANCE_STATES[i].phaseSec,
      adbSerial: `emulator-${5554 + i * 2}`,
    }
  })
  return out
}

export function getDemoAccounts(): AccountAssignment[] {
  return TEAM_LAYOUT.map(([team, captain], i) => ({
    index: i,
    qq: '',
    name: NICKS[i],
    running: true,
    adbSerial: `emulator-${5554 + i * 2}`,
    group: team,
    role: (captain ? 'captain' : 'member') as TeamRole,
    nickname: NICKS[i],
    gameId: '',
  }))
}

export function getDemoEmulators() {
  return TEAM_LAYOUT.map(([, ], i) => ({
    index: i,
    name: `雷电模拟器-${i + 1}`,
    running: true,
    adbSerial: `emulator-${5554 + i * 2}`,
  }))
}

const DECISION_TARGETS = [
  'lobby_btn @ (840, 580)',
  'ready_check @ (1200, 720)',
  'fire_zone @ (640, 360)',
  'follow_captain @ (—)',
  'recall_btn @ (220, 690)',
  'team_join @ (980, 410)',
  'map_setup @ (560, 280)',
  'ace_dismiss @ (1340, 90)',
]
const DECISION_TYPES = ['tap', 'fire', 'follow', 'ocr', 'yolo']

export function getDemoLiveDecisions(): Record<number, LiveDecisionEvent[]> {
  const out: Record<number, LiveDecisionEvent[]> = {}
  for (let idx = 0; idx < 12; idx++) {
    const list: LiveDecisionEvent[] = []
    const seed = (idx * 37) % 100
    for (let n = 0; n < 18; n++) {
      const i = (seed + n * 7) % 8
      const ok = (seed + n) % 11 !== 0
      const tBase = Date.now() - n * 4000 - (idx % 3) * 1000
      list.push({
        id: `mock-${idx}-${n}`,
        type: 'decision',
        ts: tBase,
        instance: idx,
        phase: idx === 5 ? 'error' : idx % 2 ? 'in_game' : 'lobby',
        round: 6 + (idx % 4),
        outcome: ok ? 'success' : 'fail',
        tap_method: DECISION_TYPES[(seed + n) % DECISION_TYPES.length],
        tap_target: DECISION_TARGETS[i],
        verify_success: ok,
        tier_count: 3,
      })
    }
    out[idx] = list
  }
  return out
}

export function getDemoPhaseHistory(): Record<
  number,
  Array<{ from: string; to: string; ts: number }>
> {
  const out: Record<number, Array<{ from: string; to: string; ts: number }>> =
    {}
  const now = Date.now()
  for (let idx = 0; idx < 12; idx++) {
    const sequence: InstanceState[] = [
      'init',
      'accelerator',
      'launch_game',
      'wait_login',
      'lobby',
      'team_create',
      'in_game',
    ]
    const list: Array<{ from: string; to: string; ts: number }> = []
    let cursor = now - 60_000 * 12
    for (let i = 1; i < sequence.length; i++) {
      list.push({
        from: sequence[i - 1],
        to: sequence[i],
        ts: cursor,
      })
      cursor += 60_000 + (idx % 4) * 18_000
    }
    out[idx] = list
  }
  return out
}
