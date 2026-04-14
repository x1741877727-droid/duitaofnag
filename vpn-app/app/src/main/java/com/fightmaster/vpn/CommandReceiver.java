package com.fightmaster.vpn;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.util.Log;

/**
 * ADB 命令接收器 — 脚本通过广播控制 VPN
 *
 * 启动: adb shell am broadcast -a com.fightmaster.vpn.START
 * 停止: adb shell am broadcast -a com.fightmaster.vpn.STOP
 * 状态: adb shell am broadcast -a com.fightmaster.vpn.STATUS
 */
public class CommandReceiver extends BroadcastReceiver {

    private static final String TAG = "FightMasterCmd";

    @Override
    public void onReceive(Context context, Intent intent) {
        String action = intent.getAction();
        if (action == null) return;

        Log.i(TAG, "Received: " + action);

        switch (action) {
            case "com.fightmaster.vpn.START":
                startVpn(context);
                break;
            case "com.fightmaster.vpn.STOP":
                stopVpn(context);
                break;
            case "com.fightmaster.vpn.STATUS":
                // 状态通过 VPN_STATUS 广播返回
                break;
            case "com.fightmaster.vpn.CAPTURE_ON":
                setCaptureMode(context, true);
                break;
            case "com.fightmaster.vpn.CAPTURE_OFF":
                setCaptureMode(context, false);
                break;
        }
    }

    private void startVpn(Context context) {
        Intent vpnIntent = new Intent(context, FightMasterVpnService.class);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            context.startForegroundService(vpnIntent);
        } else {
            context.startService(vpnIntent);
        }
    }

    private void stopVpn(Context context) {
        Intent vpnIntent = new Intent(context, FightMasterVpnService.class);
        vpnIntent.setAction("STOP");
        context.startService(vpnIntent);
    }

    private void setCaptureMode(Context context, boolean enabled) {
        // 通过 Intent action 传递给 Service（比 static instance 更可靠）
        Intent vpnIntent = new Intent(context, FightMasterVpnService.class);
        vpnIntent.setAction(enabled ? "CAPTURE_ON" : "CAPTURE_OFF");
        context.startService(vpnIntent);
        Log.i(TAG, "Sent capture mode " + (enabled ? "ON" : "OFF") + " to service via Intent");
    }
}
