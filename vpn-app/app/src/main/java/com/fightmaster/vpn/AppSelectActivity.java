package com.fightmaster.vpn;

import android.app.Activity;
import android.content.SharedPreferences;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.CheckBox;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * 应用选择界面 — 勾选哪些 APP 走 VPN 代理
 */
public class AppSelectActivity extends Activity {

    public static final String PREF_NAME = "fightmaster_apps";
    public static final String KEY_SELECTED = "selected_packages";

    // 默认走代理的包名
    private static final String[] DEFAULT_APPS = {
        "com.tencent.tmgp.pubgmhd",   // 和平精英
        "com.android.browser",          // 系统浏览器
        "com.android.chrome",           // Chrome
        "com.google.android.webview",   // WebView
    };

    private final List<AppItem> appList = new ArrayList<>();
    private Set<String> selectedPkgs;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // 读取已保存的选择
        SharedPreferences prefs = getSharedPreferences(PREF_NAME, MODE_PRIVATE);
        selectedPkgs = new HashSet<>(prefs.getStringSet(KEY_SELECTED, getDefaultSet()));

        // 加载已安装应用
        loadApps();

        // 构建 UI
        buildUI();
    }

    private Set<String> getDefaultSet() {
        Set<String> set = new HashSet<>();
        Collections.addAll(set, DEFAULT_APPS);
        return set;
    }

    private void loadApps() {
        PackageManager pm = getPackageManager();
        List<ApplicationInfo> installed = pm.getInstalledApplications(0);

        for (ApplicationInfo info : installed) {
            // 跳过自己
            if (info.packageName.equals(getPackageName())) continue;

            String label = pm.getApplicationLabel(info).toString();
            boolean isSystem = (info.flags & ApplicationInfo.FLAG_SYSTEM) != 0;

            appList.add(new AppItem(info.packageName, label, isSystem));
        }

        // 已选的排前面，然后按名称排序
        Collections.sort(appList, (a, b) -> {
            boolean aSelected = selectedPkgs.contains(a.pkg);
            boolean bSelected = selectedPkgs.contains(b.pkg);
            if (aSelected != bSelected) return aSelected ? -1 : 1;
            return a.label.compareToIgnoreCase(b.label);
        });
    }

    private void buildUI() {
        // 根布局
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(0xFF0A0E1A);

        // 标题栏
        LinearLayout titleBar = new LinearLayout(this);
        titleBar.setOrientation(LinearLayout.HORIZONTAL);
        titleBar.setGravity(Gravity.CENTER_VERTICAL);
        titleBar.setPadding(dp(16), dp(12), dp(16), dp(12));
        titleBar.setBackgroundColor(0xFF141929);

        TextView backBtn = new TextView(this);
        backBtn.setText("< 返回");
        backBtn.setTextColor(0xFF00D4FF);
        backBtn.setTextSize(16);
        backBtn.setPadding(dp(8), dp(8), dp(16), dp(8));
        backBtn.setOnClickListener(v -> finish());
        titleBar.addView(backBtn);

        TextView title = new TextView(this);
        title.setText("选择代理应用");
        title.setTextColor(0xFFC8CDDA);
        title.setTextSize(18);
        title.setTypeface(null, android.graphics.Typeface.BOLD);
        LinearLayout.LayoutParams titleParams = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1);
        titleBar.addView(title, titleParams);

        TextView saveBtn = new TextView(this);
        saveBtn.setText("保存");
        saveBtn.setTextColor(0xFF00FF88);
        saveBtn.setTextSize(16);
        saveBtn.setPadding(dp(16), dp(8), dp(8), dp(8));
        saveBtn.setOnClickListener(v -> saveAndFinish());
        titleBar.addView(saveBtn);

        root.addView(titleBar);

        // 提示文字
        TextView hint = new TextView(this);
        hint.setText("勾选的应用流量将通过 VPN 代理转发，其他应用直连");
        hint.setTextColor(0xFF5A6178);
        hint.setTextSize(13);
        hint.setPadding(dp(16), dp(8), dp(16), dp(8));
        root.addView(hint);

        // 分割线
        View divider = new View(this);
        divider.setBackgroundColor(0xFF1C2235);
        root.addView(divider, new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, dp(1)));

        // 应用列表
        ScrollView scrollView = new ScrollView(this);
        LinearLayout listContainer = new LinearLayout(this);
        listContainer.setOrientation(LinearLayout.VERTICAL);

        for (AppItem item : appList) {
            listContainer.addView(createAppRow(item));
        }

        scrollView.addView(listContainer);
        LinearLayout.LayoutParams scrollParams = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, 0, 1);
        root.addView(scrollView, scrollParams);

        setContentView(root);
    }

    private View createAppRow(AppItem item) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(dp(16), dp(10), dp(16), dp(10));

        // 应用图标
        ImageView icon = new ImageView(this);
        try {
            icon.setImageDrawable(getPackageManager().getApplicationIcon(item.pkg));
        } catch (PackageManager.NameNotFoundException e) {
            icon.setImageResource(android.R.drawable.sym_def_app_icon);
        }
        LinearLayout.LayoutParams iconParams = new LinearLayout.LayoutParams(dp(40), dp(40));
        iconParams.setMarginEnd(dp(12));
        row.addView(icon, iconParams);

        // 应用名 + 包名
        LinearLayout textCol = new LinearLayout(this);
        textCol.setOrientation(LinearLayout.VERTICAL);
        LinearLayout.LayoutParams textParams = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1);

        TextView labelTv = new TextView(this);
        labelTv.setText(item.label);
        labelTv.setTextColor(0xFFC8CDDA);
        labelTv.setTextSize(15);
        labelTv.setSingleLine(true);
        textCol.addView(labelTv);

        TextView pkgTv = new TextView(this);
        pkgTv.setText(item.pkg);
        pkgTv.setTextColor(0xFF5A6178);
        pkgTv.setTextSize(11);
        pkgTv.setSingleLine(true);
        textCol.addView(pkgTv);

        row.addView(textCol, textParams);

        // 勾选框
        CheckBox cb = new CheckBox(this);
        cb.setChecked(selectedPkgs.contains(item.pkg));
        cb.setOnCheckedChangeListener((buttonView, isChecked) -> {
            if (isChecked) {
                selectedPkgs.add(item.pkg);
            } else {
                selectedPkgs.remove(item.pkg);
            }
        });
        row.addView(cb);

        // 点击整行切换
        row.setOnClickListener(v -> cb.setChecked(!cb.isChecked()));

        // 分割线
        LinearLayout wrapper = new LinearLayout(this);
        wrapper.setOrientation(LinearLayout.VERTICAL);
        wrapper.addView(row);
        View div = new View(this);
        div.setBackgroundColor(0xFF1C2235);
        wrapper.addView(div, new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, 1));

        return wrapper;
    }

    private void saveAndFinish() {
        SharedPreferences.Editor editor = getSharedPreferences(PREF_NAME, MODE_PRIVATE).edit();
        editor.putStringSet(KEY_SELECTED, selectedPkgs);
        editor.apply();
        finish();
    }

    private int dp(int value) {
        return (int) (value * getResources().getDisplayMetrics().density);
    }

    /** 获取选中的包名列表（供 VpnService 调用） */
    public static Set<String> getSelectedApps(android.content.Context context) {
        SharedPreferences prefs = context.getSharedPreferences(PREF_NAME, MODE_PRIVATE);
        Set<String> defaults = new HashSet<>();
        Collections.addAll(defaults, DEFAULT_APPS);
        return prefs.getStringSet(KEY_SELECTED, defaults);
    }

    private static class AppItem {
        final String pkg;
        final String label;
        final boolean isSystem;

        AppItem(String pkg, String label, boolean isSystem) {
            this.pkg = pkg;
            this.label = label;
            this.isSystem = isSystem;
        }
    }
}
