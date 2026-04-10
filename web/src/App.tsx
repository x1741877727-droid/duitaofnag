import { useWebSocket } from "./hooks/useWebSocket";
import { useAppStore } from "./stores/appStore";
import { Dashboard } from "./components/Dashboard";
import { Settings } from "./components/Settings";
import { LogPanel } from "./components/LogPanel";
import { Header } from "./components/Header";

function App() {
  useWebSocket();

  const { activeView, showLogs } = useAppStore();

  return (
    <div className="h-screen flex flex-col overflow-hidden" style={{ background: "var(--bg-primary)" }}>
      <Header />

      <div className="flex-1 flex min-h-0">
        {/* 主内容区 */}
        <main className="flex-1 overflow-y-auto">
          {activeView === "dashboard" && <Dashboard />}
          {activeView === "settings" && <Settings />}
        </main>

        {/* 日志侧栏 */}
        {showLogs && (
          <aside className="w-[380px] shrink-0 border-l border-slate-800/60 flex flex-col animate-slide-in">
            <LogPanel />
          </aside>
        )}
      </div>
    </div>
  );
}

export default App;
