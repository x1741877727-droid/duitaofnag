/**
 * YOLO 测试 tab — 选实例 (无需 runner) → 抓帧 → 跑推理 → 标注图.
 */
import { useEffect, useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import { useAppStore } from '@/lib/store'
import { fetchYoloInfo, runYoloTest, type YoloInfo, type YoloTestResult } from '@/lib/yoloApi'

export function YoloTester() {
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

  const [info, setInfo] = useState<YoloInfo | null>(null)
  const [conf, setConf] = useState(0.4)
  const [inst, setInst] = useState<number | null>(null)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<YoloTestResult | null>(null)

  useEffect(() => {
    fetchYoloInfo().then(setInfo).catch(() => setInfo(null))
  }, [])

  async function runTest() {
    setRunning(true)
    setResult(null)
    try {
      const tgt = inst ?? availableInstances.find((e) => e.running)?.index ?? availableInstances[0]?.index ?? 0
      const r = await runYoloTest({ instance: tgt, conf_thr: conf })
      setResult(r)
    } catch (e) {
      setResult({
        ok: false, source: '', source_image_size: [0, 0],
        conf_thr: conf, duration_ms: 0,
        detections: [], annotated_b64: '', error: String(e),
      })
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="grid gap-3 h-full" style={{ gridTemplateColumns: '320px 1fr' }}>
      <div className="rounded-lg border border-border bg-card p-3 space-y-3 overflow-y-auto">
        <div className="text-xs font-semibold">YOLO 模型状态</div>
        <div className="rounded border border-border bg-muted p-2 text-[11px] space-y-0.5">
          {!info ? (
            <div className="text-muted-foreground">加载中…</div>
          ) : (
            <>
              <div>
                可用:{' '}
                <span className={info.available ? 'text-success font-bold' : 'text-destructive font-bold'}>
                  {info.available ? '✓' : '✗'}
                </span>
              </div>
              <div className="font-mono break-all text-[10px]">{info.model_path || '—'}</div>
              <div>类别 ({info.classes.length}): {info.classes.join(', ') || '—'}</div>
              <div>输入尺寸: {info.input_size}</div>
              {info.error && <div className="text-destructive">{info.error}</div>}
            </>
          )}
        </div>

        <div className="space-y-1.5">
          <div className="text-xs font-semibold">选实例 (无需启动 runner)</div>
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
          </div>
          <div className="flex items-center gap-2 text-[11px]">
            <span>置信度阈值:</span>
            <input
              type="number"
              step="0.05"
              min="0"
              max="1"
              value={conf}
              onChange={(e) => setConf(Number(e.target.value) || 0.4)}
              className="w-16 h-7 px-1 border border-border rounded text-xs font-mono"
            />
          </div>
          <Button
            size="sm"
            disabled={running || !info?.available}
            onClick={runTest}
            className="w-full"
          >
            {running ? '推理中…' : '抓帧跑 YOLO'}
          </Button>
        </div>

        {result && (
          <div className="space-y-1 pt-2 border-t border-border">
            {!result.ok ? (
              <div className="text-[11px] text-destructive">{result.error || '失败'}</div>
            ) : (
              <>
                <div className="text-xs font-semibold">
                  {result.detections.length} 个检测 ·{' '}
                  <span className="font-mono">{result.duration_ms.toFixed(1)}ms</span>
                </div>
                {result.detections.length > 0 ? (
                  <ul className="space-y-0.5 max-h-[260px] overflow-y-auto">
                    {result.detections.map((d, i) => (
                      <li key={i} className="font-mono text-[10px] flex items-center gap-2">
                        <span className="text-info">▣</span>
                        <span className="flex-1">{d.name}</span>
                        <span className="text-muted-foreground">{d.score.toFixed(3)}</span>
                        <span className="text-muted-foreground">@({d.cx},{d.cy})</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <div className="text-[11px] text-muted-foreground">画面无 close_x / action_btn</div>
                )}
              </>
            )}
          </div>
        )}
      </div>

      <div className="rounded-lg border border-border bg-card flex items-center justify-center overflow-hidden">
        {result?.annotated_b64 ? (
          <img src={result.annotated_b64} alt="标注" className="max-w-full max-h-full object-contain" />
        ) : (
          <div className="text-sm text-muted-foreground">点「抓帧跑 YOLO」看实时检测</div>
        )}
      </div>
    </div>
  )
}
