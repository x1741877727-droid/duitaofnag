/**
 * SettingsView — 设置页, 跟 DataShell / RecognitionShell 同视觉风格.
 *
 * 三个 section 单页串列 (环境配置 / 模拟器扫描 / 队伍编排), 不用 sub-tab.
 * 每个 section 用相同 card chrome (C.surface + C.border + 标题条), 无 lucide
 * 图标 (跟数据/识别页一致, 都靠文字).
 */

import { useEffect, useState } from 'react'
import {
  useAppStore,
  type TeamGroup,
  type TeamRole,
  type AccountAssignment,
} from '@/lib/store'
import { C } from '@/lib/design-tokens'
import { SquadBuilder } from './squad-builder'
import { PerfOptimizeWizard } from './optimize/PerfOptimizeWizard'

// ─── 共享 chrome ──────────────────────────────────────────────────

function Section({
  title,
  hint,
  rightSlot,
  children,
}: {
  title: string
  hint?: string
  rightSlot?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section
      style={{
        background: C.surface,
        border: `1px solid ${C.border}`,
        borderRadius: 10,
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          padding: '12px 18px',
          borderBottom: `1px solid ${C.borderSoft}`,
          background: C.surface,
        }}
      >
        <h2
          style={{
            margin: 0,
            fontSize: 13.5,
            fontWeight: 600,
            color: C.ink,
            letterSpacing: '.01em',
          }}
        >
          {title}
        </h2>
        {hint && (
          <span style={{ fontSize: 11.5, color: C.ink3 }}>{hint}</span>
        )}
        <span style={{ flex: 1 }} />
        {rightSlot}
      </div>
      <div style={{ padding: 18 }}>{children}</div>
    </section>
  )
}

function PrimaryButton({
  onClick,
  disabled,
  children,
}: {
  onClick?: () => void
  disabled?: boolean
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      style={{
        padding: '6px 14px',
        borderRadius: 6,
        fontSize: 12.5,
        fontWeight: 500,
        color: '#fff',
        background: disabled ? C.ink4 : C.ink,
        border: 'none',
        cursor: disabled ? 'not-allowed' : 'pointer',
        fontFamily: 'inherit',
        opacity: disabled ? 0.7 : 1,
      }}
    >
      {children}
    </button>
  )
}

function GhostButton({
  onClick,
  children,
}: {
  onClick?: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        padding: '5px 12px',
        borderRadius: 6,
        fontSize: 12,
        fontWeight: 500,
        color: C.ink2,
        background: C.surface,
        border: `1px solid ${C.border}`,
        cursor: 'pointer',
        fontFamily: 'inherit',
      }}
    >
      {children}
    </button>
  )
}

function TextInput({
  value,
  onChange,
  placeholder,
  width,
  mono,
}: {
  value: string
  onChange: (v: string) => void
  placeholder?: string
  width?: number | string
  mono?: boolean
}) {
  return (
    <input
      type="text"
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      style={{
        width: width ?? '100%',
        padding: '7px 10px',
        borderRadius: 6,
        border: `1px solid ${C.border}`,
        background: C.surface,
        color: C.ink,
        fontSize: 12.5,
        fontFamily: mono ? C.fontMono : 'inherit',
        outline: 'none',
        boxSizing: 'border-box',
      }}
    />
  )
}

// ─── 主入口 ──────────────────────────────────────────────────────

export function SettingsView() {
  const [perfWizardOpen, setPerfWizardOpen] = useState(false)
  return (
    <div
      style={{
        width: '100%',
        height: '100%',
        background: C.bg,
        color: C.ink,
        fontFamily: C.fontUi,
        overflow: 'auto',
      }}
    >
      <PerfOptimizeWizard
        open={perfWizardOpen}
        onClose={() => setPerfWizardOpen(false)}
        autoTrigger={false}
      />
      <div
        style={{
          maxWidth: 1200,
          margin: '0 auto',
          padding: '22px 22px 60px',
          display: 'flex',
          flexDirection: 'column',
          gap: 18,
        }}
      >
        <PerfOptimizeButton onOpen={() => setPerfWizardOpen(true)} />
        <EnvConfig />
        <EmulatorScan />
        <SquadBuilder />
      </div>
    </div>
  )
}

function PerfOptimizeButton({ onOpen }: { onOpen: () => void }) {
  return (
    <Section
      title="硬件性能优化"
      hint="一键探测电脑 + 应用最优 LDPlayer / Android 配置"
      rightSlot={
        <PrimaryButton onClick={onOpen}>开启向导</PrimaryButton>
      }
    >
      <div style={{ fontSize: 12, color: C.ink3, lineHeight: 1.7 }}>
        ★ 首次使用会自动弹出。后续换硬件 / 加内存可手动重跑。
        <br />
        改动包括: LDPlayer 实例 RAM 上限 + CPU 绑核 + Android Doze 关 + 14 个无用系统包 disable + logcat 限流。
      </div>
    </Section>
  )
}

// ─── Section 1: 环境配置 ─────────────────────────────────────────

function EnvConfig() {
  const { settings, setSettings } = useAppStore()
  const [localSettings, setLocalSettings] = useState(settings)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    fetch('/api/settings')
      .then((r) => r.json())
      .then((data) => {
        const s = {
          ...settings,
          ldPlayerPath: data.ldplayer_path || '',
          adbPath: data.adb_path || '',
          gamePackage: data.game_package || settings.gamePackage,
          targetMap: data.game_map || '',
        }
        setLocalSettings(s)
        setSettings(s)
      })
      .catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleSave = async () => {
    setSaving(true)
    setSettings(localSettings)
    await fetch('/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ldplayer_path: localSettings.ldPlayerPath,
        adb_path: localSettings.adbPath,
        game_package: localSettings.gamePackage,
        game_map: localSettings.targetMap,
      }),
    }).catch(() => {})
    setSaving(false)
  }

  const fields: Array<[string, string, string, (v: string) => void, boolean?]> = [
    [
      '雷电模拟器路径',
      'D:\\leidian\\LDPlayer9',
      localSettings.ldPlayerPath,
      (v) => setLocalSettings({ ...localSettings, ldPlayerPath: v }),
      true,
    ],
    [
      'ADB 路径',
      '留空 = 用模拟器自带',
      localSettings.adbPath,
      (v) => setLocalSettings({ ...localSettings, adbPath: v }),
      true,
    ],
    [
      '游戏包名',
      'com.tencent.tmgp.pubgmhd',
      localSettings.gamePackage,
      (v) => setLocalSettings({ ...localSettings, gamePackage: v }),
      true,
    ],
    [
      '目标地图',
      '狙击团竞',
      localSettings.targetMap,
      (v) => setLocalSettings({ ...localSettings, targetMap: v }),
    ],
  ]

  return (
    <Section
      title="环境配置"
      hint="路径 + 游戏包名 + 默认地图"
      rightSlot={
        <PrimaryButton onClick={handleSave} disabled={saving}>
          {saving ? '保存中…' : '保存'}
        </PrimaryButton>
      }
    >
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(2, 1fr)',
          gap: 14,
        }}
      >
        {fields.map(([label, ph, val, onChange, mono]) => (
          <div key={label} style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <label style={{ fontSize: 11.5, color: C.ink3, fontWeight: 500 }}>
              {label}
            </label>
            <TextInput
              value={val}
              onChange={onChange}
              placeholder={ph}
              mono={mono}
            />
          </div>
        ))}
      </div>
    </Section>
  )
}

// ─── Section 2: 模拟器扫描 ───────────────────────────────────────

function EmulatorScan() {
  const { emulators, setEmulators, setAccounts, accounts } = useAppStore()
  const [refreshing, setRefreshing] = useState(false)

  useEffect(() => {
    handleRefresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleRefresh = async () => {
    setRefreshing(true)
    try {
      const res = await fetch('/api/emulators')
      const json = await res.json()
      if (json.instances) {
        setEmulators(
          json.instances.map((e: Record<string, unknown>) => ({
            index: e.index as number,
            name: e.name as string,
            running: e.running as boolean,
            adbSerial:
              (e.adb_serial as string) ||
              `emulator-${5554 + (e.index as number) * 2}`,
          })),
        )
      }
    } catch {}
    setRefreshing(false)
  }

  const handleSync = () => {
    const newAccounts: AccountAssignment[] = emulators.map((e, idx) => {
      const existing = accounts.find((a) => a.index === e.index)
      if (existing) return existing
      return {
        index: e.index,
        qq: '',
        name: e.name,
        running: e.running,
        adbSerial: e.adbSerial,
        group: idx < 3 ? ('A' as TeamGroup) : ('B' as TeamGroup),
        role:
          idx === 0 || idx === 3
            ? ('captain' as TeamRole)
            : ('member' as TeamRole),
        nickname: e.name,
        gameId: '',
      }
    })
    setAccounts(newAccounts)
  }

  const runningCount = emulators.filter((e) => e.running).length

  return (
    <Section
      title="模拟器扫描"
      hint={`检测到 ${emulators.length} 个 · ${runningCount} 在线`}
      rightSlot={
        <div style={{ display: 'flex', gap: 8 }}>
          <GhostButton onClick={handleRefresh}>
            {refreshing ? '刷新中…' : '刷新'}
          </GhostButton>
          <GhostButton onClick={handleSync}>同步到分配表</GhostButton>
        </div>
      }
    >
      {emulators.length === 0 ? (
        <div
          style={{
            padding: '24px 16px',
            textAlign: 'center',
            color: C.ink4,
            fontSize: 12.5,
          }}
        >
          没扫到模拟器 — 检查雷电路径是否对, 或点"刷新"重试.
        </div>
      ) : (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))',
            gap: 10,
          }}
        >
          {emulators.map((e) => {
            const on = e.running
            return (
              <div
                key={e.index}
                style={{
                  background: on ? C.surface : C.surface2,
                  border: `1px solid ${on ? C.border : C.borderSoft}`,
                  borderRadius: 8,
                  padding: '10px 12px',
                  opacity: on ? 1 : 0.65,
                }}
              >
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'baseline',
                    justifyContent: 'space-between',
                    marginBottom: 4,
                  }}
                >
                  <span
                    style={{
                      fontFamily: C.fontMono,
                      fontSize: 12,
                      fontWeight: 600,
                      color: C.ink,
                    }}
                  >
                    #{String(e.index).padStart(2, '0')}
                  </span>
                  <span
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: 5,
                      fontSize: 10.5,
                      color: on ? C.live : C.ink4,
                    }}
                  >
                    <span
                      style={{
                        width: 6,
                        height: 6,
                        borderRadius: '50%',
                        background: on ? C.live : C.ink4,
                      }}
                    />
                    {on ? '运行中' : '已停止'}
                  </span>
                </div>
                <div
                  style={{
                    fontSize: 11.5,
                    color: C.ink2,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {e.name}
                </div>
                <div
                  style={{
                    marginTop: 4,
                    fontSize: 10,
                    color: C.ink4,
                    fontFamily: C.fontMono,
                  }}
                >
                  {e.adbSerial}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </Section>
  )
}
