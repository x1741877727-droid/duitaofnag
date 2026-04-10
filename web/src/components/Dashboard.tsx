import { useAppStore, type InstanceState } from "../stores/appStore";
import { GroupPanel } from "./GroupPanel";

export function Dashboard() {
  const instances = useAppStore((s) => s.instances);
  const running = useAppStore((s) => s.running);

  const all: InstanceState[] = Object.values(instances).sort(
    (a, b) => a.index - b.index
  );

  const groupA = all.filter((i) => i.group === "A");
  const groupB = all.filter((i) => i.group === "B");

  if (!running || all.length === 0) {
    return <EmptyState />;
  }

  return (
    <div className="p-5 animate-slide-in">
      <div className="grid grid-cols-2 gap-6">
        <GroupPanel group="A" instances={groupA} />
        <GroupPanel group="B" instances={groupB} />
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center h-full px-8">
      <div className="glass-card rounded-2xl p-10 max-w-md text-center">
        {/* 图标 */}
        <div
          className="w-16 h-16 rounded-2xl mx-auto mb-5 flex items-center justify-center"
          style={{ background: "linear-gradient(135deg, rgba(59,130,246,0.15), rgba(139,92,246,0.15))" }}
        >
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#6366f1" strokeWidth="1.5">
            <polygon points="5,3 19,12 5,21" />
          </svg>
        </div>

        <h2 className="text-lg font-bold mb-2">准备就绪</h2>
        <p className="text-sm text-slate-400 mb-6 leading-relaxed">
          点击右上角
          <span className="inline-flex items-center gap-1 mx-1 px-2 py-0.5 rounded-md text-blue-400 text-xs font-medium"
            style={{ background: "rgba(59,130,246,0.1)", border: "1px solid rgba(59,130,246,0.2)" }}>
            启动全部
          </span>
          按钮开始运行。
        </p>

        <div className="space-y-3 text-left">
          <GuideStep num={1} title="检查设置" desc="确认雷电模拟器路径和 ADB 配置正确" />
          <GuideStep num={2} title="配置账号" desc="在设置页分配每个模拟器实例的组别和角色" />
          <GuideStep num={3} title="启动运行" desc="点击启动按钮，所有实例将同时开始自动化流程" />
        </div>
      </div>
    </div>
  );
}

function GuideStep({ num, title, desc }: { num: number; title: string; desc: string }) {
  return (
    <div className="flex gap-3 items-start">
      <span
        className="shrink-0 w-6 h-6 rounded-full flex items-center justify-center text-[11px] font-bold"
        style={{
          background: "linear-gradient(135deg, rgba(59,130,246,0.15), rgba(139,92,246,0.15))",
          color: "#818cf8",
        }}
      >
        {num}
      </span>
      <div>
        <div className="text-xs font-medium text-slate-300">{title}</div>
        <div className="text-[11px] text-slate-500">{desc}</div>
      </div>
    </div>
  );
}
