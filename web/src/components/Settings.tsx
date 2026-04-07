import { useEffect, useState } from "react";

interface SettingsData {
  ldplayer_path: string;
  llm_api_url: string;
  llm_api_key: string;
  game_package: string;
  game_mode: string;
  game_map: string;
  match_timeout: number;
  state_timeout: number;
  screenshot_interval: number;
  dev_mock: boolean;
}

export function Settings() {
  const [data, setData] = useState<SettingsData | null>(null);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    fetch("/api/settings")
      .then((r) => r.json())
      .then(setData);
  }, []);

  async function handleSave() {
    if (!data) return;
    setSaving(true);
    setMsg("");
    const res = await fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    const json = await res.json();
    setSaving(false);
    setMsg(json.ok ? "已保存" : "保存失败");
    setTimeout(() => setMsg(""), 2000);
  }

  if (!data) return <div className="p-6 text-slate-500">加载中...</div>;

  const inputCls =
    "w-full bg-slate-800 border border-slate-600 rounded px-3 py-1.5 text-sm text-white focus:border-blue-500 focus:outline-none";
  const labelCls = "block text-xs text-slate-400 mb-1";

  return (
    <div className="p-6 max-w-2xl space-y-5">
      <h2 className="text-lg font-bold">设置</h2>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className={labelCls}>雷电模拟器路径</label>
          <input
            className={inputCls}
            value={data.ldplayer_path}
            onChange={(e) => setData({ ...data, ldplayer_path: e.target.value })}
          />
        </div>
        <div>
          <label className={labelCls}>游戏包名</label>
          <input
            className={inputCls}
            value={data.game_package}
            onChange={(e) => setData({ ...data, game_package: e.target.value })}
          />
        </div>
        <div>
          <label className={labelCls}>LLM API 地址</label>
          <input
            className={inputCls}
            value={data.llm_api_url}
            onChange={(e) => setData({ ...data, llm_api_url: e.target.value })}
          />
        </div>
        <div>
          <label className={labelCls}>LLM API Key</label>
          <input
            className={inputCls}
            type="password"
            value={data.llm_api_key}
            onChange={(e) => setData({ ...data, llm_api_key: e.target.value })}
          />
        </div>
        <div>
          <label className={labelCls}>游戏模式</label>
          <input
            className={inputCls}
            value={data.game_mode}
            onChange={(e) => setData({ ...data, game_mode: e.target.value })}
          />
        </div>
        <div>
          <label className={labelCls}>游戏地图</label>
          <input
            className={inputCls}
            value={data.game_map}
            onChange={(e) => setData({ ...data, game_map: e.target.value })}
          />
        </div>
        <div>
          <label className={labelCls}>匹配超时 (秒)</label>
          <input
            className={inputCls}
            type="number"
            value={data.match_timeout}
            onChange={(e) =>
              setData({ ...data, match_timeout: Number(e.target.value) })
            }
          />
        </div>
        <div>
          <label className={labelCls}>截图间隔 (秒)</label>
          <input
            className={inputCls}
            type="number"
            step="0.1"
            value={data.screenshot_interval}
            onChange={(e) =>
              setData({
                ...data,
                screenshot_interval: Number(e.target.value),
              })
            }
          />
        </div>
      </div>

      <div className="flex items-center gap-3">
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={data.dev_mock}
            onChange={(e) => setData({ ...data, dev_mock: e.target.checked })}
          />
          Mock 模式 (开发用)
        </label>
      </div>

      <div className="flex items-center gap-3">
        <button
          className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-sm disabled:opacity-40"
          onClick={handleSave}
          disabled={saving}
        >
          {saving ? "保存中..." : "保存设置"}
        </button>
        {msg && <span className="text-sm text-emerald-400">{msg}</span>}
      </div>
    </div>
  );
}
