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
    return <WelcomePanel />;
  }

  return (
    <div className="p-5 space-y-5 animate-slide-in">
      {/* 总览条 */}
      <OverviewBar instances={all} />

      {/* 两组并排 */}
      <div className="grid grid-cols-2 gap-5">
        {groupA.length > 0 && <GroupPanel group="A" instances={groupA} />}
        {groupB.length > 0 && <GroupPanel group="B" instances={groupB} />}
      </div>
    </div>
  );
}

function OverviewBar({ instances }: { instances: InstanceState[] }) {
  const total = instances.length;
  const phases = instances.map((i) => i.state);
  const working = phases.filter((s) => !["init", "done", "error", "lobby"].includes(s)).length;
  const ready = phases.filter((s) => s === "lobby" || s === "done").length;
  const errors = phases.filter((s) => s === "error").length;

  const progress = total > 0 ? Math.round(((ready) / total) * 100) : 0;

  return (
    <div className="glass-card rounded-xl p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-4">
          <Metric label="总计" value={`${total}`} />
          <Metric label="进行中" value={`${working}`} color="text-blue-400" />
          <Metric label="已就绪" value={`${ready}`} color="text-emerald-400" />
          {errors > 0 && <Metric label="出错" value={`${errors}`} color="text-red-400" />}
        </div>
        <span className="text-xs font-mono text-slate-500">{progress}% 完成</span>
      </div>
      {/* 进度条 */}
      <div className="h-1.5 rounded-full bg-slate-800 overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{
            width: `${progress}%`,
            background: errors > 0
              ? "linear-gradient(90deg, #10b981, #ef4444)"
              : "linear-gradient(90deg, #3b82f6, #10b981)",
          }}
        />
      </div>
    </div>
  );
}

function Metric({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-[11px] text-slate-500">{label}</span>
      <span className={`text-sm font-bold ${color || "text-white"}`}>{value}</span>
    </div>
  );
}

function WelcomePanel() {
  const setActiveView = useAppStore((s) => s.setActiveView);

  return (
    <div className="flex flex-col items-center justify-center h-full px-8">
      <div className="glass-card rounded-2xl p-10 max-w-lg text-center space-y-6">
        <div
          className="w-16 h-16 rounded-2xl mx-auto flex items-center justify-center"
          style={{ background: "linear-gradient(135deg, rgba(59,130,246,0.15), rgba(139,92,246,0.15))" }}
        >
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#6366f1" strokeWidth="1.5">
            <polygon points="5,3 19,12 5,21" />
          </svg>
        </div>

        <div>
          <h2 className="text-lg font-bold mb-2">控制中心</h2>
          <p className="text-sm text-slate-400 leading-relaxed">
            配置好模拟器实例后，点击右上角
            <span className="inline-flex items-center mx-1 px-2 py-0.5 rounded-md text-blue-400 text-[11px] font-medium"
              style={{ background: "rgba(59,130,246,0.1)", border: "1px solid rgba(59,130,246,0.2)" }}>
              启动全部
            </span>
            开始自动化流程。
          </p>
        </div>

        <div className="flex justify-center gap-3">
          <StepTag num={1} text="设置路径" done={true} />
          <StepTag num={2} text="分配实例" done={false} />
          <StepTag num={3} text="启动运行" done={false} />
        </div>

        <button
          className="px-4 py-2 rounded-lg text-xs font-medium border border-indigo-500/30 text-indigo-400 hover:bg-indigo-500/10 transition-colors"
          onClick={() => setActiveView("settings")}
        >
          前往设置
        </button>
      </div>
    </div>
  );
}

function StepTag({ num, text, done }: { num: number; text: string; done: boolean }) {
  return (
    <div className="flex items-center gap-1.5">
      <span
        className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold ${
          done
            ? "bg-emerald-500/20 text-emerald-400"
            : "bg-slate-800 text-slate-500"
        }`}
      >
        {done ? "✓" : num}
      </span>
      <span className={`text-[11px] ${done ? "text-slate-300" : "text-slate-500"}`}>{text}</span>
    </div>
  );
}
