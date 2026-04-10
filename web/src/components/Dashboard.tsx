import { useEffect, useState } from "react";
import { useAppStore, type InstanceState } from "../stores/appStore";
import { InstanceCard } from "./InstanceCard";

interface Emulator {
  index: number;
  name: string;
  running: boolean;
  adb_serial: string;
  pid: number;
}

export function Dashboard() {
  const instances = useAppStore((s) => s.instances);
  const running = useAppStore((s) => s.running);
  const [emulators, setEmulators] = useState<Emulator[]>([]);
  const [loading, setLoading] = useState(true);

  // 自动检测模拟器
  useEffect(() => {
    loadEmulators();
    const timer = setInterval(loadEmulators, 5000); // 每5秒刷新
    return () => clearInterval(timer);
  }, []);

  async function loadEmulators() {
    try {
      const res = await fetch("/api/emulators");
      const json = await res.json();
      setEmulators(json.instances || []);
    } catch { /* ignore */ }
    setLoading(false);
  }

  const all: InstanceState[] = Object.values(instances).sort((a, b) => a.index - b.index);
  const groupA = all.filter((i) => i.group === "A");
  const groupB = all.filter((i) => i.group === "B");

  // 运行中 → 显示控制面板
  if (running && all.length > 0) {
    return (
      <div className="p-5 space-y-5 animate-slide-in">
        <OverviewBar instances={all} />
        <div className="grid grid-cols-2 gap-5">
          {groupA.length > 0 && <GroupSection group="A" instances={groupA} />}
          {groupB.length > 0 && <GroupSection group="B" instances={groupB} />}
        </div>
      </div>
    );
  }

  // 未运行 → 显示模拟器列表 + 快速测试
  return (
    <div className="p-5 space-y-5 animate-slide-in">
      <EmulatorList emulators={emulators} loading={loading} onRefresh={loadEmulators} />
    </div>
  );
}


// ── 模拟器列表（未运行时显示）──

function EmulatorList({ emulators, loading, onRefresh }: {
  emulators: Emulator[]; loading: boolean; onRefresh: () => void;
}) {
  const [testingIdx, setTestingIdx] = useState<number | null>(null);

  async function testInstance(idx: number) {
    setTestingIdx(idx);
    try {
      const res = await fetch(`/api/start/${idx}`, { method: "POST" });
      const json = await res.json();
      if (json.ok) {
        useAppStore.getState().setRunning(true);
      } else {
        alert(json.error || "启动失败");
      }
    } catch (e) {
      alert("连接失败");
    }
    setTestingIdx(null);
  }

  const onlineCount = emulators.filter((e) => e.running).length;

  return (
    <div className="max-w-3xl mx-auto space-y-5">
      {/* 标题 */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-bold">模拟器实例</h2>
          <p className="text-xs text-slate-500 mt-0.5">
            {loading ? "检测中..." : `检测到 ${emulators.length} 个实例，${onlineCount} 个在线`}
          </p>
        </div>
        <button
          className="px-3 py-1.5 rounded-lg text-xs font-medium border border-slate-700 text-slate-400 hover:text-white hover:border-slate-500 transition-colors"
          onClick={onRefresh}
        >
          刷新
        </button>
      </div>

      {/* 模拟器卡片网格 */}
      {emulators.length > 0 ? (
        <div className="grid grid-cols-2 gap-3">
          {emulators.map((emu) => (
            <div key={emu.index} className="glass-card rounded-xl p-4">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2.5">
                  <span className="text-xs font-mono text-slate-600">#{emu.index}</span>
                  <span className="text-sm font-medium">{emu.name}</span>
                </div>
                <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
                  emu.running
                    ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                    : "bg-slate-700/50 text-slate-500 border border-slate-600/30"
                }`}>
                  {emu.running ? "在线" : "离线"}
                </span>
              </div>

              <div className="flex items-center justify-between">
                <span className="text-[10px] font-mono text-slate-600">{emu.adb_serial}</span>

                {emu.running ? (
                  <button
                    className="btn-primary !py-1.5 !px-3 !text-xs !rounded-lg"
                    onClick={() => testInstance(emu.index)}
                    disabled={testingIdx !== null}
                  >
                    {testingIdx === emu.index ? "启动中..." : "测试运行"}
                  </button>
                ) : (
                  <span className="text-[10px] text-slate-600">请先在雷电多开器中启动</span>
                )}
              </div>
            </div>
          ))}
        </div>
      ) : !loading ? (
        <div className="glass-card rounded-xl p-8 text-center">
          <p className="text-sm text-slate-400 mb-2">未检测到模拟器</p>
          <p className="text-xs text-slate-600">请在「设置」中确认雷电模拟器路径是否正确</p>
        </div>
      ) : null}

      {/* 使用提示 */}
      {emulators.length > 0 && (
        <div className="text-[11px] text-slate-600 p-3 rounded-lg" style={{ background: "rgba(59,130,246,0.04)", border: "1px solid rgba(59,130,246,0.08)" }}>
          <strong className="text-blue-400/70">操作说明：</strong>
          在线的实例可以点击「测试运行」单独测试（加速器→游戏→弹窗清理→大厅）。
          全部准备好后，用右上角「启动全部」按钮批量启动。
          需要先在「设置」中配置分组后才能使用全部启动。
        </div>
      )}
    </div>
  );
}


// ── 运行中显示 ──

function GroupSection({ group, instances }: { group: string; instances: InstanceState[] }) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <div className="w-1.5 h-4 rounded-full"
          style={{ background: group === "A" ? "linear-gradient(180deg, #3b82f6, #6366f1)" : "linear-gradient(180deg, #f59e0b, #ef4444)" }} />
        <span className="text-xs font-bold text-slate-400 tracking-widest uppercase">{group} 组</span>
        <span className="text-[10px] text-slate-600">{instances.length} 个实例</span>
      </div>
      <div className="grid gap-2.5">
        {instances.map((inst) => <InstanceCard key={inst.index} inst={inst} />)}
      </div>
    </div>
  );
}

function OverviewBar({ instances }: { instances: InstanceState[] }) {
  const total = instances.length;
  const working = instances.filter((i) => !["init", "done", "error", "lobby"].includes(i.state)).length;
  const ready = instances.filter((i) => i.state === "lobby" || i.state === "done").length;
  const errors = instances.filter((i) => i.state === "error").length;
  const progress = total > 0 ? Math.round((ready / total) * 100) : 0;

  return (
    <div className="glass-card rounded-xl p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-4">
          <Metric label="总计" value={`${total}`} />
          <Metric label="进行中" value={`${working}`} color="text-blue-400" />
          <Metric label="已就绪" value={`${ready}`} color="text-emerald-400" />
          {errors > 0 && <Metric label="出错" value={`${errors}`} color="text-red-400" />}
        </div>
        <span className="text-xs font-mono text-slate-500">{progress}%</span>
      </div>
      <div className="h-1.5 rounded-full bg-slate-800 overflow-hidden">
        <div className="h-full rounded-full transition-all duration-500"
          style={{ width: `${progress}%`, background: errors > 0 ? "linear-gradient(90deg, #10b981, #ef4444)" : "linear-gradient(90deg, #3b82f6, #10b981)" }} />
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
