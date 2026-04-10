import { useEffect, useState } from "react";

interface SettingsData { ldplayer_path: string; adb_path: string; game_package: string; game_mode: string; game_map: string; }
interface AccountData { qq: string; nickname: string; game_id: string; group: string; role: string; instance_index: number; }
interface Emulator { index: number; name: string; running: boolean; adb_serial: string; }

export function Settings() {
  const [data, setData] = useState<SettingsData | null>(null);
  const [accounts, setAccounts] = useState<AccountData[]>([]);
  const [emus, setEmus] = useState<Emulator[]>([]);
  const [detecting, setDetecting] = useState(false);
  const [msg, setMsg] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetch("/api/settings").then(r => r.json()).then(setData);
    fetch("/api/accounts").then(r => r.json()).then(setAccounts);
    detect();
  }, []);

  async function detect() {
    setDetecting(true);
    try { const r = await fetch("/api/emulators"); const j = await r.json(); setEmus(j.instances || []); } catch {}
    setDetecting(false);
  }

  async function save(type: "settings" | "accounts") {
    setSaving(true); setMsg("");
    const r = await fetch(type === "settings" ? "/api/settings" : "/api/accounts", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(type === "settings" ? data : accounts),
    });
    const j = await r.json();
    setSaving(false); setMsg(j.ok ? "已保存" : "失败"); setTimeout(() => setMsg(""), 2000);
  }

  function upd(i: number, f: keyof AccountData, v: string | number) {
    setAccounts(p => p.map((a, idx) => idx === i ? { ...a, [f]: v } : a));
  }

  function sync() {
    const half = Math.ceil(emus.length / 2);
    setAccounts(emus.map((e, i) => {
      const ex = accounts.find(a => a.instance_index === e.index);
      if (ex) return ex;
      return { qq: "", nickname: e.name, game_id: "", group: i < half ? "A" : "B", role: (i === 0 || i === half) ? "captain" : "member", instance_index: e.index };
    }));
    setMsg("已同步"); setTimeout(() => setMsg(""), 2000);
  }

  if (!data) return <div className="p-6" style={{ color: "var(--text-muted)" }}>加载中...</div>;
  const emuMap = Object.fromEntries(emus.map(e => [e.index, e]));

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-8 animate-in">
      {/* 环境 */}
      <Section title="环境配置">
        <div className="grid grid-cols-2 gap-4">
          <Field label="雷电模拟器路径" hint="LDPlayer 安装目录">
            <input className="input" value={data.ldplayer_path} onChange={e => setData({ ...data, ldplayer_path: e.target.value })} placeholder="D:\leidian\LDPlayer9" />
          </Field>
          <Field label="ADB 路径" hint="留空自动检测">
            <input className="input" value={data.adb_path} onChange={e => setData({ ...data, adb_path: e.target.value })} placeholder="自动" />
          </Field>
          <Field label="游戏包名"><input className="input" value={data.game_package} onChange={e => setData({ ...data, game_package: e.target.value })} /></Field>
          <Field label="目标地图"><input className="input" value={data.game_map} onChange={e => setData({ ...data, game_map: e.target.value })} placeholder="狙击团竞" /></Field>
        </div>
        <div className="mt-4 flex items-center gap-3">
          <button className="btn btn-primary" onClick={() => save("settings")} disabled={saving}>保存</button>
          {msg && <span className="text-xs" style={{ color: "var(--success)" }}>{msg}</span>}
        </div>
      </Section>

      {/* 模拟器检测 */}
      <Section title="模拟器检测">
        <div className="flex items-center gap-3 mb-3">
          <button className="btn btn-ghost text-xs" onClick={detect} disabled={detecting}>{detecting ? "检测中..." : "刷新"}</button>
          {emus.length > 0 && <button className="btn btn-ghost text-xs" onClick={sync}>同步到分配表</button>}
          <span className="text-xs" style={{ color: "var(--text-muted)" }}>{emus.length > 0 ? `${emus.length} 个实例` : "未检测到"}</span>
        </div>
        {emus.length > 0 && (
          <div className="grid grid-cols-4 gap-2">
            {emus.map(e => (
              <div key={e.index} className="card p-3 flex items-center gap-2">
                <span className={`dot ${e.running ? "dot-success" : "dot-idle"}`} />
                <div className="flex-1 min-w-0">
                  <div className="text-xs font-medium truncate">{e.name}</div>
                  <div className="text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>{e.adb_serial}</div>
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>

      {/* 分配表 */}
      <Section title="实例分配">
        {accounts.length > 0 ? (
          <div className="card overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-[11px] text-left border-b" style={{ color: "var(--text-muted)", borderColor: "var(--border)", background: "var(--bg-subtle)" }}>
                  <th className="px-3 py-2 font-medium">模拟器</th>
                  <th className="px-3 py-2 font-medium">状态</th>
                  <th className="px-3 py-2 font-medium">ADB</th>
                  <th className="px-3 py-2 font-medium">组</th>
                  <th className="px-3 py-2 font-medium">角色</th>
                  <th className="px-3 py-2 font-medium">昵称</th>
                  <th className="px-3 py-2 font-medium">游戏ID</th>
                  <th className="px-3 py-2 w-8"></th>
                </tr>
              </thead>
              <tbody>
                {accounts.map((a, i) => {
                  const e = emuMap[a.instance_index];
                  return (
                    <tr key={i} className="border-b last:border-0 hover:bg-neutral-50 transition-colors" style={{ borderColor: "var(--border)" }}>
                      <td className="px-3 py-2">
                        <span className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>#{a.instance_index}</span>
                        {" "}<span className="text-xs">{e?.name || `实例${a.instance_index}`}</span>
                      </td>
                      <td className="px-3 py-2">
                        <span className={`tag ${e?.running ? "" : ""}`} style={{
                          background: e?.running ? "var(--success-light)" : "var(--bg-subtle)",
                          color: e?.running ? "var(--success)" : "var(--text-muted)",
                        }}>{e?.running ? "在线" : e ? "离线" : "未知"}</span>
                      </td>
                      <td className="px-3 py-2"><span className="text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>:{5554 + a.instance_index * 2}</span></td>
                      <td className="px-3 py-2">
                        <select className="input text-xs w-16 text-center" value={a.group} onChange={e => upd(i, "group", e.target.value)}>
                          <option value="A">A</option><option value="B">B</option>
                        </select>
                      </td>
                      <td className="px-3 py-2">
                        <select className="input text-xs w-20" value={a.role} onChange={e => upd(i, "role", e.target.value)}>
                          <option value="captain">队长</option><option value="member">队员</option>
                        </select>
                      </td>
                      <td className="px-3 py-2"><input className="input text-xs w-24" value={a.nickname} onChange={e => upd(i, "nickname", e.target.value)} /></td>
                      <td className="px-3 py-2"><input className="input text-xs w-24" value={a.game_id} onChange={e => upd(i, "game_id", e.target.value)} placeholder="校验用" /></td>
                      <td className="px-3 py-2">
                        <button className="text-neutral-300 hover:text-red-400 transition-colors" onClick={() => setAccounts(p => p.filter((_, idx) => idx !== i))}>
                          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="card p-6 text-center text-sm" style={{ color: "var(--text-muted)" }}>点击上方「同步到分配表」生成配置</div>
        )}
        <div className="mt-3 flex items-center gap-3">
          <button className="btn btn-primary" onClick={() => save("accounts")} disabled={saving}>保存分配</button>
        </div>
      </Section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="text-sm font-semibold mb-3">{title}</h3>
      {children}
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs font-medium mb-1" style={{ color: "var(--text-secondary)" }}>{label}</label>
      {children}
      {hint && <p className="text-[10px] mt-1" style={{ color: "var(--text-muted)" }}>{hint}</p>}
    </div>
  );
}
