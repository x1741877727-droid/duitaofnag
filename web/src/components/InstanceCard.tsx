import { useState, useEffect } from "react";
import { useAppStore, type InstanceState } from "../stores/appStore";

const PHASES = [
  { key: "accelerator", label: "加速器", icon: "⚡" },
  { key: "launch_game", label: "启动", icon: "🎮" },
  { key: "dismiss_popups", label: "弹窗", icon: "🧹" },
  { key: "lobby", label: "大厅", icon: "✅" },
];

const STATE_META: Record<string, { label: string; dot: string; color: string }> = {
  init: { label: "等待启动", dot: "dot-idle", color: "text-slate-500" },
  accelerator: { label: "启动加速器", dot: "dot-running", color: "status-running" },
  launch_game: { label: "启动游戏", dot: "dot-running", color: "status-running" },
  wait_login: { label: "等待登录", dot: "dot-running", color: "status-running" },
  dismiss_popups: { label: "清理弹窗", dot: "dot-warning", color: "status-warning" },
  lobby: { label: "大厅就绪", dot: "dot-success", color: "status-success" },
  map_setup: { label: "设置地图", dot: "dot-running", color: "status-running" },
  team_create: { label: "创建队伍", dot: "dot-running", color: "status-running" },
  team_join: { label: "加入队伍", dot: "dot-running", color: "status-running" },
  done: { label: "已完成", dot: "dot-success", color: "status-success" },
  error: { label: "出错", dot: "dot-error", color: "status-error" },
};

function phaseIndex(state: string): number {
  const idx = PHASES.findIndex((p) => p.key === state);
  if (state === "done" || state === "lobby") return PHASES.length;
  return idx >= 0 ? idx : -1;
}

export function InstanceCard({ inst }: { inst: InstanceState }) {
  const running = useAppStore((s) => s.running);
  const selectedInstance = useAppStore((s) => s.selectedInstance);
  const setSelectedInstance = useAppStore((s) => s.setSelectedInstance);
  const [imgKey, setImgKey] = useState(0);

  const meta = STATE_META[inst.state] ?? STATE_META.init;
  const isCaptain = inst.role === "captain";
  const isExpanded = selectedInstance === inst.index;
  const currentPhase = phaseIndex(inst.state);
  const isWorking = running && !["init", "done", "error", "lobby"].includes(inst.state);
  const isDone = inst.state === "lobby" || inst.state === "done";

  useEffect(() => {
    if (!isExpanded || !running) return;
    const timer = setInterval(() => setImgKey((k) => k + 1), 2500);
    return () => clearInterval(timer);
  }, [isExpanded, running]);

  return (
    <div
      className={`glass-card rounded-xl overflow-hidden transition-all ${
        isExpanded ? "ring-1 ring-indigo-500/30" : ""
      } ${isDone ? "border-emerald-500/20" : ""}`}
    >
      {/* 主体 — 可点击展开 */}
      <div
        className="p-3.5 cursor-pointer"
        onClick={() => setSelectedInstance(isExpanded ? null : inst.index)}
      >
        {/* 顶行: 编号 + 标签 + 状态 */}
        <div className="flex items-center justify-between mb-2.5">
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-mono text-slate-600">#{inst.index}</span>
            {isCaptain && (
              <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-amber-500/10 text-amber-400 border border-amber-500/20 font-medium">
                队长
              </span>
            )}
            <span className="text-xs text-slate-400 truncate max-w-[100px]">
              {inst.nickname || `账号${inst.index + 1}`}
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className={`w-2 h-2 rounded-full ${meta.dot} ${isWorking ? "animate-pulse-soft" : ""}`} />
            <span className={`text-[11px] font-medium ${meta.color}`}>{meta.label}</span>
            {inst.state_duration > 0 && (
              <span className="text-[10px] text-slate-600 font-mono ml-1">
                {Math.round(inst.state_duration)}s
              </span>
            )}
          </div>
        </div>

        {/* 进度步骤 */}
        {running && (
          <div className="flex items-center gap-0.5">
            {PHASES.map((phase, i) => {
              const done = i < currentPhase;
              const active = i === currentPhase;
              return (
                <div key={phase.key} className="flex items-center flex-1 gap-0.5">
                  <div className="flex flex-col items-center gap-0.5" title={phase.label}>
                    <span
                      className={`w-6 h-6 rounded-md flex items-center justify-center text-[10px] transition-all ${
                        done
                          ? "bg-emerald-500/15 text-emerald-400"
                          : active
                          ? "bg-blue-500/15 text-blue-400 animate-pulse-soft"
                          : "bg-slate-800/50 text-slate-600"
                      }`}
                    >
                      {done ? "✓" : phase.icon}
                    </span>
                  </div>
                  {i < PHASES.length - 1 && (
                    <div className={`h-[2px] flex-1 rounded ${done ? "bg-emerald-500/30" : "bg-slate-800/50"}`} />
                  )}
                </div>
              );
            })}
          </div>
        )}

        {/* 错误 */}
        {inst.error && (
          <div className="text-[11px] text-red-400/80 mt-2 px-2 py-1 rounded bg-red-500/5 border border-red-500/10 truncate" title={inst.error}>
            {inst.error}
          </div>
        )}
      </div>

      {/* 展开区: 截图 + 操作 */}
      {isExpanded && (
        <div className="border-t border-slate-800/40 animate-slide-in">
          {/* 实时截图 */}
          {running && (
            <div className="p-3">
              <img
                key={imgKey}
                src={`/api/screenshot/${inst.index}?t=${imgKey}`}
                className="w-full rounded-lg border border-slate-700/30"
                alt="截图"
                onError={(e) => { (e.target as HTMLImageElement).style.opacity = "0.2"; }}
              />
            </div>
          )}

          {/* 信息 */}
          <div className="px-3.5 pb-3 flex items-center gap-4 text-[10px] text-slate-600">
            <span>ADB: emulator-{5554 + inst.index * 2}</span>
            <span>组: {inst.group}</span>
            <span>角色: {isCaptain ? "队长" : "队员"}</span>
          </div>
        </div>
      )}
    </div>
  );
}
