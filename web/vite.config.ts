import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

// 后端地址：本地开发用 127.0.0.1，远程调试改为 Windows IP
const BACKEND = process.env.BACKEND_URL || "http://127.0.0.1:8900";
const BACKEND_WS = BACKEND.replace("http", "ws");

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5174,
    proxy: {
      "/api": BACKEND,
      "/ws": { target: BACKEND_WS, ws: true },
    },
  },
  build: {
    outDir: "dist",
  },
});
