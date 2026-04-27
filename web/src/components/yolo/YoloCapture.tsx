/**
 * YOLO 采集 tab — 从实例抓帧存到训练数据集.
 *
 * 选实例 + tag → 抓帧 → 存到 raw_screenshots/{tag}_inst{N}_{ts}.png.
 * 不依赖 runner. 也支持 "连拍" (隔 N 秒抓一张, 持续 M 张).
 */
import { useEffect, useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useAppStore } from '@/lib/store'
import { captureFromInstance, datasetImgSrc } from '@/lib/yoloApi'

export function YoloCapture() {
  const instances = useAppStore((s) => s.instances)
  const emulators = useAppStore((s) => s.emulators)
  const availableInstances = useMemo(() => {
    const ids = new Set<number>()
    for (const e of emulators) ids.add(e.index)
    for (const i of Object.values(instances)) ids.add(i.index)
    return Array.from(ids).sort((a, b) => a - b).map((idx) => ({
      index: idx,
      running: emulators.find((e) => e.index === idx)?.running ?? false,
    }))
  }, [instances, emulators])

  const [tag, setTag] = useState('manual')
  const [inst, setInst] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [recent, setRecent] = useState<{ name: string; ts: number; w: number; h: number }[]>([])

  // 连拍
  const [burstN, setBurstN] = useState(5)
  const [burstSec, setBurstSec] = useState(2)
  const [bursting, setBursting] = useState(false)
  const [burstProg, setBurstProg] = useState(0)

  async function captureOne(): Promise<string | null> {
    setErr('')
    const tgt = inst ?? availableInstances.find((e) => e.running)?.index ?? availableInstances[0]?.index
    if (tgt === undefined) {
      setErr('没选实例')
      return null
    }
    try {
      const r = await captureFromInstance(tgt, tag.trim() || 'manual')
      setRecent((rs) => [{ name: r.name, ts: Date.now(), w: r.width, h: r.height }, ...rs].slice(0, 12))
      return r.name
    } catch (e) {
      setErr(String(e))
      return null
    }
  }

  async function onSingle() {
    setBusy(true)
    await captureOne()
    setBusy(false)
  }

  async function onBurst() {
    if (bursting) return
    setBursting(true)
    setBurstProg(0)
    for (let i = 0; i < burstN; i++) {
      const n = await captureOne()
      setBurstProg(i + 1)
      if (!n) break
      if (i < burstN - 1) await new Promise((res) => setTimeout(res, burstSec * 1000))
    }
    setBursting(false)
  }

  return (
    <div className="grid gap-3 h-full" style={{ gridTemplateColumns: '320px 1fr' }}>
      <div className="rounded-lg border border-border bg-card p-3 space-y-3 overflow-y-auto">
        <div className="text-xs font-semibold">从实例抓帧</div>

        <div className="space-y-1.5">
          <div className="text-[11px] font-semibold">选实例</div>
          <div className="flex flex-wrap gap-1">
            {availableInstances.map((e) => (
              <Button
                key={e.index}
                variant={inst === e.index ? 'secondary' : 'outline'}
                size="sm"
                onClick={() => setInst(e.index)}
                className={!e.running ? 'opacity-60' : ''}
                title={e.running ? '运行中' : '模拟器未启动'}
              >
                #{e.index}{e.running ? '' : '·停'}
              </Button>
            ))}
            {availableInstances.length === 0 && (
              <div className="text-[11px] text-muted-foreground">没检测到模拟器</div>
            )}
          </div>
        </div>

        <div>
          <div className="text-[11px] font-semibold mb-1">标签 (文件名前缀)</div>
          <Input
            value={tag}
            onChange={(e) => setTag(e.target.value)}
            placeholder="如: lobby_popup / dialog / activity"
            className="h-8 text-xs"
          />
          <div className="text-[10px] text-muted-foreground mt-0.5">
            文件名: <span className="font-mono">{tag.trim() || 'manual'}_inst{`{N}`}_{`{ts}`}.png</span>
          </div>
        </div>

        <Button
          size="sm"
          disabled={busy || bursting}
          onClick={onSingle}
          className="w-full"
        >
          {busy ? '抓中…' : '抓 1 张'}
        </Button>

        <div className="border-t border-border pt-2">
          <div className="text-[11px] font-semibold mb-1">连拍 (定时)</div>
          <div className="flex items-center gap-2 text-[11px]">
            <span>张数:</span>
            <Input
              type="number" min={1} max={100}
              value={burstN}
              onChange={(e) => setBurstN(Math.max(1, Math.min(100, Number(e.target.value) || 5)))}
              className="w-14 h-7 text-xs"
            />
            <span>每隔</span>
            <Input
              type="number" min={1} max={60} step={0.5}
              value={burstSec}
              onChange={(e) => setBurstSec(Math.max(0.5, Number(e.target.value) || 2))}
              className="w-14 h-7 text-xs"
            />
            <span>秒</span>
          </div>
          <Button
            size="sm"
            variant="outline"
            disabled={bursting}
            onClick={onBurst}
            className="w-full mt-1"
          >
            {bursting ? `连拍中… ${burstProg}/${burstN}` : `连拍 ${burstN} 张`}
          </Button>
          <div className="text-[10px] text-muted-foreground mt-1">
            采集多样化画面: 跑游戏到不同状态 (大厅/弹窗/对话/活动) 各连拍一波
          </div>
        </div>

        {err && <div className="text-[11px] text-destructive bg-destructive/10 p-2 rounded">{err}</div>}
      </div>

      {/* 右: 最近 */}
      <div className="rounded-lg border border-border bg-card p-3 overflow-y-auto">
        <div className="text-xs font-semibold mb-2">本次会话已采集 ({recent.length})</div>
        {recent.length === 0 ? (
          <div className="text-xs text-muted-foreground text-center py-8">
            点「抓 1 张」开始; 抓完去「数据集」/「标注」处理.
          </div>
        ) : (
          <div className="grid gap-2"
               style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))' }}>
            {recent.map((r) => (
              <div key={r.name} className="rounded border border-border p-1">
                <div className="bg-muted rounded h-24 overflow-hidden">
                  <img
                    src={datasetImgSrc(r.name)}
                    alt={r.name}
                    className="w-full h-full object-cover"
                  />
                </div>
                <div className="text-[10px] font-mono truncate mt-0.5" title={r.name}>{r.name}</div>
                <div className="text-[10px] text-muted-foreground">
                  {r.w}×{r.h}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
