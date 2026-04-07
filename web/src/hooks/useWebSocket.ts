import { useEffect, useRef } from "react";
import { useAppStore } from "../stores/appStore";

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<number>();
  const { setInstances, setStats, addLog, updateInstance, setRunning } =
    useAppStore();

  useEffect(() => {
    function connect() {
      const protocol = location.protocol === "https:" ? "wss:" : "ws:";
      const url = `${protocol}//${location.host}/ws`;
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        console.log("[WS] 已连接");
      };

      ws.onmessage = (evt) => {
        try {
          const msg = JSON.parse(evt.data);
          switch (msg.type) {
            case "snapshot":
              setInstances(msg.instances ?? {});
              if (msg.stats) setStats(msg.stats);
              setRunning(true);
              break;
            case "state_change":
              updateInstance(
                msg.data.instance,
                msg.data.old,
                msg.data.new
              );
              break;
            case "log":
              addLog(msg.data);
              break;
            case "stats":
              setStats(msg.data);
              break;
          }
        } catch {
          // ignore parse errors
        }
      };

      ws.onclose = () => {
        console.log("[WS] 断开，3秒后重连");
        reconnectTimer.current = window.setTimeout(connect, 3000);
      };

      ws.onerror = () => ws.close();

      // 心跳
      const heartbeat = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send("ping");
      }, 15000);

      ws.addEventListener("close", () => clearInterval(heartbeat));
    }

    connect();

    return () => {
      clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [setInstances, setStats, addLog, updateInstance, setRunning]);
}
