import { useEffect, useState } from "react";
import { useAppStore, type InstanceState } from "../stores/appStore";

interface Emulator {
  index: number;
  name: string;
  running: boolean;
  adb_serial: string;
  pid: number;
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// 主面板
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

export function Dashboard() {
  const instances = useAppStore((s) => s.instances);
  const running = useAppStore((s) => s.running);
  const [emulators, setEmulators] = useState<Emulator[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadEmulators();
    const t = setInterval(loadEmulators, 5000);
    return () => clearInterval(t);
  }, []);

  async function loadEmulators() {
    try {
      const r = await fetch("/api/emulators");
      const j = await r.json();
      setEmulators(j.instances || []);
    } catch {}
    setLoading(false);
  }

  const all: InstanceState[] = Object.values(instances).sort((a, b) => a.index - b.index);

  // 运行中：作战视图
  if (running && all.length > 0) {
    return <BattleView instances={all} />;
  }

  // 未运行：部署视图
  return <DeployView emulators={emulators} loading={loading} onRefresh={loadEmulators} />;
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// 作战视图（运行中）
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

const PIPELINE = [
  { key: "accelerator", label: "加速器", icon: "⚡" },
  { key: "launch_game", label: "启动游戏", icon: "🎮" },
  { key: "dismiss_popups", label: "清理弹窗", icon: "🧹" },
  { key: "lobby", label: "进入大厅", icon: "🏠" },
];

function phaseIdx(state: string): number {
  const i = PIPELINE.findIndex((p) => p.key === state);
  if (state === "done" || state === "lobby") return PIPELINE.length;
  if (state === "wait_login") return 1; // 归到"启动游戏"阶段
  return i >= 0 ? i : -1;
}

function BattleView({ instances }: { instances: InstanceState[] }) {
  const stats = useAppStore((s) => s.stats);
  const groupA = instances.filter((i) => i.group === "A");
  const groupB = instances.filter((i) => i.group === "B");

  // 全局流水线统计
  const pipelineStats = PIPELINE.map((p, i) => {
    const count = instances.filter((inst) => phaseIdx(inst.state) > i).length;
    return { ...p, done: count, total: instances.length };
  });

  return (
    <div className="p-5 space-y-4 animate-slide-in">
      {/* ── 全局流水线 ── */}
      <div className="glass-card rounded-xl p-4">
        <div className="flex items-center justify-between mb-3">
          <span className="text-xs font-bold text-slate-400 tracking-wider">全局进度</span>
          <span className="text-xs font-mono text-slate-500">
            运行 {formatDur(stats.running_duration)}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {pipelineStats.map((p, i) => (
            <div key={p.key} className="flex items-center flex-1 gap-2">
              <div className="flex-1">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-[10px] text-slate-500">{p.icon} {p.label}</span>
                  <span className={`text-[10px] font-bold ${
                    p.done === p.total ? "text-emerald-400" : p.done > 0 ? "text-blue-400" : "text-slate-600"
                  }`}>
                    {p.done}/{p.total}
                  </span>
                </div>
                <div className="h-1.5 rounded-full bg-slate-800 overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all duration-700"
                    style={{
                      width: `${p.total > 0 ? (p.done / p.total) * 100 : 0}%`,
                      background: p.done === p.total
                        ? "#10b981"
                        : "linear-gradient(90deg, #3b82f6, #6366f1)",
                    }}
                  />
                </div>
              </div>
              {i < pipelineStats.length - 1 && (
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#334155" strokeWidth="2">
                  <polyline points="9,18 15,12 9,6" />
                </svg>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* ── 两队对照 ── */}
      <div className="grid grid-cols-2 gap-4">
        <TeamPanel group="A" instances={groupA} />
        <TeamPanel group="B" instances={groupB} />
      </div>
    </div>
  );
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// 队伍面板
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

function TeamPanel({ group, instances }: { group: string; instances: InstanceState[] }) {
  const captain = instances.find((i) => i.role === "captain");
  const members = instances.filter((i) => i.role !== "captain");

  const gradient = group === "A"
    ? "linear-gradient(135deg, rgba(59,130,246,0.08), rgba(99,102,241,0.04))"
    : "linear-gradient(135deg, rgba(245,158,11,0.08), rgba(239,68,68,0.04))";
  const accent = group === "A" ? "#3b82f6" : "#f59e0b";

  const allReady = instances.every((i) => i.state === "lobby" || i.state === "done");

  return (
    <div className="rounded-xl overflow-hidden" style={{ background: gradient, border: `1px solid ${accent}15` }}>
      {/* 队伍标题 */}
      <div className="px-4 py-2.5 flex items-center justify-between" style={{ borderBottom: `1px solid ${accent}10` }}>
        <div className="flex items-center gap-2">
          <div className="w-1.5 h-4 rounded-full" style={{ background: accent }} />
          <span className="text-xs font-bold tracking-wider" style={{ color: accent }}>{group} 队</span>
          <span className="text-[10px] text-slate-600">{instances.length} 机器人 + 2 真人</span>
        </div>
        {allReady && (
          <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
            全员就绪
          </span>
        )}
      </div>

      <div className="p-3 space-y-2">
        {/* 队长（大卡片 + 截图） */}
        {captain && <CaptainCard inst={captain} />}

        {/* 队员（小卡片） */}
        <div className="grid grid-cols-2 gap-2">
          {members.map((m) => (
            <MemberCard key={m.index} inst={m} />
          ))}
        </div>

        {/* 真人位 */}
        <div className="grid grid-cols-2 gap-2">
          <HumanSlot label="真人 1" status="待加入" />
          <HumanSlot label="真人 2" status="待加入" />
        </div>
      </div>
    </div>
  );
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// 队长卡片（带截图）
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

function CaptainCard({ inst }: { inst: InstanceState }) {
  const [imgKey, setImgKey] = useState(0);
  const running = useAppStore((s) => s.running);

  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => setImgKey((k) => k + 1), 3000);
    return () => clearInterval(t);
  }, [running]);

  const meta = getStateMeta(inst.state);
  const progress = phaseIdx(inst.state);

  return (
    <div className="glass-card rounded-xl overflow-hidden">
      <div className="flex">
        {/* 左: 截图 */}
        <div className="w-[180px] shrink-0 bg-black/30 relative">
          <img
            key={imgKey}
            src={`/api/screenshot/${inst.index}?t=${imgKey}`}
            className="w-full h-full object-cover"
            alt=""
            onError={(e) => { (e.target as HTMLImageElement).style.opacity = "0.1"; }}
          />
          <div className="absolute top-1.5 left-1.5 flex items-center gap-1">
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-black/60 text-amber-400 font-bold backdrop-blur-sm">
              ★ 队长
            </span>
          </div>
        </div>

        {/* 右: 信息 */}
        <div className="flex-1 p-3 flex flex-col justify-between min-w-0">
          <div>
            <div className="flex items-center justify-between mb-1">
              <div className="flex items-center gap-1.5">
                <span className="text-[10px] font-mono text-slate-600">#{inst.index}</span>
                <span className="text-xs font-medium truncate">{inst.nickname || `实例${inst.index}`}</span>
              </div>
              <StatusBadge state={inst.state} meta={meta} working={progress >= 0 && progress < PIPELINE.length} />
            </div>

            {/* 进度步骤 */}
            <div className="flex items-center gap-1 mt-2">
              {PIPELINE.map((p, i) => (
                <div key={p.key} className="flex items-center flex-1 gap-0.5">
                  <div
                    className={`w-5 h-5 rounded flex items-center justify-center text-[9px] ${
                      i < progress ? "bg-emerald-500/15 text-emerald-400"
                      : i === progress ? "bg-blue-500/15 text-blue-400 animate-pulse-soft"
                      : "bg-slate-800/50 text-slate-700"
                    }`}
                    title={p.label}
                  >
                    {i < progress ? "✓" : p.icon}
                  </div>
                  {i < PIPELINE.length - 1 && (
                    <div className={`h-[2px] flex-1 rounded ${i < progress ? "bg-emerald-500/30" : "bg-slate-800/50"}`} />
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* 底部信息 */}
          <div className="flex items-center justify-between mt-2">
            <span className="text-[10px] font-mono text-slate-600">emulator-{5554 + inst.index * 2}</span>
            {inst.state_duration > 0 && (
              <span className="text-[10px] font-mono text-slate-600">{Math.round(inst.state_duration)}s</span>
            )}
          </div>

          {inst.error && (
            <div className="text-[10px] text-red-400/80 mt-1 truncate" title={inst.error}>{inst.error}</div>
          )}
        </div>
      </div>
    </div>
  );
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// 队员卡片（紧凑）
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

function MemberCard({ inst }: { inst: InstanceState }) {
  const [imgKey, setImgKey] = useState(0);
  const [showImg, setShowImg] = useState(false);
  const running = useAppStore((s) => s.running);

  useEffect(() => {
    if (!showImg || !running) return;
    const t = setInterval(() => setImgKey((k) => k + 1), 3000);
    return () => clearInterval(t);
  }, [showImg, running]);

  const meta = getStateMeta(inst.state);
  const progress = phaseIdx(inst.state);
  const isWorking = progress >= 0 && progress < PIPELINE.length;

  return (
    <div className="glass-card rounded-lg overflow-hidden cursor-pointer" onClick={() => setShowImg(!showImg)}>
      <div className="p-2.5">
        <div className="flex items-center justify-between mb-1.5">
          <div className="flex items-center gap-1.5">
            <span className="text-[10px] font-mono text-slate-600">#{inst.index}</span>
            <span className="text-[11px] text-slate-400 truncate max-w-[60px]">
              {inst.nickname || `队员`}
            </span>
          </div>
          <StatusBadge state={inst.state} meta={meta} working={isWorking} small />
        </div>

        {/* 迷你进度 */}
        <div className="flex items-center gap-0.5">
          {PIPELINE.map((p, i) => (
            <div
              key={p.key}
              className={`h-1 flex-1 rounded-full ${
                i < progress ? "bg-emerald-500/40"
                : i === progress ? "bg-blue-500/40 animate-pulse-soft"
                : "bg-slate-800/50"
              }`}
            />
          ))}
        </div>

        {inst.error && (
          <div className="text-[9px] text-red-400/70 mt-1 truncate">{inst.error}</div>
        )}
      </div>

      {/* 展开截图 */}
      {showImg && running && (
        <div className="border-t border-slate-800/30">
          <img
            key={imgKey}
            src={`/api/screenshot/${inst.index}?t=${imgKey}`}
            className="w-full"
            alt=""
            onError={(e) => { (e.target as HTMLImageElement).style.opacity = "0.1"; }}
          />
        </div>
      )}
    </div>
  );
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// 真人占位
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

function HumanSlot({ label, status }: { label: string; status: string }) {
  return (
    <div className="rounded-lg border border-dashed border-slate-700/40 px-2.5 py-2 flex items-center justify-between">
      <span className="text-[10px] text-slate-600">{label}</span>
      <span className="text-[10px] text-slate-700">{status}</span>
    </div>
  );
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// 部署视图（未运行时）
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

function DeployView({ emulators, loading, onRefresh }: {
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
    } catch {
      alert("连接失败");
    }
    setTestingIdx(null);
  }

  const online = emulators.filter((e) => e.running);
  const offline = emulators.filter((e) => !e.running);

  return (
    <div className="p-5 max-w-4xl mx-auto space-y-5 animate-slide-in">
      {/* 标题 */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-bold">部署中心</h2>
          <p className="text-xs text-slate-500 mt-0.5">
            {loading ? "扫描模拟器中..." : `${emulators.length} 个模拟器 · ${online.length} 个在线`}
          </p>
        </div>
        <button
          className="px-3 py-1.5 rounded-lg text-xs font-medium border border-slate-700 text-slate-400 hover:text-white hover:border-slate-500 transition-colors"
          onClick={onRefresh}
        >
          刷新
        </button>
      </div>

      {/* 在线实例 */}
      {online.length > 0 && (
        <div>
          <div className="text-[11px] text-slate-500 mb-2 flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full dot-success" />
            在线 · 可操作
          </div>
          <div className="grid grid-cols-3 gap-3">
            {online.map((emu) => (
              <div key={emu.index} className="glass-card rounded-xl p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-mono text-slate-500">#{emu.index}</span>
                    <span className="text-sm font-medium">{emu.name}</span>
                  </div>
                  <span className="w-2 h-2 rounded-full dot-success" />
                </div>
                <div className="text-[10px] font-mono text-slate-600">{emu.adb_serial}</div>
                <button
                  className="btn-primary w-full !py-2 !text-xs"
                  onClick={() => testInstance(emu.index)}
                  disabled={testingIdx !== null}
                >
                  {testingIdx === emu.index ? "启动中..." : "▶ 测试运行"}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 离线实例 */}
      {offline.length > 0 && (
        <div>
          <div className="text-[11px] text-slate-500 mb-2 flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full dot-idle" />
            离线 · 需启动
          </div>
          <div className="grid grid-cols-3 gap-3">
            {offline.map((emu) => (
              <div key={emu.index} className="glass-card rounded-xl p-4 opacity-50">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-xs font-mono text-slate-600">#{emu.index}</span>
                  <span className="text-sm text-slate-500">{emu.name}</span>
                </div>
                <div className="text-[10px] text-slate-700">请在雷电多开器中启动</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {emulators.length === 0 && !loading && (
        <div className="glass-card rounded-xl p-8 text-center">
          <p className="text-sm text-slate-400">未检测到模拟器</p>
          <p className="text-xs text-slate-600 mt-1">请在「设置」中确认雷电模拟器路径</p>
        </div>
      )}

      {/* 操作提示 */}
      {online.length > 0 && (
        <div className="text-[11px] text-slate-600 p-3 rounded-lg"
          style={{ background: "rgba(59,130,246,0.04)", border: "1px solid rgba(59,130,246,0.08)" }}>
          <strong className="text-blue-400/70">测试模式：</strong>
          点击在线实例的「测试运行」按钮，将对该实例执行完整流程（加速器→游戏→弹窗清理→大厅）。
          测试通过后，在「设置」中配置分组，然后用右上角「启动全部」批量运行。
        </div>
      )}
    </div>
  );
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// 工具
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

const STATE_META: Record<string, { label: string; dot: string }> = {
  init: { label: "等待", dot: "dot-idle" },
  accelerator: { label: "加速器", dot: "dot-running" },
  launch_game: { label: "启动游戏", dot: "dot-running" },
  wait_login: { label: "登录中", dot: "dot-running" },
  dismiss_popups: { label: "清弹窗", dot: "dot-warning" },
  lobby: { label: "就绪", dot: "dot-success" },
  done: { label: "完成", dot: "dot-success" },
  error: { label: "出错", dot: "dot-error" },
};

function getStateMeta(state: string) {
  return STATE_META[state] ?? STATE_META.init;
}

function StatusBadge({ state, meta, working, small }: {
  state: string; meta: { label: string; dot: string }; working: boolean; small?: boolean;
}) {
  const colorMap: Record<string, string> = {
    "dot-idle": "bg-slate-700/50 text-slate-500",
    "dot-running": "bg-blue-500/10 text-blue-400 border-blue-500/20",
    "dot-warning": "bg-amber-500/10 text-amber-400 border-amber-500/20",
    "dot-success": "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
    "dot-error": "bg-red-500/10 text-red-400 border-red-500/20",
  };
  const cls = colorMap[meta.dot] ?? colorMap["dot-idle"];
  const sz = small ? "text-[9px] px-1.5 py-0.5" : "text-[10px] px-2 py-0.5";

  return (
    <span className={`${sz} rounded-full border font-medium ${cls} ${working ? "animate-pulse-soft" : ""}`}>
      {meta.label}
    </span>
  );
}

function formatDur(secs: number): string {
  if (secs <= 0) return "0s";
  const m = Math.floor(secs / 60);
  const s = Math.round(secs % 60);
  return m > 0 ? `${m}m${s}s` : `${s}s`;
}
