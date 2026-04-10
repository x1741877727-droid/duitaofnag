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
    <div className="h-screen flex flex-col overflow-hidden" style={{ background: "var(--bg)" }}>
      <Header />
      <div className="flex-1 flex min-h-0">
        <main className="flex-1 overflow-y-auto">
          {activeView === "dashboard" && <Dashboard />}
          {activeView === "settings" && <Settings />}
        </main>
        {showLogs && (
          <aside className="w-[360px] shrink-0 border-l animate-in" style={{ borderColor: "var(--border)" }}>
            <LogPanel />
          </aside>
        )}
      </div>
    </div>
  );
}

export default App;
