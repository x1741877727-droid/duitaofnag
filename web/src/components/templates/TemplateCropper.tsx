/**
 * 截图裁剪 — 弹层.
 *
 * 三种源:
 *   1. 上传图片 (input file)
 *   2. 实例当前帧 (从 /api/screenshot/{idx} 拉)
 *   3. 历史决策 input.jpg (从 /api/decision/{id}/image/input.jpg)
 *
 * 自实现拖框 (mousedown / mousemove / mouseup), 输出 bbox = 源图坐标.
 */
import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { uploadTemplate } from '@/lib/templatesApi'
import { useAppStore } from '@/lib/store'

interface CropperProps {
  open: boolean
  onClose: () => void
  onSaved?: (name: string) => void
  initialUrl?: string                       // 直接喂图 URL (来自决策档案 / 实例帧)
  initialBlob?: Blob | null
}

export function TemplateCropper({ open, onClose, onSaved, initialUrl, initialBlob }: CropperProps) {
  const instances = useAppStore((s) => s.instances)
  const focusedInstance = useAppStore((s) => s.focusedInstance)

  const [imgUrl, setImgUrl] = useState<string>('')
  const [imgBlob, setImgBlob] = useState<Blob | null>(null)
  const [naturalW, setNaturalW] = useState(0)
  const [naturalH, setNaturalH] = useState(0)
  const [name, setName] = useState('')
  const [overwrite, setOverwrite] = useState(false)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')

  // 框选 state — 全部用源图自然坐标
  const [box, setBox] = useState<{ x: number; y: number; w: number; h: number } | null>(null)
  const [drag, setDrag] = useState<{ startX: number; startY: number } | null>(null)

  const containerRef = useRef<HTMLDivElement | null>(null)
  const imgRef = useRef<HTMLImageElement | null>(null)

  useEffect(() => {
    if (!open) {
      setImgUrl('')
      setImgBlob(null)
      setBox(null)
      setName('')
      setErr('')
      return
    }
    if (initialUrl) setImgUrl(initialUrl)
    if (initialBlob) setImgBlob(initialBlob)
  }, [open, initialUrl, initialBlob])

  if (!open) return null

  // 把 client 坐标 (e.clientX/Y) 映射回源图自然坐标
  function clientToNatural(clientX: number, clientY: number): { nx: number; ny: number } | null {
    const img = imgRef.current
    if (!img || !naturalW || !naturalH) return null
    const rect = img.getBoundingClientRect()
    const px = clientX - rect.left
    const py = clientY - rect.top
    const nx = (px / rect.width) * naturalW
    const ny = (py / rect.height) * naturalH
    return {
      nx: Math.max(0, Math.min(nx, naturalW)),
      ny: Math.max(0, Math.min(ny, naturalH)),
    }
  }

  function onMouseDown(e: React.MouseEvent) {
    if (!naturalW) return
    const p = clientToNatural(e.clientX, e.clientY)
    if (!p) return
    setDrag({ startX: p.nx, startY: p.ny })
    setBox({ x: p.nx, y: p.ny, w: 0, h: 0 })
  }
  function onMouseMove(e: React.MouseEvent) {
    if (!drag || !box) return
    const p = clientToNatural(e.clientX, e.clientY)
    if (!p) return
    const x = Math.min(drag.startX, p.nx)
    const y = Math.min(drag.startY, p.ny)
    const w = Math.abs(p.nx - drag.startX)
    const h = Math.abs(p.ny - drag.startY)
    setBox({ x, y, w, h })
  }
  function onMouseUp() {
    setDrag(null)
  }

  // 文件选择 → 转 URL + Blob
  async function pickFile(f: File) {
    setImgBlob(f)
    const reader = new FileReader()
    reader.onload = () => setImgUrl(reader.result as string)
    reader.readAsDataURL(f)
  }

  // 拉实例当前帧
  async function loadInstanceFrame(idx: number) {
    setErr('')
    try {
      const r = await fetch(`/api/screenshot/${idx}?t=${Date.now()}`)
      if (!r.ok) throw new Error(`screenshot ${r.status}`)
      const b = await r.blob()
      setImgBlob(b)
      setImgUrl(URL.createObjectURL(b))
    } catch (e) {
      setErr('实例截图失败: ' + String(e))
    }
  }

  async function save() {
    setErr('')
    if (!name.trim()) {
      setErr('请输入模版名')
      return
    }
    if (!imgBlob && !imgUrl) {
      setErr('请先选源图')
      return
    }
    setSaving(true)
    try {
      let blob = imgBlob
      if (!blob && imgUrl) {
        const r = await fetch(imgUrl)
        blob = await r.blob()
      }
      if (!blob) throw new Error('无源图 blob')
      const result = await uploadTemplate({
        name: name.trim(),
        file: blob,
        overwrite,
        crop: box && box.w > 4 && box.h > 4
          ? { x: Math.round(box.x), y: Math.round(box.y), w: Math.round(box.w), h: Math.round(box.h) }
          : undefined,
      })
      if (result.similar.length > 0) {
        const sims = result.similar.map((s) => `${s.name}(dist=${s.phash_dist})`).join(', ')
        setErr(`已保存. 注意: 与现有 phash 相近的模版: ${sims}`)
      }
      onSaved?.(result.name)
    } catch (e) {
      setErr('保存失败: ' + String(e))
    } finally {
      setSaving(false)
    }
  }

  // 显示用 box: 把自然坐标投回当前 img 元素显示坐标
  let displayBox: { left: number; top: number; width: number; height: number } | null = null
  if (box && imgRef.current && naturalW) {
    const rect = imgRef.current.getBoundingClientRect()
    const containerRect = containerRef.current?.getBoundingClientRect()
    if (containerRect) {
      const sx = rect.width / naturalW
      const sy = rect.height / naturalH
      displayBox = {
        left: rect.left - containerRect.left + box.x * sx,
        top: rect.top - containerRect.top + box.y * sy,
        width: box.w * sx,
        height: box.h * sy,
      }
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4">
      <div className="bg-card rounded-lg shadow-xl w-[min(1100px,95vw)] max-h-[92vh] flex flex-col">
        {/* 顶栏 */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-border">
          <span className="font-semibold">截图裁剪</span>
          <span className="text-xs text-muted-foreground">
            画一个矩形框 (鼠标拖拽), 命名后保存为新模版
          </span>
          <Button variant="ghost" size="sm" className="ml-auto" onClick={onClose}>关闭</Button>
        </div>

        <div className="flex-1 overflow-hidden grid"
             style={{ gridTemplateColumns: '1fr 280px' }}>
          {/* 左 - 图 + 框 */}
          <div
            ref={containerRef}
            className="relative bg-zinc-900 overflow-auto"
            onMouseDown={onMouseDown}
            onMouseMove={onMouseMove}
            onMouseUp={onMouseUp}
            onMouseLeave={onMouseUp}
            style={{ cursor: 'crosshair' }}
          >
            {imgUrl ? (
              <img
                ref={imgRef}
                src={imgUrl}
                alt="source"
                draggable={false}
                onLoad={(e) => {
                  const img = e.currentTarget
                  setNaturalW(img.naturalWidth)
                  setNaturalH(img.naturalHeight)
                }}
                className="block max-w-full max-h-[70vh] mx-auto select-none"
              />
            ) : (
              <div className="flex items-center justify-center h-[60vh] text-zinc-400 text-sm">
                还没有源图 — 右侧选一种方式
              </div>
            )}
            {displayBox && box && box.w > 0 && (
              <div
                className="absolute border-2 border-blue-400 bg-blue-400/15 pointer-events-none"
                style={{
                  left: displayBox.left,
                  top: displayBox.top,
                  width: displayBox.width,
                  height: displayBox.height,
                }}
              >
                <div className="absolute -top-6 left-0 text-[11px] font-mono bg-blue-500 text-white px-1 rounded">
                  {Math.round(box.w)}×{Math.round(box.h)}
                </div>
              </div>
            )}
          </div>

          {/* 右 - 控制面板 */}
          <div className="border-l border-border p-3 space-y-3 overflow-y-auto text-xs">
            <div>
              <div className="font-semibold mb-1">1. 选源图</div>
              <div className="space-y-1.5">
                <label className="block">
                  <input
                    type="file"
                    accept="image/png,image/jpeg"
                    className="block w-full text-xs"
                    onChange={(e) => {
                      const f = e.currentTarget.files?.[0]
                      if (f) pickFile(f)
                    }}
                  />
                </label>
                <div className="text-[10px] text-muted-foreground">或:</div>
                {Object.values(instances).length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {Object.values(instances).slice(0, 12).map((inst) => (
                      <Button
                        key={inst.index}
                        variant="outline"
                        size="sm"
                        onClick={() => loadInstanceFrame(inst.index)}
                        className={focusedInstance === inst.index ? 'ring-1 ring-blue-500' : ''}
                      >
                        实例 #{inst.index}
                      </Button>
                    ))}
                  </div>
                )}
                {Object.values(instances).length === 0 && (
                  <div className="text-[10px] text-muted-foreground">没有运行实例 (在「运行控制」点开始后可拉实例帧)</div>
                )}
              </div>
            </div>

            {imgUrl && naturalW > 0 && (
              <div className="text-[10px] text-muted-foreground">
                源图尺寸: {naturalW} × {naturalH}
              </div>
            )}

            <div>
              <div className="font-semibold mb-1">2. 框选区域 {box && box.w > 4 ? '✓' : '(可选, 不框=整图)'}</div>
              {box && box.w > 0 && (
                <div className="text-[10px] font-mono text-muted-foreground">
                  bbox: ({Math.round(box.x)}, {Math.round(box.y)}) → ({Math.round(box.x + box.w)}, {Math.round(box.y + box.h)})<br />
                  尺寸: {Math.round(box.w)} × {Math.round(box.h)}
                </div>
              )}
              {box && (
                <Button variant="ghost" size="sm" className="text-[10px]"
                        onClick={() => setBox(null)}>清除框</Button>
              )}
            </div>

            <div>
              <div className="font-semibold mb-1">3. 命名 + 保存</div>
              <Input
                placeholder="模版名 (a-z 0-9 _ -)"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="h-8 text-xs"
              />
              <label className="flex items-center gap-1 mt-1 text-[11px]">
                <input
                  type="checkbox"
                  checked={overwrite}
                  onChange={(e) => setOverwrite(e.target.checked)}
                />
                覆盖同名模版
              </label>
              <Button
                disabled={saving || !name || !imgUrl}
                onClick={save}
                className="w-full mt-2"
                size="sm"
              >
                {saving ? '保存中…' : '保存到 fixtures/templates/'}
              </Button>
              {err && <div className="text-[11px] text-amber-700 mt-1 break-words">{err}</div>}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
