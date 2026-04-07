import { useWebSocket } from "./hooks/useWebSocket";
import { useAppStore } from "./stores/appStore";
import { Controls } from "./components/Controls";
import { Dashboard } from "./components/Dashboard";
import { DebugPanel } from "./components/DebugPanel";
import { LogViewer } from "./components/LogViewer";
import { Settings } from "./components/Settings";

function App() {
  useWebSocket();

  const { activeTab, setActiveTab } = useAppStore();

  const tabCls = (tab: typeof activeTab) =>
    `px-4 py-2 text-sm font-medium rounded-t-lg transition-colors ${
      activeTab === tab
        ? "bg-slate-800 text-white border-b-2 border-blue-500"
        : "text-slate-400 hover:text-white"
    }`;

  return (
    <div className="h-screen flex flex-col bg-slate-900">
      {/* 顶栏 */}
      <header className="shrink-0 flex items-center justify-between px-5 py-3 border-b border-slate-700 bg-slate-900">
        <h1 className="text-base font-bold tracking-wide">
          游戏自动化控制台
        </h1>
        <Controls />
      </header>

      {/* Tab 栏 */}
      <nav className="shrink-0 flex gap-1 px-5 pt-2 bg-slate-900">
        <button className={tabCls("dashboard")} onClick={() => setActiveTab("dashboard")}>
          面板
        </button>
        <button className={tabCls("debug")} onClick={() => setActiveTab("debug")}>
          调试
        </button>
        <button className={tabCls("settings")} onClick={() => setActiveTab("settings")}>
          设置
        </button>
      </nav>

      {/* 主区域 */}
      <div className="flex-1 flex min-h-0">
        {/* 左侧：面板/设置/调试 */}
        <main className="flex-1 overflow-y-auto p-5">
          {activeTab === "dashboard" && <Dashboard />}
          {activeTab === "debug" && <DebugPanel />}
          {activeTab === "settings" && <Settings />}
        </main>

        {/* 右侧：日志 */}
        <aside className="w-[400px] shrink-0 border-l border-slate-700 flex flex-col bg-slate-900">
          <div className="px-3 py-2 text-xs font-bold text-slate-500 border-b border-slate-700 tracking-wider">
            实时日志
          </div>
          <div className="flex-1 min-h-0">
            <LogViewer />
          </div>
        </aside>
      </div>
    </div>
  );
}

export default App;
