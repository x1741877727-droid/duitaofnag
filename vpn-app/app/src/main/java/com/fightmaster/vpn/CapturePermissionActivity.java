package com.fightmaster.vpn;

import android.app.Activity;
import android.content.Intent;
import android.media.projection.MediaProjectionManager;
import android.os.Bundle;
import android.util.Log;

/**
 * 透明 Activity — 仅用于触发 MediaProjection 授权对话框
 *
 * 工作流程：
 *   1. 任何地方（CommandReceiver / Java 内部）想启动 CaptureService 时
 *      不能直接 startService，因为 MediaProjection 必须从 Activity 发起
 *   2. 通过 Intent 启动本 Activity → createScreenCaptureIntent → 系统弹"立即开始"
 *      （LDPlayer 9 root 环境可用 `appops set ... PROJECT_MEDIA allow` 跳过）
 *   3. onActivityResult 拿到 (resultCode, data) token，启动 CaptureService 把 token 传过去
 *   4. Activity 立即 finish，不可见
 *
 * ADB 直接调用：
 *   am start -n com.fightmaster.vpn/.CapturePermissionActivity
 */
public class CapturePermissionActivity extends Activity {

    private static final String TAG = "FMCapPerm";
    private static final int REQ_PROJECTION = 200;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        // 不 setContentView，保持透明
        MediaProjectionManager mpm =
                (MediaProjectionManager) getSystemService(MEDIA_PROJECTION_SERVICE);
        if (mpm == null) {
            Log.e(TAG, "MediaProjectionManager 不可用");
            finish();
            return;
        }
        Intent permissionIntent = mpm.createScreenCaptureIntent();
        try {
            startActivityForResult(permissionIntent, REQ_PROJECTION);
        } catch (Exception e) {
            Log.e(TAG, "启动权限请求失败: " + e);
            finish();
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != REQ_PROJECTION) {
            finish();
            return;
        }
        if (resultCode != RESULT_OK || data == null) {
            Log.w(TAG, "用户拒绝/取消 MediaProjection 授权");
            finish();
            return;
        }

        Log.i(TAG, "MediaProjection 授权拿到，启动 CaptureService");
        Intent svc = new Intent(this, CaptureService.class);
        svc.setAction(CaptureService.ACTION_START);
        svc.putExtra(CaptureService.EXTRA_RESULT_CODE, resultCode);
        svc.putExtra(CaptureService.EXTRA_RESULT_DATA, data);
        // 转发由 CommandReceiver 传过来的可选参数
        Intent self = getIntent();
        if (self != null) {
            for (String k : new String[]{
                    CaptureService.EXTRA_WIDTH, CaptureService.EXTRA_HEIGHT,
                    CaptureService.EXTRA_BITRATE, CaptureService.EXTRA_FPS}) {
                if (self.hasExtra(k)) {
                    svc.putExtra(k, self.getIntExtra(k, 0));
                }
            }
        }
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.O) {
            startForegroundService(svc);
        } else {
            startService(svc);
        }
        finish();
    }
}
