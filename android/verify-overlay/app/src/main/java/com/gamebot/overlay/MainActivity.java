package com.gamebot.overlay;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.AsyncTask;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup.LayoutParams;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

import androidx.core.content.ContextCompat;

/**
 * 极简单页: 一个"检查"按钮 + 状态文本.
 * 检查通过 → 启 OverlayService (在游戏屏幕底部画一行文字).
 * 检查失败 → 停 OverlayService + 显示错误.
 */
public class MainActivity extends Activity {
    private TextView statusText;
    private Button checkBtn;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // 简单代码 build UI, 不用 layout xml
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setGravity(Gravity.CENTER);
        int pad = (int) (24 * getResources().getDisplayMetrics().density);
        root.setPadding(pad, pad, pad, pad);
        root.setBackgroundColor(Color.WHITE);

        TextView title = new TextView(this);
        title.setText(R.string.app_name);
        title.setTextSize(20f);
        title.setTextColor(0xFF1A1A17);
        title.setGravity(Gravity.CENTER);

        checkBtn = new Button(this);
        checkBtn.setText(R.string.btn_check);
        checkBtn.setTextSize(16f);

        statusText = new TextView(this);
        statusText.setText(R.string.status_idle);
        statusText.setTextSize(14f);
        statusText.setTextColor(0xFF7D7869);
        statusText.setGravity(Gravity.CENTER);

        LinearLayout.LayoutParams gap = new LinearLayout.LayoutParams(
                LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT);
        gap.topMargin = pad;
        root.addView(title);
        root.addView(checkBtn, gap);
        root.addView(statusText, gap);
        setContentView(root);

        checkBtn.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                doCheck();
            }
        });
    }

    private void doCheck() {
        // 先确认 overlay 权限
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M
                && !Settings.canDrawOverlays(this)) {
            statusText.setText(R.string.status_need_permission);
            Intent intent = new Intent(
                    Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    Uri.parse("package:" + getPackageName()));
            startActivity(intent);
            return;
        }

        checkBtn.setEnabled(false);
        statusText.setText(R.string.status_checking);

        // 异步去探 verify 端点 (主线程不能跑 HTTP)
        new AsyncTask<Void, Void, VerifyClient.Result>() {
            @Override
            protected VerifyClient.Result doInBackground(Void... voids) {
                return VerifyClient.probe();
            }

            @Override
            protected void onPostExecute(VerifyClient.Result r) {
                checkBtn.setEnabled(true);
                if (r.ok) {
                    long m = r.uptimeSeconds / 60;
                    statusText.setText(getString(R.string.status_ok_fmt, m));
                    statusText.setTextColor(0xFF16A34A);
                    // 启浮窗
                    Intent svc = new Intent(MainActivity.this, OverlayService.class);
                    ContextCompat.startForegroundService(MainActivity.this, svc);
                } else {
                    statusText.setText(getString(R.string.status_fail_fmt, r.error));
                    statusText.setTextColor(0xFFDC2626);
                    // 关浮窗
                    stopService(new Intent(MainActivity.this, OverlayService.class));
                }
            }
        }.execute();
    }
}
