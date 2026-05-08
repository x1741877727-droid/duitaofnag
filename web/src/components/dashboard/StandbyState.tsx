/**
 * StandbyState — 待启动态: 有账号但还没启动.
 * 简化版: hero 卡 + 实例预览 grid + 启动 CTA.
 * 完整 HeroSplash 动画 (states.jsx 1000+ 行) 此处不做, 只保留核心信息.
 */

import { cn } from '@/lib/utils'
import type { Instance, Emulator, AccountAssignment } from '@/lib/store'

function greetByHour(): string {
  const h = new Date().getHours()
  if (h < 6) return '夜深了'
  if (h < 12) return '早上好'
  if (h < 18) return '下午好'
  return '晚上好'
}

interface PreviewItem {
  index: number
  nickname: string
  group: string
  captain: boolean
  emulatorReady: boolean
  emulatorName: string
}

export function StandbyState({
  accounts,
  emulators,
  onStart,
  onSettings,
  className,
}: {
  /** 来自 store.accounts. */
  accounts: AccountAssignment[]
  /** 来自 store.emulators. */
  emulators: Emulator[]
  onStart: () => void
  onSettings: () => void
  className?: string
}) {
  const items: PreviewItem[] = accounts.map((a) => {
    const emu = emulators.find((e) => e.index === a.index)
    return {
      index: a.index,
      nickname: a.nickname || `玩家${a.index}`,
      group: a.group,
      captain: a.role === 'captain',
      emulatorReady: emu?.running ?? false,
      emulatorName: emu?.name ?? `emulator-${5554 + a.index * 2}`,
    }
  })

  const teamCount = new Set(items.map((i) => i.group)).size
  const emuReady = items.filter((i) => i.emulatorReady).length
  const total = items.length

  return (
    <div
      className={cn(
        'flex-1 flex flex-col bg-background overflow-hidden',
        className,
      )}
    >
      <div className="flex-1 overflow-y-auto px-7 py-6">
        {/* HERO */}
        <div className="mb-6">
          <div className="text-[12px] text-subtle mb-1">{greetByHour()}</div>
          <h1 className="text-[28px] font-semibold text-foreground tracking-tight leading-tight">
            一切准备就绪
          </h1>
          <div className="grid grid-cols-3 gap-3 mt-5 max-w-[680px]">
            <ReadyTile
              label="账号"
              value={`${total} 个号`}
              sub={`已分 ${teamCount} 个队`}
            />
            <ReadyTile
              label="模拟器"
              value={`${emuReady}/${total}`}
              sub="台 LDPlayer 在跑"
              tone={emuReady < total ? 'warn' : 'live'}
            />
            <ReadyTile
              label="加速器"
              value="保护中"
              sub="TUN 模式"
              tone="live"
            />
          </div>
          <div className="flex gap-2 mt-6">
            <button
              type="button"
              onClick={onStart}
              disabled={total === 0 || emuReady === 0}
              className="px-5 py-2.5 rounded-lg text-[13px] font-semibold text-card cursor-pointer border bg-accent border-accent hover:bg-foreground hover:border-foreground disabled:opacity-50 disabled:cursor-not-allowed shadow-[0_1px_2px_rgba(0,0,0,0.08)]"
            >
              开始今天的工作 · {total} 台 →
            </button>
            <button
              type="button"
              onClick={onSettings}
              className="px-4 py-2.5 rounded-lg text-[13px] font-medium text-muted-foreground cursor-pointer border border-border bg-card hover:border-foreground transition-colors"
            >
              去设置
            </button>
          </div>
        </div>

        {/* preview */}
        <div className="mt-2">
          <div
            className="text-[11px] font-semibold text-muted-foreground uppercase mb-2.5"
            style={{ letterSpacing: '.04em' }}
          >
            实例预览
          </div>
          <div className="grid gap-2 grid-cols-[repeat(auto-fill,minmax(220px,1fr))]">
            {items.length === 0 && (
              <div className="text-[13px] text-fainter px-3 py-6">
                还没账号 — 去设置加几个吧
              </div>
            )}
            {items.map((it) => (
              <div
                key={it.index}
                className="rounded-md p-2.5 bg-card border border-border flex items-center gap-2.5"
              >
                <span className="gb-mono text-[10px] font-semibold text-subtle w-7">
                  #{String(it.index).padStart(2, '0')}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5 text-[13px] font-medium text-foreground">
                    <span className="truncate">{it.nickname}</span>
                    {it.captain && (
                      <span className="text-[10px] font-semibold text-accent">
                        队长
                      </span>
                    )}
                  </div>
                  <div className="text-[11px] text-subtle gb-mono truncate">
                    {it.emulatorName} · {it.group} 队
                  </div>
                </div>
                <ReadinessDot ok={it.emulatorReady} />
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

function ReadyTile({
  label,
  value,
  sub,
  tone = 'idle',
}: {
  label: string
  value: string
  sub: string
  tone?: 'idle' | 'live' | 'warn' | 'error'
}) {
  const valueColor =
    tone === 'live'
      ? 'text-[var(--color-live)]'
      : tone === 'warn'
        ? 'text-[var(--color-warn)]'
        : 'text-foreground'
  return (
    <div className="rounded-lg bg-card border border-border p-3.5">
      <div
        className="text-[10px] font-semibold uppercase text-subtle mb-1"
        style={{ letterSpacing: '.04em' }}
      >
        {label}
      </div>
      <div className={cn('text-[20px] font-semibold gb-mono leading-none', valueColor)}>
        {value}
      </div>
      <div className="text-[11px] text-subtle mt-1">{sub}</div>
    </div>
  )
}

function ReadinessDot({ ok }: { ok: boolean }) {
  return (
    <span
      className="inline-block w-2 h-2 rounded-full"
      style={{
        background: ok ? 'var(--color-live)' : 'var(--color-fainter)',
      }}
      aria-label={ok ? '就绪' : '未就绪'}
    />
  )
}
