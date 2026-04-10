import { useEffect, useState } from "react";

interface SettingsData {
  ldplayer_path: string;
  adb_path: string;
  game_package: string;
  game_mode: string;
  game_map: string;
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
  const [msg, setMsg] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetch("/api/settings").then((r) => r.json()).then(setData);
    fetch("/api/accounts").then((r) => r.json()).then(setAccounts);
  }, []);

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
    setMsg(json.ok ? "已保存" : "保存失败");
    setTimeout(() => setMsg(""), 2500);
  }

  function updateAccount(idx: number, field: keyof AccountData, value: string | number) {
    setAccounts((prev) =>
      prev.map((a, i) => (i === idx ? { ...a, [field]: value } : a))
    );
  }

  function addAccount() {
    const maxIdx = accounts.length > 0 ? Math.max(...accounts.map((a) => a.instance_index)) + 1 : 0;
    setAccounts([
      ...accounts,
      { qq: "", nickname: `玩家${maxIdx + 1}`, game_id: "", group: accounts.length < 3 ? "A" : "B", role: "member", instance_index: maxIdx },
    ]);
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

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-8 animate-slide-in">

      {/* ── 环境配置 ── */}
      <Section icon="env" title="环境配置" desc="配置模拟器和 ADB 路径">
        <div className="grid grid-cols-2 gap-4">
          <Field label="雷电模拟器路径" hint="LDPlayer 安装目录，例如 D:\leidian\LDPlayer9">
            <input
              className="input-glass"
              value={data.ldplayer_path}
              onChange={(e) => setData({ ...data, ldplayer_path: e.target.value })}
              placeholder="D:\leidian\LDPlayer9"
            />
          </Field>
          <Field label="ADB 路径" hint="留空则自动使用模拟器自带的 adb.exe">
            <input
              className="input-glass"
              value={data.adb_path}
              onChange={(e) => setData({ ...data, adb_path: e.target.value })}
              placeholder="自动检测"
            />
          </Field>
          <Field label="游戏包名" hint="一般不需要修改">
            <input
              className="input-glass"
              value={data.game_package}
              onChange={(e) => setData({ ...data, game_package: e.target.value })}
            />
          </Field>
          <Field label="目标地图" hint="队长将自动选择此地图">
            <input
              className="input-glass"
              value={data.game_map}
              onChange={(e) => setData({ ...data, game_map: e.target.value })}
              placeholder="狙击团竞"
            />
          </Field>
        </div>
        <div className="mt-4">
          <button className="btn-primary text-xs" onClick={() => save("settings")} disabled={saving}>
            保存设置
          </button>
          {msg && <span className="ml-3 text-xs text-emerald-400">{msg}</span>}
        </div>
      </Section>

      {/* ── 实例分配 ── */}
      <Section icon="team" title="实例分配" desc="将模拟器实例分配到 A/B 组，指定队长和队员">
        <div className="text-[11px] text-slate-500 mb-3 p-3 rounded-lg" style={{ background: "rgba(59,130,246,0.05)", border: "1px solid rgba(59,130,246,0.1)" }}>
          <strong className="text-blue-400">使用说明：</strong>
          实例编号对应雷电多开器中的模拟器序号（从 0 开始）。
          每组需要 1 个队长 + 2 个队员 = 3 个实例。
          ADB 端口自动计算：实例 0 = emulator-5554，实例 1 = emulator-5556，依此类推。
        </div>

        <div className="overflow-hidden rounded-xl border border-slate-800/60">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-[11px] text-slate-500 uppercase tracking-wider" style={{ background: "rgba(15,23,42,0.5)" }}>
                <th className="text-left px-3 py-2.5 font-medium">实例</th>
                <th className="text-left px-3 py-2.5 font-medium">ADB</th>
                <th className="text-left px-3 py-2.5 font-medium">组</th>
                <th className="text-left px-3 py-2.5 font-medium">角色</th>
                <th className="text-left px-3 py-2.5 font-medium">昵称</th>
                <th className="text-left px-3 py-2.5 font-medium">QQ</th>
                <th className="text-left px-3 py-2.5 font-medium">游戏ID</th>
                <th className="px-3 py-2.5 w-8"></th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((acc, i) => (
                <tr key={i} className="border-t border-slate-800/40 hover:bg-slate-800/20 transition-colors">
                  <td className="px-3 py-2">
                    <input
                      type="number"
                      className="input-glass w-14 text-center"
                      value={acc.instance_index}
                      onChange={(e) => updateAccount(i, "instance_index", parseInt(e.target.value) || 0)}
                      min={0}
                      max={11}
                    />
                  </td>
                  <td className="px-3 py-2">
                    <span className="text-[10px] font-mono text-slate-600">
                      :{5554 + acc.instance_index * 2}
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    <select
                      className="input-glass w-16 text-center appearance-none cursor-pointer"
                      value={acc.group}
                      onChange={(e) => updateAccount(i, "group", e.target.value)}
                    >
                      <option value="A">A</option>
                      <option value="B">B</option>
                    </select>
                  </td>
                  <td className="px-3 py-2">
                    <select
                      className="input-glass w-20 appearance-none cursor-pointer"
                      value={acc.role}
                      onChange={(e) => updateAccount(i, "role", e.target.value)}
                    >
                      <option value="captain">队长</option>
                      <option value="member">队员</option>
                    </select>
                  </td>
                  <td className="px-3 py-2">
                    <input className="input-glass w-24" value={acc.nickname} onChange={(e) => updateAccount(i, "nickname", e.target.value)} placeholder="昵称" />
                  </td>
                  <td className="px-3 py-2">
                    <input className="input-glass w-24" value={acc.qq} onChange={(e) => updateAccount(i, "qq", e.target.value)} placeholder="QQ号" />
                  </td>
                  <td className="px-3 py-2">
                    <input className="input-glass w-24" value={acc.game_id} onChange={(e) => updateAccount(i, "game_id", e.target.value)} placeholder="游戏ID" />
                  </td>
                  <td className="px-3 py-2">
                    <button
                      className="text-slate-600 hover:text-red-400 transition-colors p-1"
                      onClick={() => removeAccount(i)}
                      title="删除"
                    >
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <line x1="18" y1="6" x2="6" y2="18" />
                        <line x1="6" y1="6" x2="18" y2="18" />
                      </svg>
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="flex items-center gap-3 mt-4">
          <button
            className="px-3 py-1.5 rounded-lg text-xs text-slate-400 border border-slate-700 hover:border-indigo-500/40 hover:text-indigo-400 transition-colors"
            onClick={addAccount}
          >
            + 添加实例
          </button>
          <button className="btn-primary text-xs" onClick={() => save("accounts")} disabled={saving}>
            保存账号配置
          </button>
        </div>
      </Section>

      {/* ── 使用指南 ── */}
      <Section icon="guide" title="快速入门" desc="三步开始使用">
        <div className="grid grid-cols-3 gap-4">
          <GuideCard
            step={1}
            title="配置环境"
            items={[
              "安装雷电模拟器 9 并多开所需数量的实例",
              "在上方填写雷电模拟器安装路径",
              "ADB 路径留空即可自动检测",
            ]}
          />
          <GuideCard
            step={2}
            title="分配账号"
            items={[
              "在每个模拟器中登录对应的游戏账号",
              "在实例分配表中设置组别 (A/B) 和角色",
              "每组需要 1 个队长 + 2 个队员",
            ]}
          />
          <GuideCard
            step={3}
            title="启动运行"
            items={[
              "确保所有模拟器已启动并在主页面",
              "点击右上角「启动全部」按钮",
              "观察面板中每个实例的运行状态",
            ]}
          />
        </div>
      </Section>
    </div>
  );
}

// ── 子组件 ──

function Section({ icon, title, desc, children }: { icon: string; title: string; desc: string; children: React.ReactNode }) {
  const icons: Record<string, React.ReactNode> = {
    env: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2" /></svg>,
    team: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M23 21v-2a4 4 0 00-3-3.87" /><path d="M16 3.13a4 4 0 010 7.75" /></svg>,
    guide: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10" /><path d="M9.09 9a3 3 0 015.83 1c0 2-3 3-3 3" /><line x1="12" y1="17" x2="12.01" y2="17" /></svg>,
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

function GuideCard({ step, title, items }: { step: number; title: string; items: string[] }) {
  return (
    <div className="glass-card rounded-xl p-4">
      <div className="flex items-center gap-2 mb-3">
        <span
          className="w-6 h-6 rounded-full flex items-center justify-center text-[11px] font-bold"
          style={{ background: "linear-gradient(135deg, rgba(99,102,241,0.15), rgba(59,130,246,0.15))", color: "#818cf8" }}
        >
          {step}
        </span>
        <span className="text-xs font-bold">{title}</span>
      </div>
      <ul className="space-y-1.5">
        {items.map((item, i) => (
          <li key={i} className="flex items-start gap-2 text-[11px] text-slate-400">
            <span className="text-indigo-500/50 mt-0.5">›</span>
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}
