import { useEffect, useRef } from "react";
import { useAppStore } from "../stores/appStore";

const LEVEL_COLORS: Record<string, string> = {
  info: "text-slate-300",
  warn: "text-yellow-400",
  error: "text-red-400",
};

export function LogViewer() {
  const { logs, logFilter, setLogFilter, clearLogs } = useAppStore();
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
    <div className="flex flex-col h-full">
      {/* 过滤栏 */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-slate-700 shrink-0">
        <span className="text-xs text-slate-500">过滤:</span>
        <button
          className={`text-xs px-2 py-0.5 rounded ${logFilter === null ? "bg-slate-600 text-white" : "text-slate-400 hover:text-white"}`}
          onClick={() => setLogFilter(null)}
        >
          全部
        </button>
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <button
            key={i}
            className={`text-xs px-2 py-0.5 rounded ${logFilter === i ? "bg-slate-600 text-white" : "text-slate-400 hover:text-white"}`}
            onClick={() => setLogFilter(i)}
          >
            {i}
          </button>
        ))}
        <div className="flex-1" />
        <button
          className="text-xs text-slate-500 hover:text-red-400"
          onClick={clearLogs}
        >
          清空
        </button>
      </div>

      {/* 日志列表 */}
      <div className="flex-1 overflow-y-auto p-3 text-xs font-mono space-y-0.5">
        {filtered.length === 0 && (
          <div className="text-slate-600 text-center mt-8">暂无日志</div>
        )}
        {filtered.map((entry, i) => (
          <div key={i} className={LEVEL_COLORS[entry.level] ?? "text-slate-300"}>
            <span className="text-slate-600">{formatTime(entry.timestamp)}</span>
            {" "}
            <span className="text-slate-500">
              [{entry.instance === -1 ? "协调" : `实例${entry.instance}`}]
            </span>
            {" "}
            {entry.message}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
