# 保留 service / activity 入口 — Android framework 通过反射拉起.
-keep class com.gamebot.overlay.MainActivity { *; }
-keep class com.gamebot.overlay.OverlayService { *; }

# VerifyClient.Result 字段被 MainActivity 直接访问.
-keep class com.gamebot.overlay.VerifyClient$Result { *; }

# 默认混淆其余符号 — 减小 apk 体积 + 抵抗反编译.
-dontwarn org.json.**
