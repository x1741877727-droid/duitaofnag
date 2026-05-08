# verify-overlay — 加速器校验浮窗 APK

模拟器内运行的小工具. 一个按钮验证 gameproxy TUN 是否真的在转发, 验证通过后在屏幕底部中央显示 "fightmaster 已启动" 浮窗.

## 它解决什么

- 加速器没启动也能进游戏 → 直连 → 封号. 这个 APK 给一个**主动检测信号**: 点 检查, 看到绿色"已启动"才开局.
- 浮窗一直挂着 → 玩家随时能看到状态 (心理 + 物理双保险).
- 不联网走外部接口 → 检测请求只能命中 gameproxy 内置的虚拟域名 `gameproxy-verify-json`, 没经过 TUN 直接 fail.

## 如何工作

```
[APK] 点 检查
   └─→ HTTP GET http://gameproxy-verify-json/
            └─→ TUN 拦截虚拟域名, 不出网, 直接返回 JSON {ok, uptime_seconds, ...}
       ├─ 200 + ok=true   → 显示绿字 + startForegroundService(OverlayService)
       └─ 失败 / 超时       → 显示红字, 不挂浮窗

[OverlayService] WindowManager + TYPE_APPLICATION_OVERLAY
   └─→ 屏幕底部中央 "fightmaster 已启动" 半透明绿底文字
```

## Build

**前置**:
- Windows + JDK 17+ (`JAVA_HOME` 指向 JDK)
- Android SDK (cmdline-tools + platform-tools + build-tools-34) — 装好后设 `ANDROID_HOME`
- Gradle 8.4+ (一次性, build_apk.bat 会自动生成 wrapper)

**一键 build**:

```cmd
cd D:\game-automation\android\verify-overlay
build_apk.bat
```

输出 → `D:\game-automation\fixtures\verify-overlay.apk`

## Install (LDPlayer)

```cmd
adb connect 127.0.0.1:5555
adb install -r D:\game-automation\fixtures\verify-overlay.apk
```

或多实例批量:

```cmd
for /L %i in (5555,2,5575) do adb -s 127.0.0.1:%i install -r D:\game-automation\fixtures\verify-overlay.apk
```

## Use

1. 模拟器启动后, 桌面找到 **加速器校验** 图标 → 打开
2. 点 **检查** 按钮
3. 首次会跳"显示在其他应用上层"权限页 → 开 → 返回 APK 再点一次 检查
4. 看到绿字 "✓ 加速器正常 · 已运行 N 分钟" + 屏幕底部出现 "fightmaster 已启动" → 可以开局了
5. 看到红字 "✗ 未启动: ..." → gameproxy 没跑, 检查后端 / TUN

## 关浮窗

回到 APK, 关进程 (从最近任务划掉) — OverlayService 跟着停, 浮窗消失.

或 adb:

```cmd
adb shell am force-stop com.gamebot.overlay
```

## 为什么用 `gameproxy-verify-json` 这个虚拟域名

`gameproxy-go/verify.go` 里写好了 — TUN 拦截这个 host, **不走上游**, 直接本地返回 JSON. 优点:

- 没网也能验 (只要 TUN 在跑)
- 客户端拿到 200 → **物理证明**包走过 TUN, 不是直连
- 不依赖外部 server, 离线 / 跨网都行

## 文件结构

```
verify-overlay/
├── settings.gradle
├── build.gradle               # 顶层
├── gradle.properties
├── build_apk.bat              # 一键 build → fixtures/verify-overlay.apk
├── README.md                  # 本文档
└── app/
    ├── build.gradle
    ├── proguard-rules.pro
    └── src/main/
        ├── AndroidManifest.xml
        ├── java/com/gamebot/overlay/
        │   ├── MainActivity.java     # 检查按钮 + 状态文字
        │   ├── OverlayService.java   # foreground service + 浮窗 TextView
        │   └── VerifyClient.java     # HTTP 调 gameproxy-verify-json
        └── res/
            ├── values/strings.xml
            └── drawable/ic_launcher.xml
```

## 限制

- 仅 Android 8+ (TYPE_APPLICATION_OVERLAY) — LDPlayer 9 默认 Android 9, OK
- 用户必须手动授权"显示在其他应用上层" — 系统强制, APK 不能跳过
- 浮窗 NOT_TOUCHABLE → 不挡操作, 但也不能点
- `release` 用 debug key 签名 — 客户内网不上 store, 没必要正经签名 keystore
