import { useState, useEffect } from "react";
import { useAppStore, type InstanceState } from "../stores/appStore";

const PHASES = [
  { key: "accelerator", label: "加速器" },
  { key: "launch_game", label: "启动" },
  { key: "dismiss_popups", label: "弹窗" },
  { key: "lobby", label: "大厅" },
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

  // 自动刷新截图
  useEffect(() => {
    if (!isExpanded || !running) return;
    const timer = setInterval(() => setImgKey((k) => k + 1), 2000);
    return () => clearInterval(timer);
  }, [isExpanded, running]);

  return (
    <div
      className={`glass-card rounded-xl p-3.5 cursor-pointer transition-all ${
        isExpanded ? "ring-1 ring-indigo-500/30" : ""
      }`}
      onClick={() => setSelectedInstance(isExpanded ? null : inst.index)}
    >
      {/* 顶部: 编号 + 角色 + 状态 */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-xs font-mono text-slate-500">#{inst.index}</span>
          {isCaptain && (
            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/20 font-medium">
              队长
            </span>
          )}
          <span className="text-xs text-slate-400 truncate max-w-[80px]">
            {inst.nickname || `账号${inst.index + 1}`}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className={`step-dot ${meta.dot} ${isWorking ? "animate-pulse-soft" : ""}`} />
          <span className={`text-xs font-medium ${meta.color}`}>{meta.label}</span>
        </div>
      </div>

      {/* 进度条 */}
      {running && (
        <div className="flex items-center gap-1 mb-2">
          {PHASES.map((phase, i) => (
            <div key={phase.key} className="flex items-center flex-1 gap-1">
              <div
                className={`step-dot ${
                  i < currentPhase
                    ? "dot-success"
                    : i === currentPhase
                    ? "dot-running animate-pulse-soft"
                    : "dot-idle"
                }`}
                title={phase.label}
              />
              {i < PHASES.length - 1 && (
                <div className={`step-line ${i < currentPhase ? "active" : ""}`} />
              )}
            </div>
          ))}
        </div>
      )}

      {/* 耗时 */}
      {inst.state_duration > 0 && (
        <div className="text-[10px] text-slate-600 font-mono">
          {Math.round(inst.state_duration)}s
        </div>
      )}

      {/* 错误 */}
      {inst.error && (
        <div className="text-[11px] text-red-400/80 mt-1 truncate" title={inst.error}>
          {inst.error}
        </div>
      )}

      {/* 展开: 实时截图 */}
      {isExpanded && running && (
        <div className="mt-3 animate-slide-in">
          <img
            key={imgKey}
            src={`/api/screenshot/${inst.index}?t=${imgKey}`}
            className="w-full rounded-lg border border-slate-700/50"
            alt={`实例${inst.index}截图`}
            onError={(e) => {
              (e.target as HTMLImageElement).style.opacity = "0.3";
            }}
          />
        </div>
      )}
    </div>
  );
}
