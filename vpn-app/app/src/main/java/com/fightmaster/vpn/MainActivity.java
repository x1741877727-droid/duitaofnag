package com.fightmaster.vpn;

import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.net.VpnService;
import android.os.Build;
import android.os.Bundle;
import android.view.View;
import android.widget.TextView;

/**
 * FightMaster 主界面 — 现代深色主题，一键连接
 */
public class MainActivity extends Activity {

    private static final int VPN_REQUEST_CODE = 100;

    private View connectBtn;
    private View outerRing;
    private TextView statusText;
    private TextView engineStatus;
    private boolean connected = false;

    private final BroadcastReceiver statusReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            String status = intent.getStringExtra("status");
            String detail = intent.getStringExtra("detail");
            runOnUiThread(() -> updateUI(status, detail));
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        connectBtn = findViewById(R.id.connect_btn);
        outerRing = findViewById(R.id.outer_ring);
        statusText = findViewById(R.id.status_text);
        engineStatus = findViewById(R.id.engine_status);

        connectBtn.setOnClickListener(v -> onConnectClick());

        // 应用选择按钮
        findViewById(R.id.app_select_btn).setOnClickListener(v -> {
            startActivity(new Intent(this, AppSelectActivity.class));
        });

        // 验证按钮 — 打开浏览器访问验证页面（用假域名，proxy 拦截返回状态页）
        findViewById(R.id.verify_btn).setOnClickListener(v -> {
            Intent browserIntent = new Intent(Intent.ACTION_VIEW,
                    android.net.Uri.parse("http://gameproxy-verify"));
            startActivity(browserIntent);
        });

        registerReceiver(statusReceiver, new IntentFilter("com.fightmaster.vpn.VPN_STATUS"));

        // 处理 ADB Intent 启动
        handleIntent(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        handleIntent(intent);
    }

    private void handleIntent(Intent intent) {
        if (intent != null && "com.fightmaster.vpn.CONNECT".equals(intent.getAction())) {
            // ADB 触发: adb shell am start -a com.fightmaster.vpn.CONNECT
            onConnectClick();
        }
    }

    private void onConnectClick() {
        if (connected) {
            // 断开
            Intent stopIntent = new Intent(this, FightMasterVpnService.class);
            stopIntent.setAction("STOP");
            startService(stopIntent);
        } else {
            // 连接 — 先请求 VPN 权限
            Intent prepareIntent = VpnService.prepare(this);
            if (prepareIntent != null) {
                startActivityForResult(prepareIntent, VPN_REQUEST_CODE);
            } else {
                startVpnService();
            }
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == VPN_REQUEST_CODE && resultCode == RESULT_OK) {
            startVpnService();
        }
    }

    private void startVpnService() {
        Intent intent = new Intent(this, FightMasterVpnService.class);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(intent);
        } else {
            startService(intent);
        }
        updateUI("connecting", "");
    }

    private void updateUI(String status, String detail) {
        switch (status) {
            case "connected":
                connected = true;
                statusText.setText("已连接 — 香港节点");
                statusText.setTextColor(0xFF00FF88);
                connectBtn.setBackgroundResource(R.drawable.fm_btn_connected);
                outerRing.setBackgroundResource(R.drawable.fm_ring_connected);
                engineStatus.setText("11 条规则就绪");
                engineStatus.setTextColor(0xFF00FF88);
                break;
            case "connecting":
                statusText.setText("正在连接...");
                statusText.setTextColor(0xFF00D4FF);
                break;
            case "disconnected":
                connected = false;
                statusText.setText("未连接");
                statusText.setTextColor(0xFF5A6178);
                connectBtn.setBackgroundResource(R.drawable.fm_btn_circle);
                outerRing.setBackgroundResource(R.drawable.fm_ring);
                engineStatus.setText("待命");
                engineStatus.setTextColor(0xFFC8CDDA);
                break;
            case "error":
                connected = false;
                statusText.setText("连接失败: " + detail);
                statusText.setTextColor(0xFFFF4444);
                connectBtn.setBackgroundResource(R.drawable.fm_btn_circle);
                outerRing.setBackgroundResource(R.drawable.fm_ring);
                break;
        }
    }

    @Override
    protected void onDestroy() {
        unregisterReceiver(statusReceiver);
        super.onDestroy();
    }
}
