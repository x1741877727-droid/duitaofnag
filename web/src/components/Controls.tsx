import { useState } from "react";
import { useAppStore } from "../stores/appStore";

async function api(method: string, path: string, body?: unknown) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  return res.json();
}

export function Controls() {
  const { running, paused } = useAppStore();
  const [loading, setLoading] = useState("");

  async function handleStart() {
    setLoading("start");
    await api("POST", "/api/start");
    useAppStore.getState().setRunning(true);
    setLoading("");
  }

  async function handleStop() {
    setLoading("stop");
    await api("POST", "/api/stop");
    useAppStore.getState().setRunning(false);
    useAppStore.getState().setInstances({});
    setLoading("");
  }

  async function handlePause() {
    setLoading("pause");
    await api("POST", "/api/pause");
    useAppStore.getState().setRunning(true, true);
    setLoading("");
  }

  async function handleResume() {
    setLoading("resume");
    await api("POST", "/api/resume");
    useAppStore.getState().setRunning(true, false);
    setLoading("");
  }

  const btnBase =
    "px-4 py-2 rounded-lg text-sm font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed";

  return (
    <div className="flex items-center gap-3">
      {!running ? (
        <button
          className={`${btnBase} bg-emerald-600 hover:bg-emerald-500 text-white`}
          onClick={handleStart}
          disabled={loading === "start"}
        >
          {loading === "start" ? "启动中..." : "全部启动"}
        </button>
      ) : (
        <>
          {!paused ? (
            <button
              className={`${btnBase} bg-yellow-600 hover:bg-yellow-500 text-white`}
              onClick={handlePause}
              disabled={!!loading}
            >
              暂停
            </button>
          ) : (
            <button
              className={`${btnBase} bg-blue-600 hover:bg-blue-500 text-white`}
              onClick={handleResume}
              disabled={!!loading}
            >
              恢复
            </button>
          )}
          <button
            className={`${btnBase} bg-red-600 hover:bg-red-500 text-white`}
            onClick={handleStop}
            disabled={loading === "stop"}
          >
            {loading === "stop" ? "停止中..." : "停止"}
          </button>
        </>
      )}

      {running && (
        <span
          className={`text-xs ${paused ? "text-yellow-400" : "text-emerald-400"}`}
        >
          {paused ? "已暂停" : "运行中"}
        </span>
      )}
    </div>
  );
}
