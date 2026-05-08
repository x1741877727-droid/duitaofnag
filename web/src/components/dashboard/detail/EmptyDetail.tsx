import { Kbd } from '@/components/ui/kbd'

/**
 * EmptyDetail — 抽屉空态提示.
 * 来源: detail.jsx EmptyDetail.
 */
export function EmptyDetail() {
  return (
    <div className="h-full flex flex-col items-center justify-center gap-2.5 px-8 text-center text-fainter">
      <div className="w-[38px] h-[38px] rounded-lg flex items-center justify-center border border-dashed border-fainter">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
          <rect
            x="2"
            y="3"
            width="12"
            height="9"
            rx="1"
            stroke="currentColor"
            strokeWidth="1.2"
          />
          <path
            d="M2 6h12"
            stroke="currentColor"
            strokeWidth="1.2"
          />
        </svg>
      </div>
      <div className="text-[13px] text-subtle font-medium">选实例聚焦</div>
      <div className="text-[11.5px] leading-relaxed max-w-[220px]">
        点击任意实例查看完整识别证据 / 决策日志 / Phase 时间线
        <br />
        多选 (1–4) 横向对比, <Kbd>Space</Kbd> 切换选中
      </div>
    </div>
  )
}
