import { useEffect, useRef } from "react";
import { useAppStore } from "../stores/appStore";

const LEVEL_STYLES: Record<string, string> = {
  info: "text-slate-300",
  warning: "text-amber-400",
  warn: "text-amber-400",
  error: "text-red-400",
  debug: "text-slate-500",
};

const LEVEL_DOTS: Record<string, string> = {
  info: "bg-blue-400/60",
  warning: "bg-amber-400/60",
  warn: "bg-amber-400/60",
  error: "bg-red-400/60",
  debug: "bg-slate-600",
};

export function LogPanel() {
  const { logs, logFilter, setLogFilter, clearLogs, setShowLogs } = useAppStore();
  const bottomRef = useRef<HTMLDivElement>(null);

  const filtered =
    logFilter === null
      ? logs
      : logs.filter((l) => l.instance === logFilter || l.instance === -1);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [filtered.length]);

  function formatTime(ts: number) {
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString("zh-CN", { hour12: false });
  }

  return (
    <div className="flex flex-col h-full" style={{ background: "rgba(8, 12, 24, 0.8)" }}>
      {/* 顶栏 */}
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-slate-800/60">
        <div className="flex items-center gap-2">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#475569" strokeWidth="2">
            <path d="M4 19.5A2.5 2.5 0 016.5 17H20" />
            <path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z" />
          </svg>
          <span className="text-xs font-bold text-slate-500 tracking-wider">日志</span>
          <span className="text-[10px] text-slate-600">{filtered.length}</span>
        </div>
        <div className="flex items-center gap-1">
          <button
            className="text-[10px] text-slate-600 hover:text-slate-400 px-1.5 py-0.5 rounded"
            onClick={clearLogs}
          >
            清空
          </button>
          <button
            className="text-[10px] text-slate-600 hover:text-slate-400 px-1.5 py-0.5 rounded"
            onClick={() => setShowLogs(false)}
          >
            关闭
          </button>
        </div>
      </div>

      {/* 过滤器 */}
      <div className="flex items-center gap-1 px-3 py-1.5 border-b border-slate-800/40">
        <FilterBtn active={logFilter === null} onClick={() => setLogFilter(null)} label="全部" />
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <FilterBtn key={i} active={logFilter === i} onClick={() => setLogFilter(i)} label={`${i}`} />
        ))}
      </div>

      {/* 日志内容 */}
      <div className="flex-1 overflow-y-auto px-3 py-2 text-[11px] font-mono space-y-px">
        {filtered.length === 0 && (
          <div className="text-slate-700 text-center mt-12">暂无日志</div>
        )}
        {filtered.map((entry, i) => (
          <div key={i} className={`flex items-start gap-1.5 py-0.5 ${LEVEL_STYLES[entry.level] ?? "text-slate-400"}`}>
            <span className={`shrink-0 w-1.5 h-1.5 rounded-full mt-1 ${LEVEL_DOTS[entry.level] ?? "bg-slate-600"}`} />
            <span className="text-slate-600 shrink-0">{formatTime(entry.timestamp)}</span>
            <span className="text-slate-600 shrink-0">
              [{entry.instance === -1 ? "SYS" : entry.instance}]
            </span>
            <span className="break-all">{entry.message}</span>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

function FilterBtn({ active, onClick, label }: { active: boolean; onClick: () => void; label: string }) {
  return (
    <button
      className={`text-[10px] px-1.5 py-0.5 rounded transition-colors ${
        active
          ? "bg-indigo-500/15 text-indigo-400 border border-indigo-500/20"
          : "text-slate-600 hover:text-slate-400"
      }`}
      onClick={onClick}
    >
      {label}
    </button>
  );
}
