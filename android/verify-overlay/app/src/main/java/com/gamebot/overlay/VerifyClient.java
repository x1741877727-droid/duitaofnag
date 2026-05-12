package com.gamebot.overlay;

import android.util.Log;

import org.json.JSONObject;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/**
 * 探活 TUN→gameproxy→PUBG 路径是否通.
 *
 * 设计:
 *   旧版本探 http://gameproxy-verify-json/ 虚拟域名 — 只在 SOCKS5 模式 gameproxy
 *   拦得到. TUN 模式 Android 先 DNS 查 gameproxy-verify-json → NXDOMAIN, 包都不发.
 *
 *   新版本探 TCP connect 到已知 PUBG game server IP (在 gameproxy TUN 路由表里).
 *   3s timeout 内连上 = 全链路 TUN+gameproxy+upstream 真通. 任何一环挂都失败.
 *
 *   备用: 探 backend 自检 (10.0.2.2:8900/api/tun/state). 不经 TUN, 只确认基建活.
 *   两个都失败 = 严重问题. 只 TUN 失败 = upstream / IP 不健康.
 */
final class VerifyClient {
    private static final String TAG = "GamebotOverlay";
    private static final int TIMEOUT_MS = 3000;

    // PUBG / Tencent 游戏服务器候选 IP (gameproxy TUN 路由表里的)
    // 候选多个: 任一通过即认通过 (单 IP 抖动有容错)
    private static final String[][] PROBE_TARGETS = {
            {"43.135.105.51", "17500"},
            {"129.226.102.123", "17500"},
            {"129.226.103.85", "443"},
    };

    static final class Result {
        final boolean ok;
        final long uptimeSeconds;
        final long activeConns;
        final long totalConns;
        final String error;

        private Result(boolean ok, long uptime, long active, long total, String err) {
            this.ok = ok;
            this.uptimeSeconds = uptime;
            this.activeConns = active;
            this.totalConns = total;
            this.error = err;
        }

        static Result ok(long up, long active, long total) {
            return new Result(true, up, active, total, null);
        }

        static Result fail(String reason) {
            return new Result(false, 0, 0, 0, reason);
        }
    }

    static Result probe() {
        // 候选 IP 逐个 TCP connect, 任一通就算 ok.
        // 单 IP 不通可能是该 IP 暂时不健康 (gameproxy IPHealth 标 unhealthy);
        // 多个全不通 = TUN/upstream/网络故障.
        StringBuilder errs = new StringBuilder();
        for (String[] target : PROBE_TARGETS) {
            String ip = target[0];
            int port = Integer.parseInt(target[1]);
            String err = tcpProbe(ip, port);
            if (err == null) {
                // 至少一个通过 → 视为整体通
                return Result.ok(0, 0, 0);
            }
            if (errs.length() > 0) errs.append("; ");
            errs.append(ip).append(":").append(port).append("=").append(err);
        }
        return Result.fail("all probes fail: " + errs);
    }

    /** TCP connect + 立即关闭. 返回 null = 通, 否则错误描述. */
    private static String tcpProbe(String host, int port) {
        Socket s = new Socket();
        try {
            s.connect(new InetSocketAddress(host, port), TIMEOUT_MS);
            return null;
        } catch (Throwable t) {
            return t.getClass().getSimpleName();
        } finally {
            try { s.close(); } catch (Throwable ignored) {}
        }
    }

    /**
     * 校验失败时 POST 到 backend, backend 收到后触发"重启加速器 + 重试"恢复链路.
     * <p>
     * 端点: {backendBaseUrl}/api/v2/network/failure
     * body: {"inst": <idx>, "reason": "<msg>", "ts": <unix_ms>}
     * 5s timeout, 失败/网络断也只忍不抛 (浮窗服务继续跑).
     */
    static void reportFailure(String backendBaseUrl, int instIdx, String reason) {
        HttpURLConnection conn = null;
        try {
            URL u = new URL(backendBaseUrl + "/api/v2/network/failure");
            conn = (HttpURLConnection) u.openConnection();
            conn.setConnectTimeout(TIMEOUT_MS);
            conn.setReadTimeout(TIMEOUT_MS);
            conn.setRequestMethod("POST");
            conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            conn.setDoOutput(true);
            JSONObject body = new JSONObject();
            body.put("inst", instIdx);
            body.put("reason", reason == null ? "" : reason);
            body.put("ts", System.currentTimeMillis());
            byte[] data = body.toString().getBytes(StandardCharsets.UTF_8);
            try (OutputStream os = conn.getOutputStream()) {
                os.write(data);
            }
            int code = conn.getResponseCode();
            Log.i(TAG, "reportFailure inst=" + instIdx + " → HTTP " + code);
        } catch (Throwable t) {
            Log.w(TAG, "reportFailure swallow: " + t.getMessage());
        } finally {
            if (conn != null) conn.disconnect();
        }
    }
}
