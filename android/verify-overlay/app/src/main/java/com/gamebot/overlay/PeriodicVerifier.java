package com.gamebot.overlay;

import android.util.Log;

import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;

/**
 * 周期校验:
 *   1. 每 30s VerifyClient.probe() 确认 TUN 路径 + gameproxy 活
 *   2. 失败 → reportFailure() 通知 backend (backend 重启加速器 / 重试)
 *   3. 连续 3 次失败前不报警 (防 30s 偶尔抖动假阳性)
 *   4. 一次成功重置连续失败计数
 * <p>
 * 不抓 wake lock — 模拟器内浮窗 service 是前台服务, 跟主 PUBG 一起跑不会被冻.
 */
final class PeriodicVerifier {
    private static final String TAG = "GamebotOverlay";

    private static final long PROBE_INTERVAL_S = 30;
    /** 连续失败到这个阈值才上报 (防偶发抖动) */
    private static final int CONSECUTIVE_FAIL_THRESHOLD = 3;

    private final InstanceConfig cfg;
    private final Listener listener;
    private ScheduledExecutorService exec;
    private ScheduledFuture<?> task;
    private int consecutiveFails = 0;

    interface Listener {
        void onProbeResult(VerifyClient.Result r, int consecutiveFails);
    }

    PeriodicVerifier(InstanceConfig cfg, Listener listener) {
        this.cfg = cfg;
        this.listener = listener;
    }

    synchronized void start() {
        if (exec != null) return;
        exec = Executors.newSingleThreadScheduledExecutor(r -> {
            Thread t = new Thread(r, "gamebot-verifier");
            t.setDaemon(true);
            return t;
        });
        task = exec.scheduleWithFixedDelay(this::tick, 0, PROBE_INTERVAL_S, TimeUnit.SECONDS);
        Log.i(TAG, "PeriodicVerifier started: every " + PROBE_INTERVAL_S + "s, inst=" + cfg.instIdx);
    }

    synchronized void stop() {
        if (task != null) {
            task.cancel(false);
            task = null;
        }
        if (exec != null) {
            exec.shutdownNow();
            exec = null;
        }
        Log.i(TAG, "PeriodicVerifier stopped");
    }

    private void tick() {
        VerifyClient.Result r = VerifyClient.probe();
        if (r.ok) {
            if (consecutiveFails > 0) {
                Log.i(TAG, "PeriodicVerifier recovered after " + consecutiveFails + " fails");
            }
            consecutiveFails = 0;
        } else {
            consecutiveFails++;
            Log.w(TAG, "PeriodicVerifier fail #" + consecutiveFails + ": " + r.error);
            if (consecutiveFails == CONSECUTIVE_FAIL_THRESHOLD) {
                // 阈值触发: 报 backend 一次, 之后即使继续失败也不再 spam
                VerifyClient.reportFailure(cfg.backendBaseUrl, cfg.instIdx,
                        "verify fail x" + consecutiveFails + ": " + r.error);
            }
        }
        if (listener != null) {
            try {
                listener.onProbeResult(r, consecutiveFails);
            } catch (Throwable t) {
                Log.w(TAG, "listener err: " + t.getMessage());
            }
        }
    }
}
