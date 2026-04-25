package com.fightmaster.vpn;

/**
 * Host 字符串混淆 — 让 strings/grep 类静态分析找不到明文 IP。
 * 注：XOR 混淆门槛不高，脚本小子几分钟能解；真正强保护需 NDK。
 */
final class HostObfuscate {
    private static final byte KEY = 0x5A;

    private static final byte[] ENC_HOST = {
        0x6b, 0x6b, 0x6b, 0x74, 0x6b, 0x6d, 0x6a, 0x74,
        0x6b, 0x6d, 0x6a, 0x74, 0x6b, 0x6e, 0x63
    };

    static String getHost() {
        byte[] b = new byte[ENC_HOST.length];
        for (int i = 0; i < b.length; i++) b[i] = (byte)(ENC_HOST[i] ^ KEY);
        return new String(b);
    }

    static int getPort() {
        return 9900;
    }
}
