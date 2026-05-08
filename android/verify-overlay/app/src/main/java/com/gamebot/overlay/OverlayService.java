package com.gamebot.overlay;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.graphics.PixelFormat;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.os.IBinder;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * 浮窗服务: 屏幕底部正中, 一只小猫趴着 + 上方一个对话气泡 "fightmaster 已启动".
 * 整体很小, 不挡操作.
 */
public class OverlayService extends Service {
    private static final String NOTIF_CHANNEL = "gamebot_overlay";
    private WindowManager wm;
    private View root;

    @Override
    public IBinder onBind(Intent intent) { return null; }

    @Override
    public void onCreate() {
        super.onCreate();
        startForegroundCompat();
        showOverlay();
    }

    @Override
    public void onDestroy() {
        if (wm != null && root != null) {
            try { wm.removeView(root); } catch (Throwable ignored) {}
        }
        super.onDestroy();
    }

    private void startForegroundCompat() {
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel ch = new NotificationChannel(
                    NOTIF_CHANNEL, "GameBot 浮窗", NotificationManager.IMPORTANCE_MIN);
            ch.setShowBadge(false);
            nm.createNotificationChannel(ch);
        }
        Notification n;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            n = new Notification.Builder(this, NOTIF_CHANNEL)
                    .setContentTitle("GameBot 加速器")
                    .setContentText("浮窗运行中")
                    .setSmallIcon(android.R.drawable.ic_lock_idle_lock)
                    .setOngoing(true)
                    .build();
        } else {
            n = new Notification.Builder(this)
                    .setContentTitle("GameBot 加速器")
                    .setContentText("浮窗运行中")
                    .setSmallIcon(android.R.drawable.ic_lock_idle_lock)
                    .setOngoing(true)
                    .build();
        }
        startForeground(1001, n);
    }

    private int dp(float v) {
        return (int) TypedValue.applyDimension(
                TypedValue.COMPLEX_UNIT_DIP, v, getResources().getDisplayMetrics());
    }

    private void showOverlay() {
        wm = (WindowManager) getSystemService(Context.WINDOW_SERVICE);

        // 垂直布局: 上 = 气泡, 下 = 猫. col 整体贴屏幕底, 猫底边 = 屏幕底边.
        LinearLayout col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        col.setGravity(Gravity.CENTER_HORIZONTAL);

        // 文字气泡: 半透明 ink 黑底 + 白字, 跟猫之间留 4dp 间距
        TextView bubble = new TextView(this);
        bubble.setText(getString(R.string.overlay_text));
        bubble.setTextColor(0xFFFFFFFF);
        bubble.setTextSize(TypedValue.COMPLEX_UNIT_SP, 9f);
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(0xCC1A1A17);          // 80% alpha ink 黑
        bg.setCornerRadius(dp(8));
        bubble.setBackground(bg);
        bubble.setPadding(dp(7), dp(2), dp(7), dp(2));
        bubble.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams lpBubble = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        lpBubble.bottomMargin = dp(2);
        col.addView(bubble, lpBubble);

        // 小猫: 透明 PNG, 高 36dp + adjustViewBounds 自动按 240:174 比例算宽 ≈ 50dp
        ImageView cat = new ImageView(this);
        cat.setImageResource(R.drawable.cat_overlay);
        cat.setScaleType(ImageView.ScaleType.FIT_CENTER);
        cat.setAdjustViewBounds(true);
        LinearLayout.LayoutParams lpCat = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, dp(18));
        col.addView(cat, lpCat);

        root = col;

        int type;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            type = WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY;
        } else {
            type = WindowManager.LayoutParams.TYPE_PHONE;
        }

        WindowManager.LayoutParams lp = new WindowManager.LayoutParams(
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.WRAP_CONTENT,
                type,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                        | WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE
                        | WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN
                        | WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
                PixelFormat.TRANSLUCENT);

        // 屏幕底部正中, 真贴底 (y=0 让猫脚直接到屏幕边缘)
        lp.gravity = Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL;
        lp.y = 0;

        try {
            wm.addView(root, lp);
        } catch (Throwable t) {
            stopSelf();
        }
    }
}
