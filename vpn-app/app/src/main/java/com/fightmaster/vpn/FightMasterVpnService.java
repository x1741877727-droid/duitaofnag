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
    public static final String ACTION_STOP = "com.fightmaster.vpn.STOP";
    public static final String ACTION_STATUS = "com.fightmaster.vpn.STATUS";

    // Intent extra keys
    public static final String EXTRA_PROXY_HOST = "proxy_host";
    public static final String EXTRA_PROXY_PORT = "proxy_port";

    // 代理服务器默认配置
    private static final String DEFAULT_PROXY_HOST = "171.80.4.221";
    private static final int DEFAULT_PROXY_PORT = 9900;
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

            // log
            config.put("log", new JSONObject().put("loglevel", "warning"));

            // dns
            JSONObject dns = new JSONObject();
            dns.put("hosts", new JSONObject().put("gameproxy-verify", "1.2.3.4"));
            dns.put("servers", new JSONArray().put("223.5.5.5").put("8.8.8.8"));
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

            // outboundDetour
            JSONArray detour = new JSONArray();
            detour.put(new JSONObject().put("protocol", "freedom").put("settings", new JSONObject()).put("tag", "direct"));
            detour.put(new JSONObject().put("protocol", "dns").put("settings", new JSONObject()).put("tag", "dns-out"));
            detour.put(new JSONObject().put("protocol", "blackhole").put("settings", new JSONObject()).put("tag", "reject"));
            config.put("outboundDetour", detour);

            // routing rules — 六花模型：默认 DIRECT，只把封号相关 + 游戏协议端口送进代理
            // 2026-04-19 根因修复：上一版 FINAL→PROXY 导致 qq.com OAuth 在 QUIC/sniff 失败时被路由到代理，
            // 腾讯 OAuth 端点从代理来源被拒 → ERR_EMPTY_RESPONSE，且 Chromium BrokenAlternativeServices 缓存留后遗症
            JSONArray rules = new JSONArray();

            // 1) DNS
            rules.put(new JSONObject()
                .put("type", "field").put("port", "53").put("outboundTag", "dns-out"));

            // 2) HIT IP → PROXY（2026-04-19 回滚：REJECT 导致秒封，改回 PROXY 让 gameproxy 做 pos21 改字节）
            rules.put(new JSONObject()
                .put("type", "field")
                .put("ip", new JSONArray()
                    // 原 HIT 抓包段
                    .put("222.189.172.0/24").put("27.155.112.0/24")
                    // 六花"免白"规则 IP（2026-04-18 gitee liuhuaduankou/six-flower-port）
                    .put("211.154.24.135/32")
                    .put("122.96.96.179/32").put("122.96.96.206/32").put("122.96.96.211/32")
                    .put("122.96.96.217/32").put("122.96.96.251/32")
                    .put("59.83.207.176/32")
                    .put("182.50.10.74/32")
                    .put("180.109.171.23/32")
                    .put("180.102.211.18/32").put("180.102.211.42/32")
                    .put("180.102.211.93/32").put("180.102.211.116/32")
                    .put("36.155.186.200/32").put("36.155.202.52/32").put("36.155.202.73/32")
                    .put("36.155.202.119/32").put("36.155.228.118/32").put("36.155.249.82/32")
                    .put("36.155.251.15/32")
                    .put("117.89.177.167/32")
                    .put("222.94.109.22/32")
                    .put("43.135.105.28/32")
                    .put("43.159.233.114/32").put("43.159.233.119/32").put("43.159.233.137/32")
                    .put("43.159.233.192/32").put("43.159.233.204/32")
                    .put("129.226.102.0/24")  // 扩展到 /24 覆盖 HIT 同段其他 IP
                    .put("129.226.103.0/24")  // 同上
                    .put("129.226.107.0/24")) // 实测也有 HIT 443
                .put("outboundTag", "proxy"));

            // 3) ACE 反作弊域名 → PROXY（game_proxy 侧 ace_block）
            rules.put(new JSONObject()
                .put("type", "field")
                .put("domain", new JSONArray()
                    .put("anticheatexpert").put("crashsight"))
                .put("outboundTag", "proxy"));

            // 3b) 六花官方反作弊/检测关键词 → PROXY（gitee liuhuaduankou/six-flower-port 对齐）
            //     DOMAIN-KEYWORD,anti / baidu / jd / yqdk / aliyun + cn.bing.com
            rules.put(new JSONObject()
                .put("type", "field")
                .put("domain", new JSONArray()
                    .put("keyword:anti").put("keyword:baidu").put("keyword:jd")
                    .put("keyword:yqdk").put("keyword:aliyun")
                    .put("cn.bing.com"))
                .put("outboundTag", "proxy"));

            // 4) FAKE-BEACON 8081 → PROXY（game_proxy 假 HTTP 200 响应，六花官方不含游戏端口转发）
            rules.put(new JSONObject()
                .put("type", "field")
                .put("port", "8081")
                .put("outboundTag", "proxy"));

            // 4b) 5692/7889/3013 → PROXY（2026-04-19 抓 JSON 分析 cmd_id，server 端不改包只转发+capture）
            rules.put(new JSONObject()
                .put("type", "field")
                .put("port", "5692,7889,3013")
                .put("outboundTag", "proxy"));

            // 5) 验证页域名 → PROXY（brand_page / verify）
            rules.put(new JSONObject()
                .put("type", "field")
                .put("domain", new JSONArray()
                    .put("gameproxy-verify").put("gameproxy-verify-json")
                    .put("m.baidu.com"))
                .put("outboundTag", "proxy"));

            // 6) FINAL → DIRECT（除 HIT IP 外其他 443 和所有其他端口正常直连，登录/CDN/游戏主服务器全放行）
            rules.put(new JSONObject()
                .put("type", "field").put("outboundTag", "direct").put("port", "0-65535"));

            config.put("routing", new JSONObject()
                .put("settings", new JSONObject()
                    .put("domainStrategy", "IPOnDemand")
                    .put("rules", rules)));

            return config.toString();
        } catch (JSONException e) {
            Log.e(TAG, "Failed to build v2ray config", e);
            return "{}";
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
