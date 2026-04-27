import { useCallback } from 'react'
import { useAppStore, type AccelInstanceState } from '@/lib/store'
import { Button } from '@/components/ui/button'
import { Zap, Play, Square, RefreshCw, CheckCircle2, XCircle, Loader2, Shield, FileText, Wifi } from 'lucide-react'
import { cn } from '@/lib/utils'

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
