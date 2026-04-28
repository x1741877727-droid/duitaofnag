/**
 * OCR 调试 / ROI 校准页面.
 *
 * 三栏布局:
 *   左: ROI 列表 (从 config/roi.yaml 加载)
 *   中: 抓帧来源 + 画布 (帧 + ROI 红框 + 拖框) + 裁剪预览
 *   右: 当前 ROI 详情 (数字框 + scale + desc) + OCR 测试结果 + 保存
 *
 * 工作流:
 *   1. 选 ROI → 自动抓一帧 + 显示当前 ROI 红框
 *   2. 调 ROI: 数字框输入 / 画布上拖框 (二选一)
 *   3. 点 "测试 OCR" → 看裁剪后内容 + OCR 识别文字 + 置信度
 *   4. 满意 → 点 "保存" 写回 yaml + 备份
 */
import { useEffect, useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import { useAppStore } from '@/lib/store'
import {
  fetchRoiList, saveRoi, testRoiOcr,
  type RoiItem, type OcrHit, type Preprocessing,
} from '@/lib/roiApi'
import { RoiCanvas } from './RoiCanvas'

const PREPROC_OPTIONS: { key: Preprocessing; label: string; hint: string }[] = [
  { key: 'grayscale', label: '灰度化', hint: '消色彩噪声, 字纯色场景有用' },
  { key: 'clahe',     label: '增强对比', hint: 'CLAHE 增强, 暗字 / 半透明字最有效' },
  { key: 'sharpen',   label: '锐化',     hint: '边缘锐化, 抗锯齿模糊救字符边界' },
  { key: 'binarize',  label: '二值化',   hint: 'Otsu 黑白, 极端但纯文字识别可飙升' },
  { key: 'invert',    label: '反相',     hint: '白底黑字 ↔ 黑底白字, 看 OCR 偏好' },
]

type FrameSrc = { kind: 'instance'; idx: number } | { kind: 'decision'; session: string; id: string } | null

export function OcrTuner() {
  const emulators = useAppStore((s) => s.emulators)
  const [items, setItems] = useState<RoiItem[]>([])
  const [yamlPath, setYamlPath] = useState('')
  const [selectedName, setSelectedName] = useState<string>('')

  // 编辑中的字段 (跟选中的 RoiItem 解耦, 用户改了不立即保存)
  const [editRect, setEditRect] = useState<[number, number, number, number]>([0, 0, 0, 0])
  const [editScale, setEditScale] = useState(1)
  const [editDesc, setEditDesc] = useState('')
  const [editPreproc, setEditPreproc] = useState<Preprocessing[]>([])

  // 抓帧来源 + 抓回的图
  const [src, setSrc] = useState<FrameSrc>(null)
  const [fullImg, setFullImg] = useState<string>('')
  const [croppedImg, setCroppedImg] = useState<string>('')
  const [imgSize, setImgSize] = useState<[number, number]>([1280, 720])
  const [ocrHits, setOcrHits] = useState<OcrHit[]>([])
  const [busy, setBusy] = useState(false)
  const [hint, setHint] = useState('')

  // 默认选第 1 个能用的实例
  const availInsts = useMemo(() => emulators.filter((e) => e.running), [emulators])
  useEffect(() => {
    if (!src && availInsts.length > 0) {
      setSrc({ kind: 'instance', idx: availInsts[0].index })
    }
  }, [availInsts])

  // 加载 ROI 列表
  async function loadList() {
    try {
      const r = await fetchRoiList()
      setItems(r.items)
      setYamlPath(r.yaml_path)
      if (!selectedName && r.items.length > 0) {
        selectRoi(r.items[0])
      }
    } catch (e: any) {
      setHint(`加载 roi.yaml 失败: ${e?.message || e}`)
    }
  }

  useEffect(() => { loadList() }, [])

  function selectRoi(it: RoiItem) {
    setSelectedName(it.name)
    setEditRect(it.rect)
    setEditScale(it.scale)
    setEditDesc(it.desc)
    // 从 yaml 读已保存的预处理 (会被 RoiItem.preprocessing 带回来)
    const savedPrep = (it.preprocessing || []).filter(
      (p): p is Preprocessing => ['grayscale', 'clahe', 'binarize', 'sharpen', 'invert'].includes(p)
    )
    setEditPreproc(savedPrep)
    setOcrHits([])             // 切 ROI 清掉旧的 OCR 结果
    setCroppedImg('')
    setHint('')
  }

  function togglePreproc(key: Preprocessing) {
    setEditPreproc((cur) => cur.includes(key) ? cur.filter((k) => k !== key) : [...cur, key])
  }

  async function grabFrame() {
    if (!src) {
      setHint('先选实例')
      return
    }
    setBusy(true)
    setHint('抓帧中…')
    try {
      // 抓帧通过 test_ocr 接口顺便拿全图 (省一个端点)
      const r = await testRoiOcr({
        ...(src.kind === 'instance' ? { instance: src.idx } : { decision_id: src.id, session: src.session }),
        rect: editRect,
        scale: editScale,
        preprocessing: editPreproc.length > 0 ? editPreproc : undefined,
      })
      setFullImg(r.full_image_b64)
      setCroppedImg(r.cropped_image_b64)
      setImgSize(r.source_size)
      setOcrHits(r.ocr_results)
      setHint(`OK: ${r.source_size[0]}×${r.source_size[1]}, OCR ${r.n_texts} 个文字`)
    } catch (e: any) {
      setHint(`抓帧/OCR 失败: ${(e?.message || e).toString().slice(0, 100)}`)
    } finally {
      setBusy(false)
    }
  }

  async function runTestOcr() {
    if (!fullImg) {
      // 还没抓帧 → 先抓
      await grabFrame()
      return
    }
    if (!src) return
    setBusy(true)
    setHint('OCR 中…')
    try {
      const r = await testRoiOcr({
        ...(src.kind === 'instance' ? { instance: src.idx } : { decision_id: src.id, session: src.session }),
        rect: editRect,
        scale: editScale,
        preprocessing: editPreproc.length > 0 ? editPreproc : undefined,
      })
      setFullImg(r.full_image_b64)
      setCroppedImg(r.cropped_image_b64)
      setImgSize(r.source_size)
      setOcrHits(r.ocr_results)
      setHint(`OCR ${r.n_texts} 个文字`)
    } catch (e: any) {
      setHint(`失败: ${(e?.message || e).toString().slice(0, 100)}`)
    } finally {
      setBusy(false)
    }
  }

  async function doSave() {
    if (!selectedName) return
    setBusy(true)
    setHint('保存中…')
    try {
      const r = await saveRoi({
        name: selectedName,
        rect: editRect,
        scale: editScale,
        desc: editDesc,
        preprocessing: editPreproc,   // 空数组也写, 用户显式清空
      })
      setHint(`已保存, 备份 ${r.backup}`)
      await loadList()  // 刷新列表
    } catch (e: any) {
      setHint(`保存失败: ${(e?.message || e).toString().slice(0, 200)}`)
    } finally {
      setBusy(false)
    }
  }

  function resetToOriginal() {
    const it = items.find((x) => x.name === selectedName)
    if (it) selectRoi(it)
  }

  // ROI 数字编辑 — 0.001 步进, 输入受限 0-1
  function setRectPart(idx: 0 | 1 | 2 | 3, v: number) {
    const next = [...editRect] as [number, number, number, number]
    next[idx] = Math.max(0, Math.min(1, v))
    // 保证 x1<x2, y1<y2
    if (idx === 0 && next[0] >= next[2]) next[2] = Math.min(1, next[0] + 0.01)
    if (idx === 2 && next[2] <= next[0]) next[0] = Math.max(0, next[2] - 0.01)
    if (idx === 1 && next[1] >= next[3]) next[3] = Math.min(1, next[1] + 0.01)
    if (idx === 3 && next[3] <= next[1]) next[1] = Math.max(0, next[3] - 0.01)
    setEditRect(next)
  }

  const [iw, ih] = imgSize
  const px = {
    x1: Math.round(editRect[0] * iw),
    y1: Math.round(editRect[1] * ih),
    x2: Math.round(editRect[2] * iw),
    y2: Math.round(editRect[3] * ih),
    w: Math.round((editRect[2] - editRect[0]) * iw),
    h: Math.round((editRect[3] - editRect[1]) * ih),
  }

  return (
    <div className="grid gap-3 h-full min-h-0" style={{ gridTemplateColumns: '220px 1fr 320px' }}>
      {/* 左: ROI 列表 */}
      <div className="rounded-lg border border-border bg-card overflow-y-auto">
        <div className="sticky top-0 bg-card z-10 p-2 border-b border-border">
          <div className="text-xs font-semibold">ROI 列表 ({items.length})</div>
          <div className="text-[10px] text-muted-foreground truncate" title={yamlPath}>
            {yamlPath || '加载中…'}
          </div>
        </div>
        <ul className="divide-y divide-border text-[11px]">
          {items.map((it) => (
            <li key={it.name}>
              <button
                onClick={() => selectRoi(it)}
                className={`w-full text-left px-2 py-1.5 ${
                  selectedName === it.name ? 'bg-info/10 text-info font-semibold' : 'hover:bg-muted'
                }`}
              >
                <div className="font-mono truncate">{it.name}</div>
                <div className="text-muted-foreground text-[10px] truncate">{it.desc || '—'}</div>
              </button>
            </li>
          ))}
        </ul>
      </div>

      {/* 中: 画布 */}
      <div className="flex flex-col gap-2 min-h-0">
        {/* 来源选择 */}
        <div className="flex items-center gap-2 text-xs flex-wrap">
          <span className="font-semibold">来源:</span>
          {availInsts.map((e) => (
            <Button
              key={e.index}
              variant={src?.kind === 'instance' && src.idx === e.index ? 'secondary' : 'outline'}
              size="sm"
              onClick={() => setSrc({ kind: 'instance', idx: e.index })}
            >
              #{e.index}
            </Button>
          ))}
          {availInsts.length === 0 && (
            <span className="text-muted-foreground">没运行的实例</span>
          )}
          <Button size="sm" onClick={grabFrame} disabled={busy || !src} className="ml-auto">
            {busy ? '抓帧中…' : '抓帧'}
          </Button>
        </div>

        {/* 画布 (帧 + ROI 框 + OCR 框) */}
        <div className="flex-1 min-h-0 rounded-lg border border-border overflow-hidden bg-foreground">
          <RoiCanvas
            imageSrc={fullImg}
            imageSize={imgSize}
            rect={editRect}
            onRectChange={setEditRect}
            ocrHits={ocrHits}
            className="w-full h-full"
          />
        </div>

        {/* 裁剪预览 */}
        {croppedImg && (
          <div className="rounded-lg border border-border bg-card p-2">
            <div className="text-[10px] text-muted-foreground mb-1">
              ROI 裁剪 (放大 ×{editScale}):
            </div>
            <img
              src={croppedImg}
              alt="cropped"
              className="max-w-full max-h-[180px] object-contain"
            />
          </div>
        )}

        {hint && (
          <div className="text-[11px] text-muted-foreground">{hint}</div>
        )}
      </div>

      {/* 右: 操作面板 */}
      <div className="rounded-lg border border-border bg-card p-3 space-y-3 overflow-y-auto">
        {!selectedName ? (
          <div className="text-xs text-muted-foreground">从左侧选一个 ROI</div>
        ) : (
          <>
            <div>
              <div className="font-mono text-sm font-semibold">{selectedName}</div>
              <input
                type="text"
                value={editDesc}
                onChange={(e) => setEditDesc(e.target.value)}
                placeholder="描述 (可选)"
                className="mt-1 w-full px-2 py-1 border border-border rounded text-xs"
              />
              <div className="text-[10px] text-muted-foreground mt-1">
                用于: <span className="font-mono">{items.find(i => i.name === selectedName)?.used_in || '—'}</span>
              </div>
            </div>

            {/* 坐标编辑 */}
            <div>
              <div className="text-xs font-semibold mb-1">坐标 (归一化 0-1)</div>
              <div className="grid grid-cols-2 gap-2 text-xs">
                {(['x1', 'y1', 'x2', 'y2'] as const).map((k, idx) => (
                  <label key={k} className="flex items-center gap-1.5">
                    <span className="font-mono w-6">{k}</span>
                    <input
                      type="number"
                      min={0}
                      max={1}
                      step={0.005}
                      value={editRect[idx].toFixed(3)}
                      onChange={(e) => setRectPart(idx as 0 | 1 | 2 | 3, parseFloat(e.target.value) || 0)}
                      className="flex-1 px-1 py-0.5 border border-border rounded font-mono text-center"
                    />
                  </label>
                ))}
              </div>
              <div className="mt-1 text-[10px] text-muted-foreground font-mono">
                像素: {px.x1},{px.y1} → {px.x2},{px.y2} ({px.w}×{px.h})
              </div>
              <div className="text-[10px] text-muted-foreground">
                提示: 在画布上左键拖动也能画框
              </div>
            </div>

            {/* OCR 放大倍数 */}
            <div>
              <div className="text-xs font-semibold mb-1">
                OCR 放大倍数: {editScale}×
              </div>
              <input
                type="range"
                min={1}
                max={5}
                step={1}
                value={editScale}
                onChange={(e) => setEditScale(parseInt(e.target.value))}
                className="w-full"
              />
              <div className="text-[10px] text-muted-foreground">
                小文字必须放大. 默认 2-3, 极小字试 4-5. 大字反而别放大.
              </div>
            </div>

            {/* 图像预处理 */}
            <div>
              <div className="text-xs font-semibold mb-1">
                图像预处理 ({editPreproc.length})
              </div>
              <div className="space-y-1">
                {PREPROC_OPTIONS.map((opt) => {
                  const checked = editPreproc.includes(opt.key)
                  return (
                    <label
                      key={opt.key}
                      className={`flex items-start gap-2 px-1.5 py-1 rounded cursor-pointer text-[11px] ${
                        checked ? 'bg-info/10' : 'hover:bg-muted'
                      }`}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => togglePreproc(opt.key)}
                        className="mt-0.5"
                      />
                      <div className="flex-1">
                        <div className="font-semibold">{opt.label}</div>
                        <div className="text-muted-foreground text-[10px] leading-snug">{opt.hint}</div>
                      </div>
                    </label>
                  )
                })}
              </div>
              <div className="text-[10px] text-muted-foreground mt-1 leading-relaxed">
                按勾选顺序应用. 调出最优组合后告我, 我加进生产 OCR 代码.
              </div>
            </div>

            {/* OCR 结果 */}
            <div>
              <div className="text-xs font-semibold mb-1 flex items-center justify-between">
                <span>OCR 识别 ({ocrHits.length})</span>
                <Button size="sm" variant="outline" onClick={runTestOcr} disabled={busy || !src}>
                  {busy ? '...' : '测试 OCR'}
                </Button>
              </div>
              <ul className="space-y-1 max-h-[200px] overflow-y-auto text-[11px]">
                {ocrHits.map((h, i) => (
                  <li key={i} className="flex items-center gap-2 font-mono">
                    <span className="text-success">✓</span>
                    <span className="flex-1 truncate" title={h.text}>{h.text}</span>
                    <span className="text-muted-foreground tabular-nums">
                      {(h.conf * 100).toFixed(0)}%
                    </span>
                  </li>
                ))}
                {ocrHits.length === 0 && (
                  <li className="text-muted-foreground">暂无结果, 点 "测试 OCR"</li>
                )}
              </ul>
            </div>

            {/* 操作 */}
            <div className="space-y-1 pt-2 border-t border-border">
              <Button onClick={doSave} disabled={busy} className="w-full" size="sm">
                {busy ? '保存中…' : '保存修改'}
              </Button>
              <Button onClick={resetToOriginal} disabled={busy} variant="ghost" size="sm" className="w-full">
                恢复 (撤销改动)
              </Button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
