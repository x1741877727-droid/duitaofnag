/**
 * ZeroState — 零配置态: 没账号时的引导.
 * 来源: states.jsx ZeroState (简化版).
 */

function greetByHour(): string {
  const h = new Date().getHours()
  if (h < 6) return '夜深了'
  if (h < 12) return '早上好'
  if (h < 18) return '下午好'
  return '晚上好'
}

const STEPS: Array<[string, string]> = [
  [
    '加账号',
    '在「设置 → 账号」里添加 Gamebot 接管的账号 (1 队长 + 2 队员 = 1 队)',
  ],
  ['启 LDPlayer', '打开雷电模拟器, 数量 ≥ 你的账号数, Gamebot 自动配对'],
  ['回这里启动', '点开始, 12 台一起跑'],
]

export function ZeroState({
  onAddAccount,
  onDocs,
}: {
  onAddAccount: () => void
  onDocs: () => void
}) {
  return (
    <div className="flex-1 flex items-center justify-center p-8 bg-background">
      <div className="max-w-[560px] w-full">
        <div
          className="text-[11px] font-semibold text-subtle uppercase mb-3"
          style={{ letterSpacing: '.08em' }}
        >
          ZERO CONFIG · 0 / 0
        </div>
        <div className="text-[12px] text-subtle mb-1">{greetByHour()}</div>
        <h1 className="text-[32px] font-semibold text-foreground m-0 leading-tight tracking-tight">
          先去配账号
        </h1>
        <p className="text-[14px] text-subtle mt-2.5 leading-relaxed">
          还没有任何账号或模拟器记录。三步就能跑起来：
        </p>
        <ol className="mt-4 p-0 list-none flex flex-col gap-3">
          {STEPS.map(([title, desc], i) => (
            <li key={i} className="flex gap-3.5 items-start">
              <span className="gb-mono w-[26px] h-[26px] rounded-md bg-card border border-border flex items-center justify-center text-[12px] font-semibold text-muted-foreground flex-none">
                {i + 1}
              </span>
              <div className="flex-1 pt-[3px]">
                <div className="text-[13.5px] font-semibold text-foreground">
                  {title}
                </div>
                <div className="text-[12px] text-subtle mt-0.5 leading-relaxed">
                  {desc}
                </div>
              </div>
            </li>
          ))}
        </ol>
        <div className="flex gap-2 mt-6">
          <button
            type="button"
            onClick={onAddAccount}
            className="px-4 py-2.5 rounded-lg text-[13px] font-semibold text-card cursor-pointer border border-foreground bg-foreground hover:opacity-90"
          >
            去设置 添加账号 →
          </button>
          <button
            type="button"
            onClick={onDocs}
            className="px-4 py-2.5 rounded-lg text-[13px] font-medium text-muted-foreground cursor-pointer border border-border bg-card hover:border-foreground transition-colors"
          >
            看演示
          </button>
        </div>
        <div className="mt-7 p-3 rounded-lg bg-card text-[11.5px] text-subtle leading-relaxed border border-dashed border-border">
          <span className="font-semibold text-muted-foreground">队伍规则</span>
          　1 队 = 1 队长 + 2 队员　·　大组 1 = A+B　·　大组 2 = C+D　·　大组 3 = E+F
        </div>
      </div>
    </div>
  )
}
