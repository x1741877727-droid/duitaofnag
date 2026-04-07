import { useAppStore, type InstanceState } from "../stores/appStore";
import { GroupPanel } from "./GroupPanel";
import { StatsBar } from "./StatsBar";

export function Dashboard() {
  const instances = useAppStore((s) => s.instances);
  const running = useAppStore((s) => s.running);

  const all: InstanceState[] = Object.values(instances).sort(
    (a, b) => a.index - b.index
  );

  const groupA = all.filter((i) => i.group === "A");
  const groupB = all.filter((i) => i.group === "B");

  if (!running || all.length === 0) {
    return (
      <div className="flex items-center justify-center h-64 text-slate-500">
        点击「全部启动」开始运行
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <StatsBar />

      <div className="grid grid-cols-2 gap-4">
        <GroupPanel group="A" instances={groupA} />
        <GroupPanel group="B" instances={groupB} />
      </div>
    </div>
  );
}
