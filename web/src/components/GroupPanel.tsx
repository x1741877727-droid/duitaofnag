import type { InstanceState } from "../stores/appStore";
import { InstanceCard } from "./InstanceCard";

interface Props {
  group: string;
  instances: InstanceState[];
}

export function GroupPanel({ group, instances }: Props) {
  return (
    <div className="rounded-xl bg-slate-850 border border-slate-700 p-4">
      <h3 className="text-sm font-bold text-slate-400 mb-3 tracking-wider">
        {group} 组
      </h3>
      <div className="grid gap-3">
        {instances.map((inst) => (
          <InstanceCard key={inst.index} inst={inst} />
        ))}
      </div>
    </div>
  );
}
