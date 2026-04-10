import { useEffect, useState, useRef } from "react";
import { useAppStore, type InstanceState } from "../stores/appStore";

interface Emulator {
  index: number;
  name: string;
  running: boolean;
  adb_serial: string;
}

const STEPS = [
  { key: "accelerator", label: "加速器" },
  { key: "launch_game", label: "启动游戏" },
  { key: "dismiss_popups", label: "弹窗清理" },
  { key: "lobby", label: "大厅" },
];

function stepIdx(state: string): number {
  const i = STEPS.findIndex(s => s.key === state);
  if (state === "done" || state === "lobby") return STEPS.length;
  if (state === "wait_login") return 1;
  return i >= 0 ? i : -1;
}

// ── 主面板 ──

export function Dashboard() {
  const instances = useAppStore(s => s.instances);
  const running = useAppStore(s => s.running);
  const [emus, setEmus] = useState<Emulator[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);

  async function load() {
    try { const r = await fetch("/api/emulators"); const j = await r.json(); setEmus(j.instances || []); } catch {}
    setLoading(false);
  }

  const all: InstanceState[] = Object.values(instances).sort((a, b) => a.index - b.index);
  if (running && all.length > 0) return <RunningView instances={all} />;
  return <IdleView emulators={emus} loading={loading} onRefresh={load} />;
}

// ── 运行视图 ──

function RunningView({ instances }: { instances: InstanceState[] }) {
  const stats = useAppStore(s => s.stats);
  const groupA = instances.filter(i => i.group === "A");
  const groupB = instances.filter(i => i.group === "B");

  const pipeStats = STEPS.map((s, i) => ({
    ...s,
    done: instances.filter(inst => stepIdx(inst.state) > i).length,
    total: instances.length,
  }));

  return (
    <div className="p-6 space-y-5 animate-in">
      {/* 全局进度 */}
      <div className="card p-4">
        <div className="flex items-center justify-between mb-3">
          <span className="text-xs font-medium" style={{ color: "var(--text-muted)" }}>全局进度</span>
          <span className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>
            {stats.running_duration > 0 ? `${Math.floor(stats.running_duration / 60)}m${Math.round(stats.running_duration % 60)}s` : ""}
          </span>
        </div>
        <div className="flex gap-3">
          {pipeStats.map((p, i) => (
            <div key={p.key} className="flex-1">
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-[11px]" style={{ color: "var(--text-secondary)" }}>{p.label}</span>
                <span className="text-[11px] font-semibold" style={{
                  color: p.done === p.total ? "var(--success)" : p.done > 0 ? "var(--accent)" : "var(--text-muted)"
                }}>{p.done}/{p.total}</span>
              </div>
              <div className="progress-track">
                <div className="progress-fill" style={{
                  width: `${p.total > 0 ? (p.done / p.total) * 100 : 0}%`,
                  background: p.done === p.total ? "var(--success)" : "var(--accent)",
                }} />
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* 队伍 */}
      <div className="grid grid-cols-2 gap-5">
        {groupA.length > 0 && <TeamPanel group="A" instances={groupA} />}
        {groupB.length > 0 && <TeamPanel group="B" instances={groupB} />}
      </div>
    </div>
  );
}

// ── 队伍面板 ──

function TeamPanel({ group, instances }: { group: string; instances: InstanceState[] }) {
  const captain = instances.find(i => i.role === "captain");
  const members = instances.filter(i => i.role !== "captain");
  const allReady = instances.every(i => i.state === "lobby" || i.state === "done");

  return (
    <div className="card overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2.5 border-b" style={{ borderColor: "var(--border)" }}>
        <div className="flex items-center gap-2">
          <div className="w-1 h-3.5 rounded-full" style={{ background: group === "A" ? "var(--accent)" : "var(--warning)" }} />
          <span className="text-xs font-semibold">{group} 队</span>
          <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>{instances.length} 机器人 + 2 真人</span>
        </div>
        {allReady && <span className="tag" style={{ background: "var(--success-light)", color: "var(--success)" }}>全员就绪</span>}
      </div>

      <div className="p-3 space-y-2">
        {captain && <CaptainCard inst={captain} />}
        <div className="grid grid-cols-2 gap-2">
          {members.map(m => <MemberCard key={m.index} inst={m} />)}
        </div>
        <div className="grid grid-cols-2 gap-2">
          <SlotCard label="真人 1" />
          <SlotCard label="真人 2" />
        </div>
      </div>
    </div>
  );
}

// ── 队长卡片 ──

function CaptainCard({ inst }: { inst: InstanceState }) {
  const [imgKey, setImgKey] = useState(0);
  const imgRef = useRef<HTMLImageElement>(null);
  const running = useAppStore(s => s.running);

  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => setImgKey(k => k + 1), 3000);
    return () => clearInterval(t);
  }, [running]);

  const cur = stepIdx(inst.state);
  const meta = getMeta(inst.state);

  return (
    <div className="card overflow-hidden">
      <div className="flex">
        {/* 截图（固定宽高比，不抖动） */}
        <div className="screenshot-wrap w-[200px] shrink-0">
          <img
            ref={imgRef}
            src={`/api/screenshot/${inst.index}?t=${imgKey}`}
            alt=""
            onLoad={() => { if (imgRef.current) imgRef.current.style.opacity = "1"; }}
            onError={() => { if (imgRef.current) imgRef.current.style.opacity = "0.15"; }}
            style={{ transition: "opacity 0.3s" }}
          />
        </div>

        <div className="flex-1 p-3 flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <span className="tag" style={{ background: "var(--warning-light)", color: "var(--warning)" }}>队长</span>
                <span className="text-xs font-medium">{inst.nickname || `实例${inst.index}`}</span>
                <span className="text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>#{inst.index}</span>
              </div>
              <span className="tag" style={{ background: meta.bg, color: meta.color }}>{meta.label}</span>
            </div>

            {/* 步骤进度 */}
            <div className="flex items-center gap-1">
              {STEPS.map((s, i) => (
                <div key={s.key} className="flex items-center flex-1 gap-1">
                  <div className={`w-5 h-5 rounded-md flex items-center justify-center text-[10px] font-medium ${
                    i < cur ? "bg-green-50 text-green-600"
                    : i === cur ? "bg-blue-50 text-blue-600 animate-pulse"
                    : "bg-neutral-50 text-neutral-300"
                  }`}>
                    {i < cur ? (
                      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3"><polyline points="20,6 9,17 4,12" /></svg>
                    ) : (i + 1)}
                  </div>
                  {i < STEPS.length - 1 && (
                    <div className="h-[2px] flex-1 rounded" style={{ background: i < cur ? "#bbf7d0" : "#f5f5f5" }} />
                  )}
                </div>
              ))}
            </div>
          </div>

          <div className="flex items-center justify-between mt-2">
            <span className="text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>emulator-{5554 + inst.index * 2}</span>
            {inst.state_duration > 0 && <span className="text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>{Math.round(inst.state_duration)}s</span>}
          </div>
          {inst.error && <div className="text-[10px] mt-1 truncate" style={{ color: "var(--danger)" }}>{inst.error}</div>}
        </div>
      </div>
    </div>
  );
}

// ── 队员卡片 ──

function MemberCard({ inst }: { inst: InstanceState }) {
  const [show, setShow] = useState(false);
  const [imgKey, setImgKey] = useState(0);
  const imgRef = useRef<HTMLImageElement>(null);
  const running = useAppStore(s => s.running);

  useEffect(() => {
    if (!show || !running) return;
    const t = setInterval(() => setImgKey(k => k + 1), 3000);
    return () => clearInterval(t);
  }, [show, running]);

  const cur = stepIdx(inst.state);
  const meta = getMeta(inst.state);

  return (
    <div className="card overflow-hidden cursor-pointer" onClick={() => setShow(!show)}>
      <div className="p-2.5">
        <div className="flex items-center justify-between mb-1.5">
          <div className="flex items-center gap-1.5">
            <span className="text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>#{inst.index}</span>
            <span className="text-[11px]" style={{ color: "var(--text-secondary)" }}>{inst.nickname || "队员"}</span>
          </div>
          <span className="tag" style={{ background: meta.bg, color: meta.color, fontSize: 10 }}>{meta.label}</span>
        </div>
        <div className="flex gap-0.5">
          {STEPS.map((_, i) => (
            <div key={i} className="progress-track flex-1" style={{ height: 3 }}>
              <div className="progress-fill" style={{
                width: i < cur ? "100%" : i === cur ? "50%" : "0%",
                background: i < cur ? "var(--success)" : "var(--accent)",
              }} />
            </div>
          ))}
        </div>
        {inst.error && <div className="text-[9px] mt-1 truncate" style={{ color: "var(--danger)" }}>{inst.error}</div>}
      </div>
      {show && running && (
        <div className="screenshot-wrap border-t" style={{ borderColor: "var(--border)" }}>
          <img ref={imgRef} src={`/api/screenshot/${inst.index}?t=${imgKey}`} alt=""
            onLoad={() => { if (imgRef.current) imgRef.current.style.opacity = "1"; }}
            onError={() => { if (imgRef.current) imgRef.current.style.opacity = "0.15"; }}
            style={{ transition: "opacity 0.3s" }} />
        </div>
      )}
    </div>
  );
}

function SlotCard({ label }: { label: string }) {
  return (
    <div className="rounded-lg border border-dashed px-3 py-2 flex items-center justify-between" style={{ borderColor: "#e5e5e5" }}>
      <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>{label}</span>
      <span className="text-[10px]" style={{ color: "#d4d4d4" }}>待加入</span>
    </div>
  );
}

// ── 空闲视图 ──

function IdleView({ emulators, loading, onRefresh }: { emulators: Emulator[]; loading: boolean; onRefresh: () => void }) {
  const [testing, setTesting] = useState<number | null>(null);

  async function test(idx: number) {
    setTesting(idx);
    try {
      const r = await fetch(`/api/start/${idx}`, { method: "POST" });
      const j = await r.json();
      if (j.ok) useAppStore.getState().setRunning(true);
      else alert(j.error || "启动失败");
    } catch { alert("连接失败"); }
    setTesting(null);
  }

  const online = emulators.filter(e => e.running);
  const offline = emulators.filter(e => !e.running);

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-6 animate-in">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold">模拟器</h2>
          <p className="text-xs mt-0.5" style={{ color: "var(--text-muted)" }}>
            {loading ? "扫描中..." : `${emulators.length} 个实例，${online.length} 个在线`}
          </p>
        </div>
        <button className="btn btn-ghost text-xs" onClick={onRefresh}>刷新</button>
      </div>

      {online.length > 0 && (
        <div>
          <div className="text-[11px] mb-2 flex items-center gap-1.5" style={{ color: "var(--text-muted)" }}>
            <span className="dot dot-success" /> 在线
          </div>
          <div className="grid grid-cols-3 gap-3">
            {online.map(e => (
              <div key={e.index} className="card p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>#{e.index}</span>
                    <span className="text-sm font-medium">{e.name}</span>
                  </div>
                  <span className="dot dot-success" />
                </div>
                <span className="text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>{e.adb_serial}</span>
                <button className="btn btn-primary w-full justify-center" onClick={() => test(e.index)} disabled={testing !== null}>
                  {testing === e.index ? "启动中..." : "测试运行"}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {offline.length > 0 && (
        <div>
          <div className="text-[11px] mb-2 flex items-center gap-1.5" style={{ color: "var(--text-muted)" }}>
            <span className="dot dot-idle" /> 离线
          </div>
          <div className="grid grid-cols-3 gap-3">
            {offline.map(e => (
              <div key={e.index} className="card p-4 opacity-50">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>#{e.index}</span>
                  <span className="text-sm" style={{ color: "var(--text-secondary)" }}>{e.name}</span>
                </div>
                <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>请在多开器中启动</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {emulators.length === 0 && !loading && (
        <div className="card p-8 text-center">
          <p className="text-sm" style={{ color: "var(--text-secondary)" }}>未检测到模拟器</p>
          <p className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>请在设置中确认雷电模拟器路径</p>
        </div>
      )}
    </div>
  );
}

// ── 工具 ──

function getMeta(state: string): { label: string; color: string; bg: string } {
  const map: Record<string, { label: string; color: string; bg: string }> = {
    init: { label: "等待", color: "var(--text-muted)", bg: "var(--bg-subtle)" },
    accelerator: { label: "加速器", color: "var(--accent)", bg: "var(--accent-light)" },
    launch_game: { label: "启动中", color: "var(--accent)", bg: "var(--accent-light)" },
    wait_login: { label: "登录", color: "var(--accent)", bg: "var(--accent-light)" },
    dismiss_popups: { label: "清弹窗", color: "var(--warning)", bg: "var(--warning-light)" },
    lobby: { label: "就绪", color: "var(--success)", bg: "var(--success-light)" },
    done: { label: "完成", color: "var(--success)", bg: "var(--success-light)" },
    error: { label: "错误", color: "var(--danger)", bg: "var(--danger-light)" },
  };
  return map[state] ?? map.init;
}
