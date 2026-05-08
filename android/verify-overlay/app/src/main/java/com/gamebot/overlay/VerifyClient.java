package com.gamebot.overlay;

import android.util.Log;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * HTTP 拉取 gameproxy 状态.
 *
 * 端点: http://gameproxy-verify-json/  (gameproxy 在 TUN 层拦截这个虚拟域名,
 *       直接在代理内回 JSON, 不走任何上游). 流量真的穿了 TUN 才能拿到响应.
 *
 * 响应 schema (来自 gameproxy verify.go):
 *   { ok, server, uptime_seconds, active_connections, total_connections }
 */
final class VerifyClient {
    private static final String TAG = "GamebotOverlay";
    private static final String VERIFY_URL = "http://gameproxy-verify-json/";
    private static final int TIMEOUT_MS = 3000;

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
        HttpURLConnection conn = null;
        try {
            URL u = new URL(VERIFY_URL);
            conn = (HttpURLConnection) u.openConnection();
            conn.setConnectTimeout(TIMEOUT_MS);
            conn.setReadTimeout(TIMEOUT_MS);
            conn.setRequestMethod("GET");
            conn.setRequestProperty("Accept", "application/json");
            int code = conn.getResponseCode();
            if (code != 200) {
                return Result.fail("HTTP " + code);
            }
            StringBuilder sb = new StringBuilder();
            try (BufferedReader br = new BufferedReader(
                    new InputStreamReader(conn.getInputStream(), "UTF-8"))) {
                char[] buf = new char[1024];
                int n;
                while ((n = br.read(buf)) > 0) sb.append(buf, 0, n);
            }
            JSONObject j = new JSONObject(sb.toString());
            if (!j.optBoolean("ok", false)) {
                return Result.fail("ok=false");
            }
            return Result.ok(
                    j.optLong("uptime_seconds", 0),
                    j.optLong("active_connections", 0),
                    j.optLong("total_connections", 0));
        } catch (Throwable t) {
            String msg = t.getClass().getSimpleName() + ": " + t.getMessage();
            Log.w(TAG, "probe failed: " + msg);
            return Result.fail(msg);
        } finally {
            if (conn != null) conn.disconnect();
        }
    }
}
