import { useAppStore } from "../stores/appStore";

export function StatsBar() {
  const stats = useAppStore((s) => s.stats);

  function formatDuration(secs: number) {
    if (secs < 60) return `${Math.round(secs)}s`;
    const m = Math.floor(secs / 60);
    const s = Math.round(secs % 60);
    return `${m}m${s}s`;
  }

  return (
    <div className="flex items-center gap-6 text-sm">
      <div>
        <span className="text-slate-500">匹配</span>{" "}
        <span className="font-bold">{stats.total_attempts}</span>
      </div>
      <div>
        <span className="text-slate-500">成功</span>{" "}
        <span className="font-bold text-emerald-400">{stats.success_count}</span>
      </div>
      <div>
        <span className="text-slate-500">重试</span>{" "}
        <span className="font-bold text-yellow-400">{stats.abort_count}</span>
      </div>
      <div>
        <span className="text-slate-500">错误</span>{" "}
        <span className="font-bold text-red-400">{stats.error_count}</span>
      </div>
      <div>
        <span className="text-slate-500">运行</span>{" "}
        <span className="font-mono">{formatDuration(stats.running_duration)}</span>
      </div>
    </div>
  );
}
