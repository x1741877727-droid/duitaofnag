package com.fightmaster.vpn;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.util.Log;

/**
 * ADB 命令接收器 — 脚本通过广播控制 VPN
 *
 * 启动: adb shell am broadcast -a com.fightmaster.vpn.START -n com.fightmaster.vpn/.CommandReceiver
 * 停止: adb shell am broadcast -a com.fightmaster.vpn.STOP -n com.fightmaster.vpn/.CommandReceiver
 * 启动(自定义代理): adb shell am broadcast -a com.fightmaster.vpn.START -n com.fightmaster.vpn/.CommandReceiver --es proxy_host "1.2.3.4" --ei proxy_port 9900
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
            default:
                Log.w(TAG, "Unknown action: " + action);
        }
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
