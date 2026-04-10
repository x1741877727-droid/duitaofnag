import { useEffect, useState } from "react";

interface SettingsData {
  ldplayer_path: string;
  adb_path: string;
  game_package: string;
  game_mode: string;
  game_map: string;
}

interface Emulator {
  index: number;
  name: string;
  running: boolean;
  adb_serial: string;
  adb_port: number;
}

interface AccountData {
  qq: string;
  nickname: string;
  game_id: string;
  group: string;
  role: string;
  instance_index: number;
}

export function Settings() {
  const [data, setData] = useState<SettingsData | null>(null);
  const [accounts, setAccounts] = useState<AccountData[]>([]);
  const [emulators, setEmulators] = useState<Emulator[]>([]);
  const [detecting, setDetecting] = useState(false);
  const [msg, setMsg] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetch("/api/settings").then((r) => r.json()).then(setData);
    fetch("/api/accounts").then((r) => r.json()).then(setAccounts);
    detectEmulators();
  }, []);

  async function detectEmulators() {
    setDetecting(true);
    try {
      const res = await fetch("/api/emulators");
      const json = await res.json();
      setEmulators(json.instances || []);
    } catch { /* ignore */ }
    setDetecting(false);
  }

  async function save(type: "settings" | "accounts") {
    setSaving(true);
    setMsg("");
    const endpoint = type === "settings" ? "/api/settings" : "/api/accounts";
    const body = type === "settings" ? data : accounts;
    const res = await fetch(endpoint, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const json = await res.json();
    setSaving(false);
    showMsg(json.ok ? "已保存" : "保存失败");
  }

  function showMsg(text: string) {
    setMsg(text);
    setTimeout(() => setMsg(""), 2500);
  }

  function updateAccount(idx: number, field: keyof AccountData, value: string | number) {
    setAccounts((prev) => prev.map((a, i) => (i === idx ? { ...a, [field]: value } : a)));
  }

  // 从检测到的模拟器自动生成账号配置
  function syncFromEmulators() {
    const newAccounts: AccountData[] = emulators.map((emu, i) => {
      // 看看已有配置中有没有这个实例
      const existing = accounts.find((a) => a.instance_index === emu.index);
      if (existing) return existing;
      // 自动分组: 前一半A组，后一半B组
      const half = Math.ceil(emulators.length / 2);
      const group = i < half ? "A" : "B";
      // 每组第一个是队长
      const isFirstInGroup = i === 0 || i === half;
      return {
        qq: "",
        nickname: emu.name,
        game_id: "",
        group,
        role: isFirstInGroup ? "captain" : "member",
        instance_index: emu.index,
      };
    });
    setAccounts(newAccounts);
    showMsg("已从模拟器同步，请检查分组后保存");
  }

  function removeAccount(idx: number) {
    setAccounts((prev) => prev.filter((_, i) => i !== idx));
  }

  if (!data) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-sm text-slate-500">加载中...</div>
      </div>
    );
  }

  // 把模拟器信息 merge 到 account 上用于显示
  const emuMap = Object.fromEntries(emulators.map((e) => [e.index, e]));

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-8 animate-slide-in">

      {/* ── 环境配置 ── */}
      <Section icon="env" title="环境配置" desc="配置模拟器和 ADB 路径">
        <div className="grid grid-cols-2 gap-4">
          <Field label="雷电模拟器路径" hint="LDPlayer 安装目录">
            <input className="input-glass" value={data.ldplayer_path}
              onChange={(e) => setData({ ...data, ldplayer_path: e.target.value })}
              placeholder="D:\leidian\LDPlayer9" />
          </Field>
          <Field label="ADB 路径" hint="留空自动使用模拟器自带">
            <input className="input-glass" value={data.adb_path}
              onChange={(e) => setData({ ...data, adb_path: e.target.value })}
              placeholder="自动检测" />
          </Field>
          <Field label="游戏包名" hint="一般不需要修改">
            <input className="input-glass" value={data.game_package}
              onChange={(e) => setData({ ...data, game_package: e.target.value })} />
          </Field>
          <Field label="目标地图" hint="队长将自动选择此地图">
            <input className="input-glass" value={data.game_map}
              onChange={(e) => setData({ ...data, game_map: e.target.value })}
              placeholder="狙击团竞" />
          </Field>
        </div>
        <div className="mt-4 flex items-center gap-3">
          <button className="btn-primary text-xs" onClick={() => save("settings")} disabled={saving}>
            保存设置
          </button>
          {msg && <span className="text-xs text-emerald-400">{msg}</span>}
        </div>
      </Section>

      {/* ── 模拟器检测 ── */}
      <Section icon="scan" title="模拟器检测" desc="自动发现本机雷电模拟器实例">
        <div className="flex items-center gap-3 mb-3">
          <button
            className="px-3 py-1.5 rounded-lg text-xs font-medium border border-indigo-500/30 text-indigo-400 hover:bg-indigo-500/10 transition-colors disabled:opacity-40"
            onClick={detectEmulators}
            disabled={detecting}
          >
            {detecting ? "检测中..." : "刷新检测"}
          </button>
          {emulators.length > 0 && (
            <button
              className="px-3 py-1.5 rounded-lg text-xs font-medium border border-emerald-500/30 text-emerald-400 hover:bg-emerald-500/10 transition-colors"
              onClick={syncFromEmulators}
            >
              同步到实例分配
            </button>
          )}
          <span className="text-xs text-slate-600">
            {emulators.length > 0
              ? `检测到 ${emulators.length} 个模拟器`
              : "未检测到模拟器（请确认雷电模拟器路径正确）"}
          </span>
        </div>

        {emulators.length > 0 && (
          <div className="grid grid-cols-3 gap-2">
            {emulators.map((emu) => (
              <div key={emu.index} className="glass-card rounded-lg p-3 flex items-center gap-3">
                <div className={`w-2 h-2 rounded-full ${emu.running ? "dot-success" : "dot-idle"}`} />
                <div className="flex-1 min-w-0">
                  <div className="text-xs font-medium truncate">{emu.name}</div>
                  <div className="text-[10px] text-slate-600 font-mono">{emu.adb_serial}</div>
                </div>
                <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                  emu.running
                    ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                    : "bg-slate-700/50 text-slate-500 border border-slate-600/30"
                }`}>
                  {emu.running ? "运行中" : "已关闭"}
                </span>
              </div>
            ))}
          </div>
        )}
      </Section>

      {/* ── 实例分配 ── */}
      <Section icon="team" title="实例分配" desc="将模拟器分配到 A/B 组，指定队长和队员">
        <div className="overflow-hidden rounded-xl border border-slate-800/60">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-[11px] text-slate-500 uppercase tracking-wider" style={{ background: "rgba(15,23,42,0.5)" }}>
                <th className="text-left px-3 py-2.5 font-medium">模拟器</th>
                <th className="text-left px-3 py-2.5 font-medium">状态</th>
                <th className="text-left px-3 py-2.5 font-medium">ADB</th>
                <th className="text-left px-3 py-2.5 font-medium">组</th>
                <th className="text-left px-3 py-2.5 font-medium">角色</th>
                <th className="text-left px-3 py-2.5 font-medium">昵称</th>
                <th className="text-left px-3 py-2.5 font-medium">游戏ID</th>
                <th className="px-3 py-2.5 w-8"></th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((acc, i) => {
                const emu = emuMap[acc.instance_index];
                return (
                  <tr key={i} className="border-t border-slate-800/40 hover:bg-slate-800/20 transition-colors">
                    {/* 模拟器名 */}
                    <td className="px-3 py-2">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-mono text-slate-500">#{acc.instance_index}</span>
                        <span className="text-xs text-slate-300 truncate max-w-[100px]">
                          {emu?.name || `实例${acc.instance_index}`}
                        </span>
                      </div>
                    </td>
                    {/* 运行状态 */}
                    <td className="px-3 py-2">
                      {emu ? (
                        <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                          emu.running
                            ? "bg-emerald-500/10 text-emerald-400"
                            : "bg-slate-700/50 text-slate-500"
                        }`}>
                          {emu.running ? "在线" : "离线"}
                        </span>
                      ) : (
                        <span className="text-[10px] text-slate-600">未检测</span>
                      )}
                    </td>
                    {/* ADB */}
                    <td className="px-3 py-2">
                      <span className="text-[10px] font-mono text-slate-600">
                        :{5554 + acc.instance_index * 2}
                      </span>
                    </td>
                    {/* 组 */}
                    <td className="px-3 py-2">
                      <select className="input-glass w-16 text-center text-xs appearance-none cursor-pointer"
                        value={acc.group}
                        onChange={(e) => updateAccount(i, "group", e.target.value)}>
                        <option value="A">A 组</option>
                        <option value="B">B 组</option>
                      </select>
                    </td>
                    {/* 角色 */}
                    <td className="px-3 py-2">
                      <select className="input-glass w-20 text-xs appearance-none cursor-pointer"
                        value={acc.role}
                        onChange={(e) => updateAccount(i, "role", e.target.value)}>
                        <option value="captain">队长</option>
                        <option value="member">队员</option>
                      </select>
                    </td>
                    {/* 昵称 */}
                    <td className="px-3 py-2">
                      <input className="input-glass w-24 text-xs" value={acc.nickname}
                        onChange={(e) => updateAccount(i, "nickname", e.target.value)}
                        placeholder={emu?.name || "昵称"} />
                    </td>
                    {/* 游戏ID */}
                    <td className="px-3 py-2">
                      <input className="input-glass w-24 text-xs" value={acc.game_id}
                        onChange={(e) => updateAccount(i, "game_id", e.target.value)}
                        placeholder="校验用" />
                    </td>
                    {/* 删除 */}
                    <td className="px-3 py-2">
                      <button className="text-slate-600 hover:text-red-400 transition-colors p-1"
                        onClick={() => removeAccount(i)} title="移除">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                          <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
                        </svg>
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {accounts.length === 0 && (
          <div className="text-center py-8 text-sm text-slate-600">
            没有实例配置。点击上方「同步到实例分配」从检测到的模拟器自动生成。
          </div>
        )}

        <div className="flex items-center gap-3 mt-4">
          <button className="btn-primary text-xs" onClick={() => save("accounts")} disabled={saving}>
            保存分配
          </button>
          {msg && <span className="text-xs text-emerald-400">{msg}</span>}
        </div>
      </Section>
    </div>
  );
}

// ── 子组件 ──

function Section({ icon, title, desc, children }: { icon: string; title: string; desc: string; children: React.ReactNode }) {
  const icons: Record<string, React.ReactNode> = {
    env: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2" /></svg>,
    scan: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" /></svg>,
    team: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M23 21v-2a4 4 0 00-3-3.87" /><path d="M16 3.13a4 4 0 010 7.75" /></svg>,
  };
  return (
    <div>
      <div className="flex items-center gap-2 mb-4">
        <span className="text-indigo-400">{icons[icon]}</span>
        <h2 className="text-sm font-bold">{title}</h2>
        <span className="text-xs text-slate-600">{desc}</span>
      </div>
      {children}
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs font-medium text-slate-400 mb-1">{label}</label>
      {children}
      {hint && <p className="text-[10px] text-slate-600 mt-1">{hint}</p>}
    </div>
  );
}
