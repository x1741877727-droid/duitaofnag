/**
 * StandbyState — 待启动态.
 *
 * 默认 (非 dev) 进 HeroSplash 全屏 hero (开工 + scope picker, 完全照搬设计).
 * dev 模式下显示 PhaseTester (跳过 hero).
 *
 * 来源: states.jsx StandbyState (71-163)
 */

import { useMemo, useState } from 'react'
import { useAppStore, type AccountAssignment } from '@/lib/store'
import { PhaseTester } from './PhaseTester'
import { HeroSplash } from './HeroSplash'

function greetByHour(): string {
  const h = new Date().getHours()
  if (h < 6) return '夜深了'
  if (h < 12) return '早上好'
  if (h < 18) return '下午好'
  return '晚上好'
}

export function StandbyState({
  accounts,
  onStart,
  onSettings,
}: {
  accounts: AccountAssignment[]
  onStart: () => void
  onSettings: () => void
}) {
  const devMode = useAppStore((s) => s.devMode)
  const tunMode: 'TUN' | 'SOCKS5' | 'OFF' = 'TUN' // TODO: read from /api/tun/state

  // 派生 instance 数据 (从 accounts 派生满足 HeroSplash 的 Instance shape)
  const instances = useMemo(
    () =>
      accounts.map((a) => ({
        index: a.index,
        group: a.group,
        role: a.role,
        nickname: a.nickname || `玩家${a.index}`,
        state: 'init' as const,
        error: '',
        stateDuration: 0,
        adbSerial: a.adbSerial,
      })),
    [accounts],
  )

  const greet = greetByHour()
  const teamCount = useMemo(
    () => new Set(instances.map((i) => i.group)).size,
    [instances],
  )

  // dev: skip hero, show PhaseTester
  // non-dev: 默认 hero, 用户点 "先看看" 进 reveal 模式
  const [heroMode, setHeroMode] = useState(!devMode)

  if (heroMode && !devMode) {
    return (
      <HeroSplash
        greet={greet}
        count={instances.length}
        teamCount={teamCount}
        accel={tunMode}
        instances={instances}
        onStart={() => onStart()}
        onReveal={() => setHeroMode(false)}
      />
    )
  }

  // reveal mode: 简易 instance 预览 (设计的 ReadyCard + PreviewPanel 后续增强)
  return (
    <div className="flex-1 flex flex-col bg-background overflow-y-auto">
      <div className="px-7 py-6">
        {!devMode && (
          <button
            type="button"
            onClick={() => setHeroMode(true)}
            className="text-[11.5px] text-subtle bg-transparent border-0 cursor-pointer pb-3"
            style={{ fontFamily: 'inherit' }}
          >
            ← 回封面
          </button>
        )}
        <div className="text-[12px] text-subtle mb-1">{greet}</div>
        <h1 className="text-[28px] font-semibold text-foreground tracking-tight">
          一切准备就绪 · {instances.length} 台
        </h1>
        <div className="flex gap-2 mt-5">
          <button
            type="button"
            onClick={onStart}
            className="px-5 py-2.5 rounded-lg text-[13px] font-semibold text-card cursor-pointer border border-accent bg-accent hover:bg-foreground"
          >
            开始今天的工作 · {instances.length} 台 →
          </button>
          <button
            type="button"
            onClick={onSettings}
            className="px-4 py-2.5 rounded-lg text-[13px] text-muted-foreground cursor-pointer border border-border bg-card hover:border-foreground"
          >
            去设置
          </button>
        </div>

        {/* preview list */}
        <div className="mt-6">
          <div
            className="text-[11px] font-semibold text-muted-foreground uppercase mb-2.5"
            style={{ letterSpacing: '.04em' }}
          >
            实例预览
          </div>
          <div className="grid gap-2 grid-cols-[repeat(auto-fill,minmax(220px,1fr))]">
            {instances.map((it) => (
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
                    {it.role === 'captain' && (
                      <span className="text-[10px] font-semibold text-accent">
                        队长
                      </span>
                    )}
                  </div>
                  <div className="text-[11px] text-subtle gb-mono truncate">
                    {it.adbSerial} · {it.group} 队
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {devMode && (
          <div className="mt-6">
            <PhaseTester />
          </div>
        )}
      </div>
    </div>
  )
}
