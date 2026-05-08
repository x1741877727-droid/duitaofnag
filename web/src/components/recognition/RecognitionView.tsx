import { useAppStore, type RecognitionSubView } from '@/lib/store'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { Image, SlidersHorizontal, Crosshair, Type } from 'lucide-react'
import { TemplateLibrary } from '@/components/templates/TemplateLibrary'
import { TemplateTuner } from '@/components/template/TemplateTuner'
import { YoloView } from '@/components/yolo/YoloView'
import { OcrTuner } from '@/components/ocr/OcrTuner'

const TABS: { key: RecognitionSubView; label: string; Icon: typeof Image; title: string }[] = [
  { key: 'templates',      label: '模版库',   Icon: Image,             title: '模版库' },
  { key: 'template-tuner', label: '模版调试', Icon: SlidersHorizontal, title: '模版 Preprocessing 调试' },
  { key: 'yolo',           label: 'YOLO',    Icon: Crosshair,         title: 'YOLO 测试 / 数据集 / 标注 / 模型' },
  { key: 'ocr',            label: 'OCR',     Icon: Type,              title: 'OCR ROI 校准 + 实测' },
]

export function RecognitionView() {
  const { recognitionSubView, setRecognitionSubView } = useAppStore()
  return (
    <div className="h-full flex flex-col">
      <SubTabBar
        tabs={TABS}
        active={recognitionSubView}
        onChange={k => setRecognitionSubView(k as RecognitionSubView)}
      />
      <div className="flex-1 min-h-0 overflow-auto">
        {recognitionSubView === 'templates'      && <TemplateLibrary />}
        {recognitionSubView === 'template-tuner' && <TemplateTuner />}
        {recognitionSubView === 'yolo'           && <YoloView />}
        {recognitionSubView === 'ocr'            && <OcrTuner />}
      </div>
    </div>
  )
}

// 复用的 sub-tab 横条
export function SubTabBar<K extends string>({
  tabs,
  active,
  onChange,
}: {
  tabs: { key: K; label: string; Icon: typeof Image; title: string }[]
  active: K
  onChange: (k: K) => void
}) {
  return (
    <div className="flex items-center gap-1 px-3 py-2 border-b border-border bg-muted/20 shrink-0">
      {tabs.map(({ key, label, Icon, title }) => {
        const on = active === key
        return (
          <Button
            key={key}
            variant={on ? 'secondary' : 'ghost'}
            size="sm"
            onClick={() => onChange(key)}
            title={title}
            className={cn('gap-1.5', on && 'font-semibold')}
          >
            <Icon className="h-3.5 w-3.5" />
            <span>{label}</span>
          </Button>
        )
      })}
    </div>
  )
}
