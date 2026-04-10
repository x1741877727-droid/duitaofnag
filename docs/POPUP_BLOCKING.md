# 弹窗拦截方案

> 实测日期: 2026-04-10
> 测试实例: emulator-5554 (实例0)
> 结论: 公告弹窗成功拦截, 活动弹窗无法通过网络拦截

---

## 一、弹窗分类

### 可拦截: 服务端公告弹窗
- 公告页面 (gonggao.png 那种)
- 数据来源: 服务器拉取, 缓存在 `sg_newnotice.sav`
- 图片CDN: `announcecdn.pg.qq.com`
- 新闻跳转: `sy.qq.com`
- 拦截方式: hosts屏蔽 + 删缓存

### 不可拦截: 游戏内置活动弹窗
- 开局领奖励、回归签到、新春活动、赛季结算、见面礼、隐私协议、找队友提示
- 这些由 UE4 引擎内部 Widget 渲染, 不依赖外部CDN
- 只能靠脚本循环清理 (模板匹配X + OCR关键词 + 点击屏幕中央)

---

## 二、hosts屏蔽操作 (每个实例都要执行)

### 前提条件
- ADB已root: `adb -s <serial> root`
- ADB路径: `D:\leidian\LDPlayer9\adb.exe`
- 雷电模拟器 rootfs 是只读的, 需要用 bind-mount

### 执行命令

```bash
ADB="D:\leidian\LDPlayer9\adb.exe"
SERIAL="emulator-5554"  # 改成对应实例的serial

# 1. 确保root
$ADB -s $SERIAL root

# 2. 创建自定义hosts文件 (在可写的 /data/local/tmp/)
$ADB -s $SERIAL shell "cp /etc/hosts /data/local/tmp/hosts"
$ADB -s $SERIAL shell "echo '127.0.0.1 announcecdn.pg.qq.com' >> /data/local/tmp/hosts"
$ADB -s $SERIAL shell "echo '127.0.0.1 sy.qq.com' >> /data/local/tmp/hosts"

# 3. bind-mount覆盖原hosts (重启后失效, 需要重新执行)
$ADB -s $SERIAL shell "mount --bind /data/local/tmp/hosts /etc/hosts"

# 4. 验证
$ADB -s $SERIAL shell "cat /etc/hosts"
# 应该看到:
# 127.0.0.1       localhost
# ::1             ip6-localhost
# 127.0.0.1 announcecdn.pg.qq.com
# 127.0.0.1 sy.qq.com
```

### 6个实例批量执行

```bash
ADB="D:\leidian\LDPlayer9\adb.exe"

for i in 0 1 2; do
  SERIAL="emulator-$((5554 + i * 2))"
  echo "=== $SERIAL ==="
  $ADB -s $SERIAL root
  $ADB -s $SERIAL shell "cp /etc/hosts /data/local/tmp/hosts"
  $ADB -s $SERIAL shell "echo '127.0.0.1 announcecdn.pg.qq.com' >> /data/local/tmp/hosts"
  $ADB -s $SERIAL shell "echo '127.0.0.1 sy.qq.com' >> /data/local/tmp/hosts"
  $ADB -s $SERIAL shell "mount --bind /data/local/tmp/hosts /etc/hosts"
  echo "Done"
done
```

> 注意: serial编号取决于当前运行的实例数量, 用 `adb devices` 确认

---

## 三、删除公告缓存

```bash
ADB="D:\leidian\LDPlayer9\adb.exe"
SERIAL="emulator-5554"
SAVEDIR="/sdcard/Android/data/com.tencent.tmgp.pubgmhd/files/UE4Game/ShadowTrackerExtra/ShadowTrackerExtra/Saved/SaveGames"

# 删除公告缓存 (游戏关闭状态下执行)
$ADB -s $SERIAL shell "rm $SAVEDIR/sg_newnotice.sav"
$ADB -s $SERIAL shell "rm $SAVEDIR/sg_newnotice_ext.sav"

# 删除MSDK弹窗HTML缓存
$ADB -s $SERIAL shell "rm -rf /data/data/com.tencent.tmgp.pubgmhd/files/popup/html/*"
```

---

## 四、注意事项

1. **bind-mount 重启后失效** — 模拟器重启后需要重新执行 hosts 屏蔽命令。自动化脚本应在启动游戏前执行。

2. **不要屏蔽 `game.gtimg.cn`** — 这个域名除了公告图片, 可能还用于游戏更新资源, 屏蔽可能导致更新失败。

3. **删缓存时确保游戏已关闭** — 游戏运行时删文件可能无效 (已在内存中) 或导致异常。

4. **屏蔽的域名和作用**:

| 域名 | 作用 | 屏蔽后效果 |
|------|------|-----------|
| `announcecdn.pg.qq.com` | 公告图片CDN | 公告弹窗无法加载图片, 不显示 |
| `sy.qq.com` | 新闻详情页跳转 | 点击公告不会跳转网页 |

5. **活动弹窗仍需脚本处理的种类** (无法通过网络拦截):
   - 开局领奖励 (youxihuodong-7.png)
   - 回归签到 (youxihuodong-7.1.png)
   - 赛季结算连续链 (7.1.2 ~ 7.1.5)
   - 新玩法/活动推广 (7.1.1, 7.2, 7.2.1, 7.2.3)
   - 见面礼 (jianmianli.png)
   - 隐私设置/协议 (yinsi.png, yinsi2.png)
   - 找队友提示 (zhoaduiyou.png)
   - 内存过低提醒 (neicundi-5.1.png)

---

## 五、数据探索备忘

### 游戏数据路径

```
/data/data/com.tencent.tmgp.pubgmhd/          (需要 adb root)
├── shared_prefs/MSDKPopupCommon.xml           仅 install_id
├── shared_prefs/itop.xml                      登录信息 (MSDK加密)
├── files/popup/html/                          MSDK弹窗HTML (加密)
├── files/GCloud.mmap3                         GCloud配置
└── databases/                                 beacon/crashSight日志

/sdcard/Android/data/com.tencent.tmgp.pubgmhd/files/UE4Game/
└── ShadowTrackerExtra/ShadowTrackerExtra/Saved/
    ├── Config/Android/                        引擎配置 (音画质量等)
    └── SaveGames/
        ├── sg_newnotice.sav                   ← 公告缓存 (GVAS, ~116KB, 含JSON)
        ├── sg_newnotice_ext.sav               ← 公告扩展缓存
        ├── sg_act*.sav                        活动数据
        ├── sg_hostGift*.sav                   礼包数据
        ├── sg_sys*.sav                        系统数据
        ├── playerprefs.sav                    玩家偏好
        ├── Active.sav / Cached.sav            活动状态缓存
        └── sg_loginError.sav                  登录错误记录
```

### sg_newnotice.sav 格式

- UE4 GVAS 二进制文件 (Magic: `GVAS`, Engine: 4.18)
- SaveGame类: `CommonSaveGame_C`
- 属性: `ConfigStringMap` (MapProperty<StrProperty, StrProperty>)
- 3个Key: `NewNoticeCache_1`, `NewNoticeCache_2`, `NewNoticeCache_3`
- Value: UTF-16LE 编码的 JSON, 包含公告内容和图片URL
- 涉及域名: `announcecdn.pg.qq.com`, `game.gtimg.cn`, `sy.qq.com`

### 游戏进程信息
- 包名: `com.tencent.tmgp.pubgmhd`
- 只有一个Activity: `com.epicgames.ue4.GameActivity` (所有弹窗都是游戏内部Widget)
- 基于 UE4 Release-4.18
