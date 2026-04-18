package com.fightmaster.vpn;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.Intent;
import android.net.VpnService;
import android.os.Build;
import android.os.ParcelFileDescriptor;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.nio.charset.StandardCharsets;

import tun2socks.Tun2socks;

/**
 * FightMaster VPN 服务
 * 通过 tun2socks + v2ray-core 建立 VPN 隧道
 * 代理地址可通过 Intent extra 或 SharedPreferences 配置
 */
public class FightMasterVpnService extends VpnService implements tun2socks.VpnService {

    private static final String TAG = "FightMasterVPN";
    private static final String CHANNEL_ID = "fightmaster_vpn";

    // ADB 广播 Action 常量
    public static final String ACTION_START = "com.fightmaster.vpn.START";
    public static final String ACTION_STOP = "STOP";
    public static final String ACTION_STATUS = "com.fightmaster.vpn.STATUS";

    // Intent extra keys
    public static final String EXTRA_PROXY_HOST = "proxy_host";
    public static final String EXTRA_PROXY_PORT = "proxy_port";

    // 代理服务器默认配置
    private static final String DEFAULT_PROXY_HOST = HostObfuscate.getHost();
    private static final int DEFAULT_PROXY_PORT = HostObfuscate.getPort();
    private String proxyHost = DEFAULT_PROXY_HOST;
    private int proxyPort = DEFAULT_PROXY_PORT;

    private ParcelFileDescriptor pfd;
    private Thread bgThread;
    private volatile boolean running = false;
    private int tunFd = -1;

    // 静态实例引用，供 CommandReceiver 调用
    private static FightMasterVpnService instance;

    public static FightMasterVpnService getInstance() {
        return instance;
    }

    private String buildV2RayConfig() {
        try {
            JSONObject config = new JSONObject();

            // ══════════════════════════════════════════════════════════
            // [2026-04-18] 实验配置：改成 geosite/geoip 简单 5 条规则
            // 依赖 assets/geoip.dat + assets/geosite.dat（已打包）
            // ══════════════════════════════════════════════════════════

            // log — 关掉（none）
            config.put("log", new JSONObject().put("loglevel", "none"));

            // dns — 223.5.5.5 + 1.1.1.1，保留 gameproxy-verify
            JSONObject dns = new JSONObject();
            dns.put("hosts", new JSONObject().put("gameproxy-verify", "1.2.3.4"));
            dns.put("servers", new JSONArray().put("223.5.5.5").put("1.1.1.1"));
            config.put("dns", dns);

            // outbound (主代理)
            JSONObject outbound = new JSONObject();
            outbound.put("protocol", "socks");
            outbound.put("settings", new JSONObject().put("servers",
                new JSONArray().put(new JSONObject()
                    .put("address", proxyHost)
                    .put("port", proxyPort))));
            outbound.put("streamSettings", new JSONObject().put("network", "tcp"));
            outbound.put("tag", "proxy");
            config.put("outbound", outbound);

            // outboundDetour — direct / dns-out (保留 block 防未来规则需要)
            JSONArray detour = new JSONArray();
            detour.put(new JSONObject().put("protocol", "freedom").put("settings", new JSONObject()).put("tag", "direct"));
            detour.put(new JSONObject().put("protocol", "dns").put("settings", new JSONObject()).put("tag", "dns-out"));
            detour.put(new JSONObject().put("protocol", "blackhole").put("settings", new JSONObject()).put("tag", "block"));
            config.put("outboundDetour", detour);

            // ══════════════════════════════════════════════════════════
            // 路由规则 — 5 条简单 geo 规则
            // ══════════════════════════════════════════════════════════
            JSONArray rules = new JSONArray();

            // 0. DNS 内部路由
            rules.put(new JSONObject()
                .put("type", "field").put("port", "53").put("outboundTag", "dns-out"));

            // 0.5 保留：gameproxy-verify 域名 → proxy（验证页面）
            rules.put(new JSONObject()
                .put("type", "field")
                .put("domain", new JSONArray().put("gameproxy-verify"))
                .put("outboundTag", "proxy"));

            // 1. geosite:google → proxy
            rules.put(new JSONObject()
                .put("type", "field")
                .put("domain", new JSONArray().put("geosite:google"))
                .put("outboundTag", "proxy"));

            // 2. geosite:cn → direct
            rules.put(new JSONObject()
                .put("type", "field")
                .put("domain", new JSONArray().put("geosite:cn"))
                .put("outboundTag", "direct"));

            // 3. geoip:cn → direct
            rules.put(new JSONObject()
                .put("type", "field")
                .put("ip", new JSONArray().put("geoip:cn"))
                .put("outboundTag", "direct"));

            // 4. geoip:private → direct
            rules.put(new JSONObject()
                .put("type", "field")
                .put("ip", new JSONArray().put("geoip:private"))
                .put("outboundTag", "direct"));

            // 5. FINAL → proxy（走 0.0.0.0/0 兜底）
            rules.put(new JSONObject()
                .put("type", "field").put("outboundTag", "proxy").put("port", "0-65535"));

            // domainStrategy: AsIs（不做 IP 解析即匹配，按配置严格）
            config.put("routing", new JSONObject()
                .put("settings", new JSONObject()
                    .put("domainStrategy", "AsIs")
                    .put("rules", rules)));

            return config.toString();
        } catch (JSONException e) {
            Log.e(TAG, "Failed to build v2ray config", e);
            return "{}";
        }
    }

    /**
     * 把 assets 里的 geoip.dat / geosite.dat 解压到 filesDir
     * v2ray 启动时会从 filesDir 读取 geo 数据
     * 只在首次启动或文件变更时解压
     */
    private void extractGeoAssets() {
        String[] files = {"geoip.dat", "geosite.dat"};
        java.io.File filesDir = getFilesDir();
        for (String name : files) {
            java.io.File dest = new java.io.File(filesDir, name);
            try (java.io.InputStream in = getAssets().open(name)) {
                long assetSize = in.available();
                if (dest.exists() && dest.length() == assetSize) {
                    Log.d(TAG, "geo asset up-to-date: " + name);
                    continue;
                }
                try (java.io.FileOutputStream out = new java.io.FileOutputStream(dest)) {
                    byte[] buf = new byte[65536];
                    int n;
                    while ((n = in.read(buf)) > 0) {
                        out.write(buf, 0, n);
                    }
                }
                Log.i(TAG, "extracted asset: " + name + " (" + dest.length() + " bytes)");
            } catch (java.io.IOException e) {
                Log.e(TAG, "extract asset failed: " + name, e);
            }
        }
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
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            stopVpn();
            return START_NOT_STICKY;
        }

        // 读取代理地址（优先 Intent extra，其次 SharedPreferences，最后默认值）
        if (intent != null && intent.hasExtra(EXTRA_PROXY_HOST)) {
            proxyHost = intent.getStringExtra(EXTRA_PROXY_HOST);
            proxyPort = intent.getIntExtra(EXTRA_PROXY_PORT, DEFAULT_PROXY_PORT);
            getSharedPreferences("fightmaster_config", MODE_PRIVATE)
                .edit()
                .putString("proxy_host", proxyHost)
                .putInt("proxy_port", proxyPort)
                .apply();
        } else {
            android.content.SharedPreferences prefs = getSharedPreferences("fightmaster_config", MODE_PRIVATE);
            proxyHost = prefs.getString("proxy_host", DEFAULT_PROXY_HOST);
            proxyPort = prefs.getInt("proxy_port", DEFAULT_PROXY_PORT);
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
                // 0. 解压 geoip.dat / geosite.dat 到 filesDir（v2ray 启动前必须就绪）
                extractGeoAssets();

                // 1. 建立 TUN 设备
                Builder builder = new Builder();
                builder.setSession("FightMaster")
                       .addAddress("26.26.26.1", 30)
                       .addRoute("0.0.0.0", 0)
                       .addDnsServer("223.5.5.5")
                       .addDnsServer("1.1.1.1")
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
                Tun2socks.setLocalDNS("223.5.5.5:53,1.1.1.1:53");

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
                sendStatus("connected", proxyHost + ":" + proxyPort);

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
        return instance != null && instance.running;
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
