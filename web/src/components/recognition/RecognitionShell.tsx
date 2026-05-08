/**
 * RecognitionShell — 识别页 chrome.
 *
 * 注: 全局 nav 已经在 [Header.tsx](../header/Header.tsx) 实现, 这里只渲染
 * 子 tab + ctx hint + body, 跟 DataShell 同模式.
 */

import { useEffect, useState } from 'react'
import { C } from '@/lib/design-tokens'
import { fetchTemplateList } from '@/lib/templatesApi'
import { fetchRoiList } from '@/lib/roiApi'
import { fetchYoloInfo } from '@/lib/yoloApi'
import { TemplatesView } from './templates/TemplatesView'
import { TunerView } from './tuner/TunerView'
import { YoloView } from './yolo/YoloView'
import { OcrView } from './ocr/OcrView'

type SubTab = 'templates' | 'tuner' | 'yolo' | 'ocr'

function SubTabs({
  active,
  onChange,
  counts,
}: {
  active: SubTab
  onChange: (k: SubTab) => void
  counts: { templates: number | null; rois: number | null; yoloClasses: number | null }
}) {
  const items: Array<[SubTab, string, string | number | null]> = [
    ['templates', '模版库', counts.templates],
    ['tuner', '模版调试', null],
    [
      'yolo',
      'YOLO',
      counts.yoloClasses != null ? `${counts.yoloClasses} 类` : null,
    ],
    ['ocr', 'OCR / ROI', counts.rois],
  ]
  return (
    <div
      style={{
        display: 'flex',
        gap: 2,
        background: C.surface2,
        padding: 3,
        borderRadius: 7,
        alignSelf: 'flex-start',
      }}
    >
      {items.map(([k, l, badge]) => {
        const on = active === k
        return (
          <button
            key={k}
            type="button"
            onClick={() => onChange(k)}
            style={{
              padding: '5px 13px',
              borderRadius: 5,
              fontSize: 12.5,
              fontWeight: on ? 600 : 500,
              color: on ? C.ink : C.ink3,
              background: on ? C.surface : 'transparent',
              boxShadow: on ? '0 1px 2px rgba(0,0,0,.06)' : 'none',
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              border: 'none',
              cursor: 'pointer',
              fontFamily: 'inherit',
            }}
          >
            {l}
            {badge != null && (
              <span
                className="gb-mono"
                style={{
                  fontSize: 10,
                  color: on ? C.ink3 : C.ink4,
                  fontWeight: 500,
                }}
              >
                {badge}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}

function CtxHint({ subTab }: { subTab: SubTab }) {
  const hints: Record<SubTab, string> = {
    templates: '点缩略图进详情 · 上传 = 拖拽裁切框',
    tuner: '左选模版 · 中调阈值 / 预处理 · 右看处理结果',
    yolo: '左侧 5 步流程 · 采集 → 标注 → 训练 → 测试 → 模型',
    ocr: '画 ROI 框 (拖拽 / 改变大小) · 选预处理算子 · 一键测 OCR',
  }
  return (
    <span
      style={{
        fontSize: 11.5,
        color: C.ink4,
        fontFamily: C.fontMono,
      }}
    >
      {hints[subTab]}
    </span>
  )
}

export function RecognitionShell({
  defaultSubTab = 'templates',
}: {
  defaultSubTab?: SubTab
}) {
  const [subTab, setSubTab] = useState<SubTab>(defaultSubTab)
  const [counts, setCounts] = useState<{
    templates: number | null
    rois: number | null
    yoloClasses: number | null
  }>({ templates: null, rois: null, yoloClasses: null })

  useEffect(() => {
    fetchTemplateList()
      .then((r) => setCounts((c) => ({ ...c, templates: r.count })))
      .catch(() => {})
    fetchRoiList()
      .then((r) => setCounts((c) => ({ ...c, rois: r.count })))
      .catch(() => {})
    fetchYoloInfo()
      .then((r) => setCounts((c) => ({ ...c, yoloClasses: r.classes.length })))
      .catch(() => {})
  }, [])

  return (
    <div
      style={{
        width: '100%',
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        background: C.bg,
        color: C.ink,
        fontFamily: C.fontUi,
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          background: C.surface,
          borderBottom: `1px solid ${C.border}`,
          padding: '10px 22px',
          display: 'flex',
          alignItems: 'center',
          gap: 14,
        }}
      >
        <SubTabs active={subTab} onChange={setSubTab} counts={counts} />
        <span style={{ flex: 1 }} />
        <span
          className="gb-mono"
          style={{
            fontSize: 10.5,
            color: C.ink3,
            padding: '3px 7px',
            borderRadius: 4,
            background: C.surface2,
            border: `1px solid ${C.border}`,
            letterSpacing: '.04em',
            textTransform: 'uppercase',
          }}
        >
          internal · dev tools
        </span>
        <CtxHint subTab={subTab} />
      </div>

      <div
        style={{
          flex: 1,
          minHeight: 0,
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        {subTab === 'templates' && <TemplatesView />}
        {subTab === 'tuner' && <TunerView />}
        {subTab === 'yolo' && <YoloView />}
        {subTab === 'ocr' && <OcrView />}
      </div>
    </div>
  )
}
