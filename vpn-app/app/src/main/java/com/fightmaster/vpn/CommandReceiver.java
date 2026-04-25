package com.fightmaster.vpn;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.util.Log;

/**
 * ADB 命令接收器 — 脚本通过广播控制 VPN + Capture
 *
 * VPN:
 *   启动: adb shell am broadcast -a com.fightmaster.vpn.START -n com.fightmaster.vpn/.CommandReceiver
 *   停止: adb shell am broadcast -a com.fightmaster.vpn.STOP -n com.fightmaster.vpn/.CommandReceiver
 *   启动(自定义代理): --es proxy_host "1.2.3.4" --ei proxy_port 9900
 *
 * Capture (屏幕捕获):
 *   启动: adb shell am broadcast -a com.fightmaster.vpn.CAPTURE_START -n com.fightmaster.vpn/.CommandReceiver
 *   停止: adb shell am broadcast -a com.fightmaster.vpn.CAPTURE_STOP -n com.fightmaster.vpn/.CommandReceiver
 *   带参数: --ei width 1280 --ei height 720 --ei bitrate 1500000 --ei fps 15
 */
public class CommandReceiver extends BroadcastReceiver {

    private static final String TAG = "FightMasterCmd";

    @Override
    public void onReceive(Context context, Intent intent) {
        String action = intent.getAction();
        if (action == null) return;

        Log.i(TAG, "Received: " + action);

        switch (action) {
            case FightMasterVpnService.ACTION_START:
                startVpn(context, intent);
                break;
            case FightMasterVpnService.ACTION_STOP:
                stopVpn(context);
                break;
            case FightMasterVpnService.ACTION_STATUS:
                // 状态通过 VPN_STATUS 广播返回
                break;
            case CaptureService.ACTION_START:
                startCapture(context, intent);
                break;
            case CaptureService.ACTION_STOP:
                stopCapture(context);
                break;
            default:
                Log.w(TAG, "Unknown action: " + action);
        }
    }

    private void startCapture(Context context, Intent origIntent) {
        // CAPTURE_START 广播 → 启动 CapturePermissionActivity
        // (MediaProjection 必须从 Activity 发起授权请求)
        Intent activityIntent = new Intent(context, CapturePermissionActivity.class);
        activityIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        // 转发可选参数（CapturePermissionActivity 收到后传给 CaptureService）
        if (origIntent.hasExtra(CaptureService.EXTRA_WIDTH)) {
            activityIntent.putExtra(CaptureService.EXTRA_WIDTH,
                    origIntent.getIntExtra(CaptureService.EXTRA_WIDTH, 1280));
        }
        if (origIntent.hasExtra(CaptureService.EXTRA_HEIGHT)) {
            activityIntent.putExtra(CaptureService.EXTRA_HEIGHT,
                    origIntent.getIntExtra(CaptureService.EXTRA_HEIGHT, 720));
        }
        if (origIntent.hasExtra(CaptureService.EXTRA_BITRATE)) {
            activityIntent.putExtra(CaptureService.EXTRA_BITRATE,
                    origIntent.getIntExtra(CaptureService.EXTRA_BITRATE, 1_500_000));
        }
        if (origIntent.hasExtra(CaptureService.EXTRA_FPS)) {
            activityIntent.putExtra(CaptureService.EXTRA_FPS,
                    origIntent.getIntExtra(CaptureService.EXTRA_FPS, 15));
        }
        context.startActivity(activityIntent);
    }

    private void stopCapture(Context context) {
        Intent svc = new Intent(context, CaptureService.class);
        svc.setAction(CaptureService.ACTION_STOP);
        context.startService(svc);
    }

    private void startVpn(Context context, Intent origIntent) {
        Intent vpnIntent = new Intent(context, FightMasterVpnService.class);
        // 转发 proxy_host / proxy_port extras
        if (origIntent.hasExtra(FightMasterVpnService.EXTRA_PROXY_HOST)) {
            vpnIntent.putExtra(FightMasterVpnService.EXTRA_PROXY_HOST,
                origIntent.getStringExtra(FightMasterVpnService.EXTRA_PROXY_HOST));
            vpnIntent.putExtra(FightMasterVpnService.EXTRA_PROXY_PORT,
                origIntent.getIntExtra(FightMasterVpnService.EXTRA_PROXY_PORT, 9900));
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            context.startForegroundService(vpnIntent);
        } else {
            context.startService(vpnIntent);
        }
    }

    private void stopVpn(Context context) {
        Intent vpnIntent = new Intent(context, FightMasterVpnService.class);
        vpnIntent.setAction(FightMasterVpnService.ACTION_STOP);
        context.startService(vpnIntent);
    }
}
