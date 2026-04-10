import { useState } from "react";
import { useAppStore } from "../stores/appStore";

async function api(method: string, path: string, body?: unknown) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  return res.json();
}

function formatDuration(secs: number) {
  if (secs <= 0) return "0s";
  const m = Math.floor(secs / 60);
  const s = Math.round(secs % 60);
  if (m === 0) return `${s}s`;
  return `${m}m${s}s`;
}

export function Header() {
  const {
    running, activeView, setActiveView, showLogs, setShowLogs,
    instances, stats, setRunning, setInstances
  } = useAppStore();
  const [loading, setLoading] = useState("");

  const all = Object.values(instances);
  const total = all.length;
  const activeCount = all.filter(
    (i) => !["init", "done", "error", "lobby"].includes(i.state)
  ).length;
  const readyCount = all.filter(
    (i) => i.state === "lobby" || i.state === "done"
  ).length;
  const errorCount = all.filter((i) => i.state === "error").length;

  async function handleStart() {
    setLoading("start");
    await api("POST", "/api/start");
    setRunning(true);
    setLoading("");
  }

  async function handleStop() {
    setLoading("stop");
    await api("POST", "/api/stop");
    setRunning(false);
    setInstances({});
    setLoading("");
  }

  return (
    <header className="shrink-0 glass border-b border-slate-800/60">
      <div className="flex items-center justify-between px-5 py-3">
        {/* 左: Logo + 导航 */}
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg flex items-center justify-center"
              style={{ background: "linear-gradient(135deg, #3b82f6, #8b5cf6)" }}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5">
                <path d="M12 2L2 7l10 5 10-5-10-5z" />
                <path d="M2 17l10 5 10-5" />
                <path d="M2 12l10 5 10-5" />
              </svg>
            </div>
            <span className="text-sm font-bold tracking-wide gradient-text">GameBot</span>
          </div>

          <nav className="flex items-center gap-1">
            <NavButton
              active={activeView === "dashboard"}
              onClick={() => setActiveView("dashboard")}
              icon={
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <rect x="3" y="3" width="7" height="7" rx="1" />
                  <rect x="14" y="3" width="7" height="7" rx="1" />
                  <rect x="3" y="14" width="7" height="7" rx="1" />
                  <rect x="14" y="14" width="7" height="7" rx="1" />
                </svg>
              }
              label="控制面板"
            />
            <NavButton
              active={activeView === "settings"}
              onClick={() => setActiveView("settings")}
              icon={
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="12" cy="12" r="3" />
                  <path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42" />
                </svg>
              }
              label="设置"
            />
          </nav>
        </div>

        {/* 中: 状态指标 */}
        {running && total > 0 && (
          <div className="flex items-center gap-5 text-xs">
            <StatusPill label="运行中" value={`${activeCount}/${total}`} color="blue" />
            <StatusPill label="已就绪" value={`${readyCount}/${total}`} color="green" />
            {errorCount > 0 && <StatusPill label="出错" value={String(errorCount)} color="red" />}
            <span className="text-slate-500 font-mono">{formatDuration(stats.running_duration)}</span>
          </div>
        )}

        {/* 右: 操作按钮 */}
        <div className="flex items-center gap-3">
          <button
            className="px-3 py-1.5 rounded-lg text-xs text-slate-400 hover:text-white hover:bg-slate-800/50 transition-colors"
            onClick={() => setShowLogs(!showLogs)}
          >
            {showLogs ? "隐藏日志" : "显示日志"}
          </button>

          {!running ? (
            <button className="btn-primary" onClick={handleStart} disabled={loading === "start"}>
              {loading === "start" ? (
                <span className="flex items-center gap-2">
                  <Spinner /> 启动中...
                </span>
              ) : (
                <span className="flex items-center gap-2">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                    <polygon points="5,3 19,12 5,21" />
                  </svg>
                  启动全部
                </span>
              )}
            </button>
          ) : (
            <button className="btn-danger" onClick={handleStop} disabled={loading === "stop"}>
              {loading === "stop" ? (
                <span className="flex items-center gap-2"><Spinner /> 停止中...</span>
              ) : (
                <span className="flex items-center gap-2">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                    <rect x="4" y="4" width="16" height="16" rx="2" />
                  </svg>
                  停止
                </span>
              )}
            </button>
          )}
        </div>
      </div>
    </header>
  );
}

function NavButton({
  active, onClick, icon, label,
}: {
  active: boolean; onClick: () => void; icon: React.ReactNode; label: string;
}) {
  return (
    <button
      className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
        active
          ? "bg-slate-800/80 text-white shadow-sm"
          : "text-slate-400 hover:text-slate-200 hover:bg-slate-800/40"
      }`}
      onClick={onClick}
    >
      {icon}
      {label}
    </button>
  );
}

function StatusPill({ label, value, color }: { label: string; value: string; color: "blue" | "green" | "red" }) {
  const colors = {
    blue: "bg-blue-500/10 text-blue-400 border-blue-500/20",
    green: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
    red: "bg-red-500/10 text-red-400 border-red-500/20",
  };
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border ${colors[color]}`}>
      <span className="text-slate-500">{label}</span>
      <span className="font-bold">{value}</span>
    </span>
  );
}

function Spinner() {
  return (
    <svg className="animate-spin" width="14" height="14" viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" strokeDasharray="31.4 31.4" strokeLinecap="round" />
    </svg>
  );
}
