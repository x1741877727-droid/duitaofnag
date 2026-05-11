// Wizard 共享 helpers.

import type { PillTone } from '@/components/ui/pill'

/** Plan.tier → Pill tone. */
export function tierTone(tier: string): PillTone {
  switch (tier) {
    case 'optimal':
      return 'live'
    case 'balanced':
      return 'idle'
    case 'marginal':
      return 'warn'
    case 'stretched':
      return 'error'
    default:
      return 'idle'
  }
}

/** Plan.tier → 中文 label. */
export function tierLabel(tier: string): string {
  switch (tier) {
    case 'optimal':
      return '推荐'
    case 'balanced':
      return '平衡'
    case 'marginal':
      return '边缘'
    case 'stretched':
      return '极致压榨'
    default:
      return tier
  }
}

/** AuditItem.status → Pill tone. */
export function auditStatusTone(status: string): PillTone {
  switch (status) {
    case 'applied':
      return 'live'
    case 'missing':
      return 'error'
    case 'drift':
      return 'warn'
    case 'below_recommended':
      return 'idle'
    default:
      return 'idle'
  }
}

/** AuditItem.status → 中文 label. */
export function auditStatusLabel(status: string): string {
  switch (status) {
    case 'applied':
      return '已配置'
    case 'missing':
      return '缺失'
    case 'drift':
      return '偏离'
    case 'below_recommended':
      return '低于推荐'
    default:
      return status
  }
}

export const MODE_LABEL: Record<string, string> = {
  stable: '稳定',
  balanced: '平衡',
  speed: '速度',
}

export const MODE_DESC: Record<string, string> = {
  stable: '多缓冲, 闪退率最低. 适合弱机 / 长跑稳定优先.',
  balanced: '默认. 6 实例 E5-2673v3 主流配置最佳点.',
  speed: '极致压榨, 速度优先. 弱机选此项闪退风险大.',
}
