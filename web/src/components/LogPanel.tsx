import { useEffect, useRef } from "react";
import { useAppStore } from "../stores/appStore";

export function LogPanel() {
  const { logs, logFilter, setLogFilter, clearLogs, setShowLogs } = useAppStore();
  const bottomRef = useRef<HTMLDivElement>(null);

  const filtered = logFilter === null ? logs : logs.filter(l => l.instance === logFilter || l.instance === -1);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [filtered.length]);

  function fmt(ts: number) {
    return new Date(ts * 1000).toLocaleTimeString("zh-CN", { hour12: false });
  }

  const levelColor: Record<string, string> = {
    info: "var(--text-secondary)",
    warning: "var(--warning)",
    warn: "var(--warning)",
    error: "var(--danger)",
  };

  return (
    <div className="flex flex-col h-full" style={{ background: "white" }}>
      <div className="flex items-center justify-between px-3 py-2 border-b" style={{ borderColor: "var(--border)" }}>
        <span className="text-[11px] font-medium" style={{ color: "var(--text-muted)" }}>日志</span>
        <div className="flex gap-1">
          <button className="text-[10px] px-1.5 py-0.5 rounded hover:bg-neutral-100" style={{ color: "var(--text-muted)" }} onClick={clearLogs}>清空</button>
          <button className="text-[10px] px-1.5 py-0.5 rounded hover:bg-neutral-100" style={{ color: "var(--text-muted)" }} onClick={() => setShowLogs(false)}>关闭</button>
        </div>
      </div>

      <div className="flex gap-1 px-3 py-1.5 border-b" style={{ borderColor: "var(--border)" }}>
        {[null, 0, 1, 2, 3, 4, 5].map(f => (
          <button key={f ?? "all"} onClick={() => setLogFilter(f)}
            className={`text-[10px] px-1.5 py-0.5 rounded transition-colors ${
              logFilter === f ? "bg-blue-50 text-blue-600 font-medium" : "text-neutral-400 hover:text-neutral-600"
            }`}>
            {f === null ? "全部" : f}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-2 text-[11px] font-mono space-y-px">
        {filtered.length === 0 && (
          <div className="text-center mt-10" style={{ color: "var(--text-muted)" }}>暂无日志</div>
        )}
        {filtered.map((e, i) => (
          <div key={i} className="flex items-start gap-1.5 py-[1px] leading-relaxed" style={{ color: levelColor[e.level] ?? "var(--text-secondary)" }}>
            <span style={{ color: "var(--text-muted)" }}>{fmt(e.timestamp)}</span>
            <span style={{ color: "#d4d4d4" }}>[{e.instance === -1 ? "SYS" : e.instance}]</span>
            <span className="break-all">{e.message}</span>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
