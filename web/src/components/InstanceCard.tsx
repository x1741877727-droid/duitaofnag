import type { InstanceState } from "../stores/appStore";

const STATE_COLORS: Record<string, string> = {
  idle: "bg-gray-600",
  launching: "bg-blue-500",
  login_check: "bg-blue-400",
  lobby: "bg-green-500",
  dismiss_popups: "bg-yellow-500",
  setup: "bg-yellow-400",
  team_create: "bg-purple-500",
  team_join: "bg-purple-400",
  wait_players: "bg-orange-400",
  verify_players: "bg-orange-500",
  ready_check: "bg-cyan-500",
  matching: "bg-indigo-500 animate-pulse",
  verify_opponent: "bg-indigo-400",
  success: "bg-emerald-500",
  abort: "bg-red-400",
  error_banned: "bg-red-600",
  error_network: "bg-red-500",
  error_unknown: "bg-red-500",
};

const STATE_LABELS: Record<string, string> = {
  idle: "空闲",
  launching: "启动中",
  login_check: "检查登录",
  lobby: "大厅",
  dismiss_popups: "关闭弹窗",
  setup: "赛前设置",
  team_create: "创建队伍",
  team_join: "加入队伍",
  wait_players: "等待玩家",
  verify_players: "校验玩家",
  ready_check: "准备检查",
  matching: "匹配中",
  verify_opponent: "校验对手",
  success: "匹配成功",
  abort: "中止重来",
  error_banned: "被禁赛",
  error_network: "网络错误",
  error_unknown: "未知错误",
};

export function InstanceCard({ inst }: { inst: InstanceState }) {
  const color = STATE_COLORS[inst.state] ?? "bg-gray-500";
  const label = STATE_LABELS[inst.state] ?? inst.state;
  const isCaptain = inst.role === "captain";

  return (
    <div className="rounded-lg bg-slate-800 border border-slate-700 p-3 flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-slate-300">
          {isCaptain ? "★ " : ""}
          实例 {inst.index}
        </span>
        <span className="text-xs text-slate-500">
          {isCaptain ? "队长" : "队员"}
        </span>
      </div>

      <div className="text-xs text-slate-400 truncate">
        {inst.nickname || `账号${inst.index + 1}`}
      </div>

      <div className="flex items-center gap-2">
        <span className={`inline-block w-2 h-2 rounded-full ${color}`} />
        <span className="text-sm font-semibold">{label}</span>
      </div>

      {inst.error && (
        <div className="text-xs text-red-400 truncate" title={inst.error}>
          {inst.error}
        </div>
      )}

      <div className="text-xs text-slate-500">
        {inst.state_duration > 0 ? `${Math.round(inst.state_duration)}s` : ""}
      </div>
    </div>
  );
}
