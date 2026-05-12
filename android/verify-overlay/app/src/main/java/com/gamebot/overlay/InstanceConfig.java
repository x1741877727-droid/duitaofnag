package com.gamebot.overlay;

import android.content.ContentResolver;
import android.provider.Settings;

/**
 * 从 Settings.Global 读 backend 注入的实例配置.
 * <p>
 * backend 装 APK 后会跑:
 *   adb -s emulator-5554 shell settings put global gamebot_inst 0
 *   adb -s emulator-5554 shell settings put global gamebot_backend http://10.0.2.2:8900
 * <p>
 * 不用 SystemProperties (需要 root + reflection) 也不用 SharedPreferences (要走 APK
 * 自己写入, 没法从 ADB 注入). Settings.Global 是公开 API + adb shell 直接能 put.
 */
final class InstanceConfig {
    static final String DEFAULT_BACKEND = "http://10.0.2.2:8900";

    final int instIdx;
    final String backendBaseUrl;

    private InstanceConfig(int idx, String backend) {
        this.instIdx = idx;
        this.backendBaseUrl = backend;
    }

    static InstanceConfig load(ContentResolver cr) {
        int idx = 0;
        try {
            String s = Settings.Global.getString(cr, "gamebot_inst");
            if (s != null && !s.isEmpty()) idx = Integer.parseInt(s.trim());
        } catch (Throwable ignored) {}

        String backend = DEFAULT_BACKEND;
        try {
            String s = Settings.Global.getString(cr, "gamebot_backend");
            if (s != null && !s.isEmpty()) backend = s.trim();
        } catch (Throwable ignored) {}

        return new InstanceConfig(idx, backend);
    }
}
