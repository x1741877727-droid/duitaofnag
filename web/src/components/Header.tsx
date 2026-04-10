import { useState } from "react";
import { useAppStore } from "../stores/appStore";

async function api(method: string, path: string) {
  const res = await fetch(path, { method });
  return res.json();
}

export function Header() {
  const { running, activeView, setActiveView, showLogs, setShowLogs, instances, stats, setRunning, setInstances } = useAppStore();
  const [loading, setLoading] = useState("");

  const all = Object.values(instances);
  const total = all.length;
  const active = all.filter(i => !["init","done","error","lobby"].includes(i.state)).length;
  const ready = all.filter(i => i.state === "lobby" || i.state === "done").length;
  const errors = all.filter(i => i.state === "error").length;

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

  function fmt(s: number) {
    if (s <= 0) return "0s";
    const m = Math.floor(s / 60);
    return m > 0 ? `${m}m${Math.round(s % 60)}s` : `${Math.round(s)}s`;
  }

  return (
    <header className="shrink-0 border-b" style={{ borderColor: "var(--border)", background: "white" }}>
      <div className="flex items-center justify-between px-5 h-12">
        {/* 左 */}
        <div className="flex items-center gap-5">
          <span className="text-sm font-semibold tracking-tight" style={{ color: "var(--text-primary)" }}>GameBot</span>
          <nav className="flex gap-1">
            {(["dashboard", "settings"] as const).map(v => (
              <button key={v} onClick={() => setActiveView(v)}
                className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${
                  activeView === v ? "bg-neutral-100 text-neutral-900" : "text-neutral-500 hover:text-neutral-700"
                }`}>
                {v === "dashboard" ? "控制面板" : "设置"}
              </button>
            ))}
          </nav>
        </div>

        {/* 中 */}
        {running && total > 0 && (
          <div className="flex items-center gap-4 text-xs" style={{ color: "var(--text-secondary)" }}>
            <Stat label="运行" value={`${active}/${total}`} color="var(--accent)" />
            <Stat label="就绪" value={`${ready}/${total}`} color="var(--success)" />
            {errors > 0 && <Stat label="错误" value={`${errors}`} color="var(--danger)" />}
            <span className="font-mono text-neutral-400">{fmt(stats.running_duration)}</span>
          </div>
        )}

        {/* 右 */}
        <div className="flex items-center gap-2">
          <button className="btn btn-ghost text-xs py-1 px-2" onClick={() => setShowLogs(!showLogs)}>
            {showLogs ? "隐藏日志" : "日志"}
          </button>
          {!running ? (
            <button className="btn btn-primary" onClick={handleStart} disabled={!!loading}>
              {loading === "start" ? "启动中..." : "启动全部"}
            </button>
          ) : (
            <button className="btn btn-danger" onClick={handleStop} disabled={!!loading}>
              {loading === "stop" ? "停止中..." : "停止"}
            </button>
          )}
        </div>
      </div>
    </header>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className="dot" style={{ background: color, width: 5, height: 5 }} />
      <span className="text-neutral-400">{label}</span>
      <span className="font-semibold" style={{ color }}>{value}</span>
    </span>
  );
}
