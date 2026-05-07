import { useCallback, useEffect, useState } from 'react'
import { useAppStore, type AccelInstanceState } from '@/lib/store'
import { Button } from '@/components/ui/button'
import { Zap, Play, Square, RefreshCw, CheckCircle2, XCircle, Loader2, Shield, FileText, Wifi, AlertTriangle } from 'lucide-react'
import { cn } from '@/lib/utils'

// Step 2 加速器双形态切换 (Round 5c)
type AccelMode = 'apk' | 'tun' | null
interface AccelModeInstance {
  instance_index: number
  qq: string
  nickname: string
  accel_mode_override: AccelMode
  effective_mode: 'apk' | 'tun'
}
interface AccelModeState {
  master_disable_tun: boolean
  default_mode: 'apk' | 'tun'
  instances: AccelModeInstance[]
}

function AccelModePanel() {
  const [state, setState] = useState<AccelModeState | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const r = await fetch('/api/accelerator/mode')
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      setState(await r.json())
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const setMode = useCallback(async (idx: number, mode: AccelMode) => {
    await fetch(`/api/accelerator/mode/${idx}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode }),
    })
    await refresh()
  }, [refresh])

  const toggleMaster = useCallback(async () => {
    if (!state) return
    const next = !state.master_disable_tun
    if (next && !window.confirm('紧急 kill switch: 全部模拟器立即强制走 APK 模式. 确认?')) return
    await fetch('/api/accelerator/master_disable_tun', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ disable: next }),
    })
    await refresh()
  }, [state, refresh])

  const setDefaultMode = useCallback(async (mode: 'apk' | 'tun') => {
    if (mode === 'tun' && !window.confirm('全局默认设为 TUN (脱 APK 模式). 未设 override 的模拟器都会切换. 确认?')) return
    await fetch('/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ accelerator_default_mode: mode }),
    })
    await refresh()
  }, [refresh])

  if (loading && !state) {
    return <div className="text-sm text-muted-foreground p-4">加载加速器模式...</div>
  }
  if (error) {
    return <div className="text-sm text-destructive p-4">加速器模式加载失败: {error}</div>
  }
  if (!state) return null

  const masterActive = state.master_disable_tun

  return (
    <section className="bg-card border border-border rounded-lg">
      <div className="flex items-center gap-3 px-5 py-4 border-b border-border">
        <Shield className="h-5 w-5 text-blue-500" />
        <h2 className="font-semibold text-foreground">加速器模式 (Step 2 双形态)</h2>
        <span className="text-xs text-muted-foreground ml-2">
          APK = vpn-app + SOCKS5 / TUN = 宿主机 wintun (脱 APK)
        </span>
      </div>

      <div className="p-5 space-y-4">
        {/* 紧急 kill switch */}
        <div className={cn(
          "flex items-center justify-between p-3 rounded-lg border",
          masterActive ? "bg-red-500/10 border-red-500/40" : "bg-muted/30 border-border"
        )}>
          <div className="flex items-center gap-3">
            <AlertTriangle className={cn("h-5 w-5", masterActive ? "text-red-500" : "text-muted-foreground")} />
            <div>
              <div className="font-medium text-sm">紧急 kill switch</div>
              <div className="text-xs text-muted-foreground">
                {masterActive
                  ? '已激活: 所有模拟器强制走 APK 模式 (即使 mode=tun)'
                  : '未激活: 按 default_mode + per-instance override 决议'}
              </div>
            </div>
          </div>
          <Button
            variant={masterActive ? 'default' : 'destructive'}
            size="sm"
            onClick={toggleMaster}
            className="gap-1"
          >
            {masterActive ? '解除' : '激活紧急回滚'}
          </Button>
        </div>

        {/* 全局 default */}
        <div className="flex items-center justify-between p-3 rounded-lg bg-muted/30">
          <div>
            <div className="font-medium text-sm">全局默认模式</div>
            <div className="text-xs text-muted-foreground">未设 override 的模拟器走这个</div>
          </div>
          <select
            value={state.default_mode}
            onChange={e => setDefaultMode(e.target.value as 'apk' | 'tun')}
            disabled={masterActive}
            className="px-3 py-1.5 rounded border border-border bg-background text-sm font-mono"
          >
            <option value="apk">APK (生产)</option>
            <option value="tun">TUN (脱 APK)</option>
          </select>
        </div>

        {/* per-instance 列表 */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <div className="text-sm font-medium">模拟器实例 (per-instance override)</div>
            <Button variant="outline" size="sm" onClick={refresh} disabled={loading} className="gap-1">
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
              刷新
            </Button>
          </div>

          {state.instances.length === 0 ? (
            <div className="text-xs text-muted-foreground py-2">accounts.json 没账号. 在账号设置里加完才能 per-instance 切换.</div>
          ) : (
            <div className="space-y-1.5">
              {state.instances.map(it => (
                <div key={it.instance_index}
                  className="flex items-center justify-between px-3 py-2 rounded bg-muted/20 border border-border/50">
                  <div className="flex items-center gap-3 text-sm">
                    <code className="text-muted-foreground">实例{it.instance_index}</code>
                    <span>{it.nickname || it.qq || '(空)'}</span>
                    <span className={cn(
                      "px-2 py-0.5 rounded text-xs font-mono",
                      it.effective_mode === 'tun' ? "bg-purple-500/20 text-purple-400" : "bg-green-500/20 text-green-400"
                    )}>
                      {it.effective_mode}
                    </span>
                    {masterActive && it.accel_mode_override === 'tun' && (
                      <span className="text-xs text-red-400">(被 kill switch 强制 apk)</span>
                    )}
                  </div>
                  <select
                    value={it.accel_mode_override ?? ''}
                    onChange={e => setMode(it.instance_index, (e.target.value || null) as AccelMode)}
                    disabled={masterActive}
                    className="px-2 py-1 rounded border border-border bg-background text-xs font-mono"
                  >
                    <option value="">(用全局: {state.default_mode})</option>
                    <option value="apk">apk</option>
                    <option value="tun">tun</option>
                  </select>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="text-xs text-muted-foreground space-y-1 pt-2 border-t border-border">
          <p>· 模式切换在 backend 重启 / 该 instance 下次跑 phase 时生效 (调 _start_vpn)</p>
          <p>· 紧急 kill switch 是最高优先级 — 任何 tun override 都被它压</p>
          <p>· per-instance override = null 时跟随全局 default_mode</p>
        </div>
      </div>
    </section>
  )
}

export function AcceleratorView() {
  const { emulators, accelStates, setAccelState } = useAppStore()

  const checkStatus = useCallback(async (idx: number) => {
    setAccelState(idx, { loading: true, error: null })
    try {
      const res = await fetch(`/api/accelerator/status/${idx}`)
      const data = await res.json()
      if (data.ok) {
        setAccelState(idx, { loading: false, status: data })
      } else {
        setAccelState(idx, { loading: false, error: data.message || '检查失败' })
      }
    } catch {
      setAccelState(idx, { loading: false, error: '网络错误' })
    }
  }, [setAccelState])

  const startAccel = useCallback(async (idx: number) => {
    setAccelState(idx, { loading: true, error: null })
    try {
      const res = await fetch(`/api/accelerator/start/${idx}`, { method: 'POST' })
      const data = await res.json()
      if (data.ok) {
        await checkStatus(idx)
      } else {
        setAccelState(idx, { loading: false, error: data.message || '启动失败' })
      }
    } catch {
      setAccelState(idx, { loading: false, error: '网络错误' })
    }
  }, [setAccelState, checkStatus])

  const stopAccel = useCallback(async (idx: number) => {
    setAccelState(idx, { loading: true, error: null })
    try {
      await fetch(`/api/accelerator/stop/${idx}`, { method: 'POST' })
      await checkStatus(idx)
    } catch {
      setAccelState(idx, { loading: false, error: '网络错误' })
    }
  }, [setAccelState, checkStatus])

  const instances = emulators.filter(e => e.running)

  return (
    <div className="space-y-6">
      {/* Step 2 加速器模式 (Round 5c) — 顶部新增, 不改下面现有 UI */}
      <AccelModePanel />

      <section className="bg-card border border-border rounded-lg">
        <div className="flex items-center gap-3 px-5 py-4 border-b border-border">
          <Zap className="h-5 w-5 text-yellow-500" />
          <h2 className="font-semibold text-foreground">加速器管理</h2>
          <span className="text-sm text-muted-foreground ml-2">sing-box → SOCKS5 + TLS MITM → 154.64.255.93:9900</span>
        </div>

        <div className="p-5 space-y-4">
          <div className="bg-muted/50 rounded-lg p-4">
            <div className="text-sm text-muted-foreground mb-2">代理服务器</div>
            <div className="flex items-center gap-6 text-sm">
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground">地址</span>
                <code className="text-foreground font-mono">154.64.255.93:9900</code>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground">协议</span>
                <code className="text-foreground font-mono">SOCKS5 + TLS MITM</code>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground">引擎</span>
                <code className="text-foreground font-mono">sing-box v1.13.7</code>
              </div>
            </div>
          </div>

          {instances.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              没有检测到运行中的模拟器实例。请先启动 LDPlayer。
            </div>
          ) : (
            <div className="space-y-3">
              {instances.map(emu => {
                const st: AccelInstanceState = accelStates[emu.index] || { loading: false, status: null, error: null }
                const status = st.status
                const allGreen = status?.level1_proxy && status?.level2_mitm && status?.level3_rules

                return (
                  <div key={emu.index}
                    className={cn(
                      "border rounded-lg p-4 transition-colors",
                      allGreen ? "border-green-500/50 bg-green-500/5" :
                      status?.running ? "border-yellow-500/50 bg-yellow-500/5" :
                      "border-border"
                    )}
                  >
                    <div className="flex items-center justify-between mb-3">
                      <div className="flex items-center gap-2">
                        {allGreen ? (
                          <CheckCircle2 className="h-5 w-5 text-green-500" />
                        ) : status?.running ? (
                          <Wifi className="h-5 w-5 text-yellow-500" />
                        ) : (
                          <XCircle className="h-5 w-5 text-muted-foreground" />
                        )}
                        <span className="font-medium">{emu.name || `实例 ${emu.index}`}</span>
                        <code className="text-xs text-muted-foreground">{emu.adbSerial}</code>
                        {status?.pid && (
                          <span className="text-xs text-muted-foreground">PID: {status.pid}</span>
                        )}
                      </div>

                      <div className="flex items-center gap-2">
                        <Button
                          variant="outline" size="sm"
                          onClick={() => checkStatus(emu.index)}
                          disabled={st.loading}
                          className="gap-1"
                        >
                          {st.loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                          检测
                        </Button>
                        {status?.running ? (
                          <Button variant="destructive" size="sm" onClick={() => stopAccel(emu.index)} disabled={st.loading} className="gap-1">
                            <Square className="h-4 w-4" /> 停止
                          </Button>
                        ) : (
                          <Button variant="default" size="sm" onClick={() => startAccel(emu.index)} disabled={st.loading} className="gap-1">
                            <Play className="h-4 w-4" /> 启动
                          </Button>
                        )}
                      </div>
                    </div>

                    {status && (
                      <div className="space-y-2">
                        <div className="flex items-center gap-4 text-sm">
                          <VerifyBadge ok={status.level1_proxy} label="代理通" icon={<Wifi className="h-3.5 w-3.5" />} detail={status.proxy_ip ? `IP: ${status.proxy_ip}` : undefined} />
                          <VerifyBadge ok={status.level2_mitm} label="MITM" icon={<Shield className="h-3.5 w-3.5" />}
                            detail={status.proxy_detail ? `${(status.proxy_detail as Record<string, number>).mitm_connections} 连接` : undefined} />
                          <VerifyBadge ok={status.level3_rules} label="规则" icon={<FileText className="h-3.5 w-3.5" />}
                            detail={status.proxy_detail ? `${(status.proxy_detail as Record<string, number>).rules_enabled}/${(status.proxy_detail as Record<string, number>).rules_loaded} 条` : undefined} />
                        </div>

                        {status.message && (
                          <div className={cn("text-xs px-2 py-1 rounded",
                            allGreen ? "bg-green-500/10 text-green-400" : "bg-yellow-500/10 text-yellow-400"
                          )}>
                            {status.message}
                          </div>
                        )}

                        {allGreen && status.proxy_detail && (
                          <ProxyDetail detail={status.proxy_detail as Record<string, unknown>} />
                        )}
                      </div>
                    )}

                    {st.error && (
                      <div className="text-sm text-destructive mt-2">{st.error}</div>
                    )}
                  </div>
                )
              })}
            </div>
          )}

          <div className="text-xs text-muted-foreground mt-4 space-y-1">
            <p>· 三级验证全部通过（绿色）才能安全启动游戏</p>
            <p>· Level 1: 代理通（出口IP = 服务器IP）</p>
            <p>· Level 2: MITM 通（TLS 解密启用）</p>
            <p>· Level 3: 规则生效（封包改写规则已加载）</p>
          </div>
        </div>
      </section>
    </div>
  )
}

function VerifyBadge({ ok, label, icon, detail }: { ok: boolean; label: string; icon: React.ReactNode; detail?: string }) {
  return (
    <div className={cn(
      "flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium",
      ok ? "bg-green-500/20 text-green-400" : "bg-muted text-muted-foreground"
    )}>
      {icon}
      {label}
      {ok && <CheckCircle2 className="h-3 w-3" />}
      {detail && <span className="text-muted-foreground font-normal ml-1">{detail}</span>}
    </div>
  )
}

function ProxyDetail({ detail }: { detail: Record<string, unknown> }) {
  const uptime = detail.uptime_seconds as number
  const h = Math.floor(uptime / 3600)
  const m = Math.floor((uptime % 3600) / 60)
  const s = uptime % 60
  const uptimeStr = h > 0 ? `${h}时${m}分${s}秒` : m > 0 ? `${m}分${s}秒` : `${s}秒`
  const matched = detail.rules_matched as Record<string, number> | undefined

  return (
    <div className="bg-muted/30 rounded p-3 text-xs space-y-1 mt-2">
      <div className="flex justify-between">
        <span className="text-muted-foreground">代理运行时长</span>
        <span>{uptimeStr}</span>
      </div>
      <div className="flex justify-between">
        <span className="text-muted-foreground">总封包 / 已改写</span>
        <span>{detail.packets_total as number} / <span className={(detail.packets_modified as number) > 0 ? "text-green-400" : "text-yellow-400"}>{detail.packets_modified as number}</span></span>
      </div>
      {matched && Object.keys(matched).length > 0 && (
        <div className="flex justify-between">
          <span className="text-muted-foreground">规则命中</span>
          <span className="text-green-400">
            {Object.entries(matched).map(([k, v]) => `${k}×${v}`).join(', ')}
          </span>
        </div>
      )}
    </div>
  )
}
