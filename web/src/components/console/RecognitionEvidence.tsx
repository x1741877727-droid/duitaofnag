/**
 * 识别证据 — 右侧, 5 层 Tier 卡片.
 *
 * 数据源: 拉聚焦实例最新决策的完整 JSON (/api/decision/{id}/data)
 * 5 层 Tier: 模板 / 记忆 / YOLO / 文字 / 视觉模型
 *
 * 每张卡显示:
 *   - 层名 + 是否拍板 (early_exit ✓ / 排除 ○)
 *   - 耗时
 *   - note (一行说明)
 *   - 模板层: 列出试过的 templates (name + score + hit?)
 *   - YOLO 层: 列出 detections (cls + conf)
 *   - 记忆层: phash + hit
 */
import { useEffect, useState } from 'react'
import { useAppStore } from '@/lib/store'

interface TierData {
  tier: number
  name: string
  duration_ms: number
  early_exit: boolean
  note: string
  templates?: Array<{ name: string; score: number; threshold: number; hit: boolean }>
  yolo_detections?: Array<{ cls: string; conf: number; bbox: number[] }>
  ocr_hits?: Array<{ text: string; conf: number }>
  memory_phash_query?: string
  memory_hit?: { cx: number; cy: number; note?: string } | null
}

interface DecisionData {
  id: string
  outcome: string
  tiers: TierData[]
}

export function RecognitionEvidence({ instanceIdx }: { instanceIdx?: number } = {}) {
  const focusedFromStore = useAppStore((s) => s.focusedInstance)
  const liveDecisions = useAppStore((s) => s.liveDecisions)
  const focused = instanceIdx !== undefined ? instanceIdx : focusedFromStore

  const latest = focused !== null ? (liveDecisions[focused] || []).slice(-1)[0] : null
  const decisionId = latest?.id || ''

  const [data, setData] = useState<DecisionData | null>(null)

  useEffect(() => {
    if (!decisionId) {
      setData(null)
      return
    }
    let cancelled = false
    fetch(`/api/decision/${encodeURIComponent(decisionId)}/data`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((j: DecisionData) => {
        if (!cancelled) setData(j)
      })
      .catch(() => {
        if (!cancelled) setData(null)
      })
    return () => {
      cancelled = true
    }
  }, [decisionId])

  if (focused === null) {
    return (
      <div className="rounded-lg border border-dashed border-border bg-card p-3 text-xs text-muted-foreground">
        识别证据 — 选个实例看
      </div>
    )
  }

  if (!data) {
    return (
      <div className="rounded-lg border border-border bg-card p-3 text-xs text-muted-foreground">
        识别证据 — 加载中…
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-border bg-card flex flex-col overflow-hidden">
      <div className="px-3 py-2 border-b border-border flex items-center justify-between text-xs">
        <span className="font-semibold">识别证据</span>
        <span className="font-mono text-muted-foreground">{data.outcome || '—'}</span>
      </div>
      <div className="flex-1 overflow-y-auto p-2 space-y-2">
        {(data.tiers || []).map((t, i) => (
          <TierCard key={`${t.tier}-${i}`} t={t} />
        ))}
      </div>
    </div>
  )
}

function TierCard({ t }: { t: TierData }) {
  const winner = t.early_exit
  return (
    <div
      className={`rounded border p-2 text-xs ${
        winner
          ? 'border-success/30 bg-success-muted'
          : 'border-border bg-muted/40'
      }`}
    >
      <div className="flex items-center gap-2">
        <span
          className={`inline-block w-1.5 h-1.5 rounded-full ${
            winner ? 'bg-success' : 'bg-muted-foreground/30'
          }`}
        />
        <span className="font-semibold">{t.name}</span>
        {winner && <span className="text-success text-[10px] font-bold">[赢]</span>}
        <span className="ml-auto font-mono text-[10px] text-muted-foreground">
          {t.duration_ms.toFixed(1)}ms
        </span>
      </div>
      {t.note && <div className="mt-1 text-[11px] text-muted-foreground">{t.note}</div>}

      {/* 模板层细节 */}
      {t.templates && t.templates.length > 0 && (
        <ul className="mt-1.5 space-y-0.5">
          {t.templates.map((tp, idx) => (
            <li key={idx} className="flex items-center gap-2 font-mono text-[10px]">
              <span className={tp.hit ? 'text-success' : 'text-muted-foreground'}>
                {tp.hit ? '✓' : '·'}
              </span>
              <span className="truncate flex-1">{tp.name}</span>
              <span className="text-muted-foreground">{tp.score.toFixed(2)}</span>
            </li>
          ))}
        </ul>
      )}

      {/* YOLO 检测 */}
      {t.yolo_detections && t.yolo_detections.length > 0 && (
        <ul className="mt-1.5 space-y-0.5">
          {t.yolo_detections.slice(0, 6).map((d, idx) => (
            <li key={idx} className="flex items-center gap-2 font-mono text-[10px]">
              <span className="text-info">▣</span>
              <span className="truncate flex-1">{d.cls}</span>
              <span className="text-muted-foreground">{d.conf.toFixed(2)}</span>
            </li>
          ))}
          {t.yolo_detections.length > 6 && (
            <li className="text-[10px] text-muted-foreground ml-3">
              ... 还有 {t.yolo_detections.length - 6} 个
            </li>
          )}
        </ul>
      )}

      {/* 记忆层 */}
      {t.memory_hit && (
        <div className="mt-1.5 font-mono text-[10px] text-warning">
          ◆ phash {t.memory_phash_query || ''} → ({t.memory_hit.cx},{t.memory_hit.cy})
        </div>
      )}
    </div>
  )
}
