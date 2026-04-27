/**
 * YOLO 模型 tab — 当前模型信息 + 上传新 ONNX 覆盖 latest.onnx.
 */
import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  fetchYoloInfo, uploadModel, type YoloInfo,
  fetchLabelClasses, addLabelClass,
} from '@/lib/yoloApi'

export function YoloModel() {
  const [info, setInfo] = useState<YoloInfo | null>(null)
  const [uploading, setUploading] = useState(false)
  const [msg, setMsg] = useState('')
  const [tick, setTick] = useState(0)
  // 类别管理
  const [labelClasses, setLabelClasses] = useState<string[]>([])
  const [legacyCids, setLegacyCids] = useState<number[]>([])
  const [newCls, setNewCls] = useState('')
  const [adding, setAdding] = useState(false)
  const [clsMsg, setClsMsg] = useState('')

  useEffect(() => {
    fetchYoloInfo().then(setInfo).catch(() => setInfo(null))
    fetchLabelClasses().then((r) => {
      setLabelClasses(r.classes)
      setLegacyCids(r.legacy_cids || [])
    }).catch(() => {})
  }, [tick])

  async function onAddClass() {
    const n = newCls.trim()
    if (!n) return
    setAdding(true)
    setClsMsg('')
    try {
      const r = await addLabelClass(n)
      setLabelClasses(r.classes)
      setNewCls('')
      setClsMsg(`✓ 已添加 [${r.new_id}] ${n}`)
    } catch (e) {
      setClsMsg('失败: ' + String(e))
    } finally {
      setAdding(false)
    }
  }

  async function onUpload(f: File) {
    setUploading(true)
    setMsg('')
    try {
      const r = await uploadModel(f)
      setMsg(`✓ 已保存 ${(r.size / 1024 / 1024).toFixed(2)} MB → ${r.latest}`)
      setTimeout(() => setTick((x) => x + 1), 500)
    } catch (e) {
      setMsg('上传失败: ' + String(e))
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="grid gap-3 h-full overflow-y-auto" style={{ gridTemplateColumns: '1fr 1fr' }}>
      <div className="rounded-lg border border-border bg-card p-4 space-y-3 overflow-y-auto">
        <div className="text-sm font-semibold">当前模型</div>
        {!info ? (
          <div className="text-xs text-muted-foreground">加载中…</div>
        ) : (
          <div className="space-y-2 text-xs">
            <div className="flex items-center gap-2">
              <span>状态:</span>
              <span className={`font-bold ${info.available ? 'text-emerald-700' : 'text-red-700'}`}>
                {info.available ? '✓ 已加载' : '✗ 未加载'}
              </span>
            </div>
            <div>
              <div className="text-[11px] text-muted-foreground">模型路径:</div>
              <div className="font-mono text-[11px] break-all bg-zinc-50 p-2 rounded border">
                {info.model_path || '—'}
              </div>
            </div>
            <div>类别 ({info.classes.length}):</div>
            <ul className="space-y-0.5">
              {info.classes.map((c, i) => (
                <li key={c} className="font-mono text-[11px]">
                  <span className="text-muted-foreground">[{i}]</span> {c}
                </li>
              ))}
            </ul>
            <div>输入尺寸: <span className="font-mono">{info.input_size}×{info.input_size}</span></div>
            {info.error && (
              <div className="text-[11px] text-red-700 bg-red-50 p-2 rounded">{info.error}</div>
            )}
          </div>
        )}
      </div>

      <div className="rounded-lg border border-border bg-card p-4 space-y-3">
        <div className="text-sm font-semibold">上传新模型</div>
        <div className="text-[11px] text-muted-foreground space-y-1">
          <div>训练完的 ONNX → 上传 → 自动覆盖 <span className="font-mono">latest.onnx</span></div>
          <div>下次主 runner 重启 + YOLO 测试会用新模型</div>
          <div className="text-amber-700">⚠ 上传后会立即覆盖生产模型</div>
        </div>
        <label className="block">
          <input
            type="file"
            accept=".onnx"
            disabled={uploading}
            onChange={(e) => {
              const f = e.currentTarget.files?.[0]
              if (f) onUpload(f)
              e.currentTarget.value = ''
            }}
            className="block w-full text-xs"
          />
        </label>
        {uploading && (
          <div className="text-[11px] text-muted-foreground">上传中…</div>
        )}
        {msg && (
          <div className={`text-[11px] p-2 rounded ${
            msg.startsWith('✓') ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-700'
          }`}>
            {msg}
          </div>
        )}
        <div className="border-t border-border pt-3">
          <div className="text-xs font-semibold mb-1">训练流程参考</div>
          <ol className="text-[11px] text-muted-foreground space-y-0.5 list-decimal pl-4">
            <li>「采集」连拍 100+ 张多样化画面</li>
            <li>「标注」给每张画框标 (类别可在右侧添加)</li>
            <li>「数据集」下载 zip → mac 上跑 YOLOv8 训练</li>
            <li>训练完导出 ONNX → 这里上传</li>
          </ol>
        </div>
      </div>

      {/* 类别管理 (跨列, 全宽) */}
      <div className="rounded-lg border border-border bg-card p-4 space-y-3 col-span-2">
        <div className="text-sm font-semibold">类别管理 (classes.txt)</div>
        <div className="text-[11px] text-muted-foreground">
          添加新类不影响旧 cid (新类追加, id = 当前类数). 旧标注文件保持不变.
        </div>
        <div className="flex flex-wrap gap-1.5">
          {labelClasses.map((c, i) => (
            <span
              key={c}
              className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md border border-border bg-secondary text-xs"
            >
              <span className="font-mono text-muted-foreground">[{i}]</span>
              <span className="font-medium">{c}</span>
            </span>
          ))}
          {legacyCids.length > 0 && legacyCids.map((cid) => (
            <span
              key={cid}
              className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md border border-warning/40 bg-warning-muted text-xs text-warning"
              title="历史标注用过这个 cid 但不在 classes.txt 里, 需手动决定保留/清理"
            >
              <span className="font-mono">[{cid}]</span>
              <span>历史</span>
            </span>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <Input
            placeholder="新类名 (a-z 0-9 _, 1-32 字符)"
            value={newCls}
            onChange={(e) => setNewCls(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && onAddClass()}
            className="h-8 text-xs max-w-[280px]"
          />
          <Button size="sm" onClick={onAddClass} disabled={adding || !newCls.trim()}>
            + 新增类别
          </Button>
          {clsMsg && (
            <span className={`text-[11px] ${clsMsg.startsWith('✓') ? 'text-success' : 'text-destructive'}`}>
              {clsMsg}
            </span>
          )}
        </div>
      </div>
    </div>
  )
}
