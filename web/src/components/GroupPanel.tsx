import type { InstanceState } from "../stores/appStore";
import { InstanceCard } from "./InstanceCard";

interface Props {
  group: string;
  instances: InstanceState[];
}

export function GroupPanel({ group, instances }: Props) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <div
          className="w-1.5 h-4 rounded-full"
          style={{
            background: group === "A"
              ? "linear-gradient(180deg, #3b82f6, #6366f1)"
              : "linear-gradient(180deg, #f59e0b, #ef4444)",
          }}
        />
        <span className="text-xs font-bold text-slate-400 tracking-widest uppercase">
          {group} 组
        </span>
        <span className="text-[10px] text-slate-600">
          {instances.length} 个实例
        </span>
      </div>
      <div className="grid gap-2.5">
        {instances.map((inst) => (
          <InstanceCard key={inst.index} inst={inst} />
        ))}
      </div>
    </div>
  );
}
