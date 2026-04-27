/**
 * 实时观察台 — 中间大图区, 显示聚焦实例的最新决策帧.
 *
 * 数据源: liveDecisions[focused] 的最后一条 decision id
 * 图片: GET /api/decision/{id}/image/yolo_annot.jpg (优先, 已带 YOLO 框 + 点击点)
 *      失败回退 input.jpg (原始帧).
 */
import { useEffect, useRef, useState } from 'react'
import { useAppStore } from '@/lib/store'

export function LiveInspector() {
  const focused = useAppStore((s) => s.focusedInstance)
  const liveDecisions = useAppStore((s) => s.liveDecisions)

  const latestDec = focused !== null ? (liveDecisions[focused] || []).slice(-1)[0] : null
  const decisionId = latestDec?.id || ''

  const [src, setSrc] = useState<string>('')
  const [imgError, setImgError] = useState<boolean>(false)
  const lastIdRef = useRef<string>('')

  useEffect(() => {
    if (!decisionId) {
      setSrc('')
      setImgError(false)
      lastIdRef.current = ''
      return
    }
    if (decisionId === lastIdRef.current) return
    lastIdRef.current = decisionId
    setImgError(false)
    // 先尝试 yolo_annot, 失败时 onError 回退 input
    setSrc(`/api/decision/${encodeURIComponent(decisionId)}/image/yolo_annot.jpg?t=${Date.now()}`)
  }, [decisionId])

  if (focused === null) {
    return (
      <div className="rounded-lg border border-dashed border-border bg-card flex items-center justify-center text-sm text-muted-foreground">
        选一个实例看实时帧
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-border bg-card flex flex-col overflow-hidden">
      <div className="flex items-center gap-3 px-3 py-2 border-b border-border text-xs">
        <span className="font-semibold">实时观察台 · 实例 #{focused}</span>
        {latestDec && (
          <>
            <span className="font-mono text-muted-foreground">
              {latestDec.phase} · R{latestDec.round}
            </span>
            <span className="text-muted-foreground">{labelOutcome(latestDec.outcome)}</span>
            {latestDec.tap_xy && (
              <span className="font-mono text-blue-600">
                tap ({latestDec.tap_xy[0]},{latestDec.tap_xy[1]})
              </span>
            )}
          </>
        )}
        <span className="ml-auto text-muted-foreground font-mono text-[10px]">
          {decisionId.slice(7) /* trim HHMMSS_iii_ prefix */ || '—'}
        </span>
      </div>
      <div className="flex-1 min-h-0 flex items-center justify-center bg-zinc-900">
        {!src ? (
          <div className="text-xs text-zinc-500">等待第一帧…</div>
        ) : imgError ? (
          <img
            src={`/api/decision/${encodeURIComponent(decisionId)}/image/input.jpg`}
            alt={`#${focused}`}
            className="max-w-full max-h-full object-contain"
            onError={() => {
              /* 两个都拿不到, 不再 retry */
            }}
          />
        ) : (
          <img
            src={src}
            alt={`#${focused}`}
            className="max-w-full max-h-full object-contain"
            onError={() => setImgError(true)}
          />
        )}
      </div>
    </div>
  )
}

function labelOutcome(o: string): string {
  if (!o) return ''
  if (o === 'tapped') return '已点击'
  if (o === 'lobby_confirmed_quad') return '大厅确认 (四元)'
  if (o === 'lobby_confirmed_legacy') return '大厅 (兜底)'
  if (o.startsWith('lobby_pending')) return o.replace('lobby_pending_', '大厅判定 ')
  if (o === 'no_target') return '无目标'
  if (o === 'loop_blocked') return '同坐标连击 → 拦下'
  if (o === 'login_timeout_fail') return '登录超时'
  if (o === 'dead_screen') return '死屏'
  if (o === 'phase_next') return '阶段完成'
  if (o === 'phase_done') return '全流程完成'
  if (o === 'phase_fail') return '阶段失败'
  if (o === 'game_restart') return '请求重启游戏'
  return o
}
