/**
 * YOLO 主页 — 5 tab 切换.
 *
 * 测试   YoloTester    选实例 + 阈值 → 推理 → 标注图 + dets
 * 数据集 YoloDataset   raw_screenshots 缩略图 + 已标/未标/跳过 状态 + 分类柱
 * 标注   YoloLabeler   单图标注 (画框 + 类别 + 保存 YOLO 格式)
 * 采集   YoloCapture   选实例 → 抓帧 → 自动存到 raw_screenshots
 * 模型   YoloModel     当前模型信息 + 上传新 ONNX (覆盖 latest.onnx)
 */
import { useState } from 'react'
import { useAppStore } from '@/lib/store'
import { YoloTester } from './YoloTester'
import { YoloDataset } from './YoloDataset'
import { YoloLabeler } from './YoloLabeler'
import { YoloCapture } from './YoloCapture'
import { YoloModel } from './YoloModel'

type Tab = 'test' | 'dataset' | 'label' | 'capture' | 'model'

const TABS: { key: Tab; label: string; hint: string }[] = [
  { key: 'test', label: '测试', hint: '抓帧跑推理看检测框' },
  { key: 'dataset', label: '数据集', hint: '已采集的训练截图 + 标注状态' },
  { key: 'label', label: '标注', hint: '画框标 close_x / action_btn' },
  { key: 'capture', label: '采集', hint: '从实例抓帧存进数据集' },
  { key: 'model', label: '模型', hint: '当前模型信息 + 上传新 ONNX' },
]

export function YoloView() {
  const [tab, setTab] = useState<Tab>('test')
  // 标注 tab 用: 当前编辑哪张图
  const [labelTarget, setLabelTarget] = useState<string>('')

  return (
    <div className="flex flex-col gap-3 h-full min-h-0">
      <div className="flex items-center gap-1 border-b border-border pb-2">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-3 py-1.5 rounded text-sm transition ${
              tab === t.key
                ? 'bg-blue-50 text-blue-700 font-semibold border border-blue-200'
                : 'text-muted-foreground hover:bg-zinc-50'
            }`}
            title={t.hint}
          >
            {t.label}
          </button>
        ))}
        <div className="ml-auto text-[11px] text-muted-foreground">
          {TABS.find((t) => t.key === tab)?.hint}
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-hidden">
        {tab === 'test' && <YoloTester />}
        {tab === 'dataset' && (
          <YoloDataset
            onLabel={(name) => {
              setLabelTarget(name)
              setTab('label')
            }}
          />
        )}
        {tab === 'label' && (
          <YoloLabeler
            initialName={labelTarget}
            onChangeName={(n) => setLabelTarget(n)}
          />
        )}
        {tab === 'capture' && <YoloCapture />}
        {tab === 'model' && <YoloModel />}
      </div>
    </div>
  )
}
