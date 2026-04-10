'use client'

import { useAppStore, type TeamGroup, type TeamRole, type AccountAssignment } from '@/lib/store'
import { cn } from '@/lib/utils'
import { 
  FolderOpen, 
  RefreshCw, 
  Save, 
  Plus, 
  Trash2, 
  MonitorSmartphone,
  ArrowDownToLine,
  Settings,
  Users,
  Database
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useState } from 'react'

export function SettingsView() {
  return (
    <div className="space-y-6 max-w-5xl">
      <EnvConfig />
      <EmulatorScan />
      <AccountTable />
    </div>
  )
}

function EnvConfig() {
  const { settings, setSettings } = useAppStore()
  const [localSettings, setLocalSettings] = useState(settings)

  const handleSave = () => {
    setSettings(localSettings)
  }

  return (
    <section className="bg-card border border-border rounded-lg">
      <div className="flex items-center gap-2 p-4 border-b border-border">
        <Settings className="w-4 h-4 text-muted-foreground" />
        <h2 className="font-medium">环境配置</h2>
      </div>
      
      <div className="p-4">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <SettingField
            label="雷电模拟器路径"
            placeholder="D:\leidian\LDPlayer9"
            value={localSettings.ldPlayerPath}
            onChange={(v) => setLocalSettings({ ...localSettings, ldPlayerPath: v })}
            icon={<FolderOpen className="w-4 h-4" />}
          />
          <SettingField
            label="ADB 路径"
            placeholder="留空自动使用模拟器自带"
            value={localSettings.adbPath}
            onChange={(v) => setLocalSettings({ ...localSettings, adbPath: v })}
            icon={<Database className="w-4 h-4" />}
          />
          <SettingField
            label="游戏包名"
            placeholder="com.tencent.tmgp.pubgmhd"
            value={localSettings.gamePackage}
            onChange={(v) => setLocalSettings({ ...localSettings, gamePackage: v })}
          />
          <SettingField
            label="目标地图"
            placeholder="狙击团竞"
            value={localSettings.targetMap}
            onChange={(v) => setLocalSettings({ ...localSettings, targetMap: v })}
          />
        </div>
        
        <div className="mt-4 flex justify-end">
          <Button onClick={handleSave} size="sm" className="gap-2">
            <Save className="w-4 h-4" />
            保存
          </Button>
        </div>
      </div>
    </section>
  )
}

function SettingField({ 
  label, 
  placeholder, 
  value, 
  onChange,
  icon
}: { 
  label: string
  placeholder: string
  value: string
  onChange: (v: string) => void
  icon?: React.ReactNode
}) {
  return (
    <div className="space-y-1.5">
      <label className="text-sm text-muted-foreground">{label}</label>
      <div className="relative">
        {icon && (
          <div className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground">
            {icon}
          </div>
        )}
        <Input
          placeholder={placeholder}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className={cn('bg-background', icon && 'pl-10')}
        />
      </div>
    </div>
  )
}

function EmulatorScan() {
  const { emulators, setAccounts, accounts } = useAppStore()
  const [refreshing, setRefreshing] = useState(false)

  const handleRefresh = () => {
    setRefreshing(true)
    setTimeout(() => setRefreshing(false), 1000)
  }

  const handleSync = () => {
    const newAccounts: AccountAssignment[] = emulators.map((e, idx) => {
      const existing = accounts.find(a => a.index === e.index)
      if (existing) return existing
      
      return {
        index: e.index,
        name: e.name,
        running: e.running,
        adbSerial: e.adbSerial,
        group: idx < 3 ? 'A' as TeamGroup : 'B' as TeamGroup,
        role: (idx === 0 || idx === 3) ? 'captain' as TeamRole : 'member' as TeamRole,
        nickname: e.name,
        gameId: '',
      }
    })
    setAccounts(newAccounts)
  }

  return (
    <section className="bg-card border border-border rounded-lg">
      <div className="flex items-center justify-between p-4 border-b border-border">
        <div className="flex items-center gap-2">
          <MonitorSmartphone className="w-4 h-4 text-muted-foreground" />
          <h2 className="font-medium">模拟器检测</h2>
          <span className="text-xs text-muted-foreground">检测到 {emulators.length} 个</span>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" className="gap-2" onClick={handleRefresh}>
            <RefreshCw className={cn('w-4 h-4', refreshing && 'animate-spin')} />
            刷新
          </Button>
          <Button variant="outline" size="sm" className="gap-2" onClick={handleSync}>
            <ArrowDownToLine className="w-4 h-4" />
            同步到分配表
          </Button>
        </div>
      </div>

      <div className="p-4">
        <div className="grid grid-cols-3 md:grid-cols-6 gap-2">
          {emulators.map(emulator => (
            <div 
              key={emulator.index}
              className={cn(
                'rounded-lg p-3 border',
                emulator.running 
                  ? 'bg-success/5 border-success/20' 
                  : 'bg-muted border-border opacity-60'
              )}
            >
              <div className="font-medium text-sm mb-1">
                #{emulator.index}
              </div>
              <div className="text-xs text-muted-foreground truncate">
                {emulator.name}
              </div>
              <div className="flex items-center gap-1.5 text-xs mt-1">
                <div className={cn(
                  'w-1.5 h-1.5 rounded-full',
                  emulator.running ? 'bg-success' : 'bg-muted-foreground'
                )} />
                <span className={emulator.running ? 'text-success' : 'text-muted-foreground'}>
                  {emulator.running ? '运行中' : '已停止'}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

function AccountTable() {
  const { accounts, setAccounts } = useAppStore()

  const updateAccount = (index: number, field: keyof AccountAssignment, value: string) => {
    const newAccounts = accounts.map(a => {
      if (a.index === index) {
        return { ...a, [field]: value }
      }
      return a
    })
    setAccounts(newAccounts)
  }

  const deleteAccount = (index: number) => {
    setAccounts(accounts.filter(a => a.index !== index))
  }

  const addAccount = () => {
    const maxIndex = Math.max(...accounts.map(a => a.index), -1)
    const newAccount: AccountAssignment = {
      index: maxIndex + 1,
      name: `模拟器-${maxIndex + 1}`,
      running: false,
      adbSerial: `emulator-${5554 + (maxIndex + 1) * 2}`,
      group: 'A',
      role: 'member',
      nickname: `模拟器-${maxIndex + 1}`,
      gameId: '',
    }
    setAccounts([...accounts, newAccount])
  }

  return (
    <section className="bg-card border border-border rounded-lg">
      <div className="flex items-center gap-2 p-4 border-b border-border">
        <Users className="w-4 h-4 text-muted-foreground" />
        <h2 className="font-medium">实例分配表</h2>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-muted/30">
              <th className="px-4 py-3 text-left font-medium text-muted-foreground">序号</th>
              <th className="px-4 py-3 text-left font-medium text-muted-foreground">名称</th>
              <th className="px-4 py-3 text-left font-medium text-muted-foreground">状态</th>
              <th className="px-4 py-3 text-left font-medium text-muted-foreground">组</th>
              <th className="px-4 py-3 text-left font-medium text-muted-foreground">角色</th>
              <th className="px-4 py-3 text-left font-medium text-muted-foreground">昵称</th>
              <th className="px-4 py-3 text-left font-medium text-muted-foreground">游戏ID</th>
              <th className="px-4 py-3 w-12"></th>
            </tr>
          </thead>
          <tbody>
            {accounts.map(account => (
              <tr key={account.index} className="border-b border-border hover:bg-muted/20">
                <td className="px-4 py-2 font-mono">#{account.index}</td>
                <td className="px-4 py-2">{account.name}</td>
                <td className="px-4 py-2">
                  <span className={cn(
                    'inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs',
                    account.running 
                      ? 'bg-success/10 text-success' 
                      : 'bg-muted text-muted-foreground'
                  )}>
                    <span className={cn(
                      'w-1.5 h-1.5 rounded-full',
                      account.running ? 'bg-success' : 'bg-muted-foreground'
                    )} />
                    {account.running ? '在线' : '离线'}
                  </span>
                </td>
                <td className="px-4 py-2">
                  <select
                    value={account.group}
                    onChange={(e) => updateAccount(account.index, 'group', e.target.value)}
                    className="bg-background border border-border rounded px-2 py-1 text-sm"
                  >
                    <option value="A">A</option>
                    <option value="B">B</option>
                  </select>
                </td>
                <td className="px-4 py-2">
                  <select
                    value={account.role}
                    onChange={(e) => updateAccount(account.index, 'role', e.target.value)}
                    className="bg-background border border-border rounded px-2 py-1 text-sm"
                  >
                    <option value="captain">队长</option>
                    <option value="member">队员</option>
                  </select>
                </td>
                <td className="px-4 py-2">
                  <Input
                    value={account.nickname}
                    onChange={(e) => updateAccount(account.index, 'nickname', e.target.value)}
                    className="h-8 bg-background w-24"
                  />
                </td>
                <td className="px-4 py-2">
                  <Input
                    value={account.gameId}
                    onChange={(e) => updateAccount(account.index, 'gameId', e.target.value)}
                    placeholder="选填"
                    className="h-8 bg-background w-24"
                  />
                </td>
                <td className="px-4 py-2">
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-8 w-8 text-muted-foreground hover:text-destructive"
                    onClick={() => deleteAccount(account.index)}
                  >
                    <Trash2 className="w-4 h-4" />
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="p-4 border-t border-border flex items-center justify-between">
        <Button variant="outline" size="sm" className="gap-2" onClick={addAccount}>
          <Plus className="w-4 h-4" />
          添加实例
        </Button>
        <Button size="sm" className="gap-2">
          <Save className="w-4 h-4" />
          保存
        </Button>
      </div>
    </section>
  )
}
