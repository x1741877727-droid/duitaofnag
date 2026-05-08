import { Kbd } from '@/components/ui/kbd'

/**
 * KbdHint — 底部细字快捷键提示.
 * 来源: layouts.jsx KbdHint.
 */
export function KbdHint() {
  return (
    <div className="px-6 py-2 border-t border-border-soft bg-card flex items-center gap-3 text-[10.5px] text-fainter">
      <span>
        <Kbd>Esc</Kbd> 关闭抽屉
      </span>
      <span>
        <Kbd>Space</Kbd> 选 / 取消选
      </span>
      <span>
        <Kbd>Cmd 0-3</Kbd> 切大组
      </span>
      <span>
        <Kbd>Cmd L</Kbd> 日志面板
      </span>
    </div>
  )
}
