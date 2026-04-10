# 弹窗拦截进度

## 已完成

### 1. memscan 工具 ✅
- Go 编译的静态 x86_64 二进制，在 Android 模拟器上完美运行
- 支持 64-bit 地址，0.24秒扫完全部进程内存
- 支持搜索和写入：`memscan <pid> search <pattern>` / `memscan <pid> write <hex_addr> <hex_bytes>`
- 位置：`/tmp/memscan_go` (Mac)，设备上 `/data/local/tmp/memscan`
- 源码：`scripts/memscan.c`（但实际用的是 Go 版）

### 2. 关键发现

#### 反作弊限制
- ❌ Frida `Interceptor.attach` ARM 代码 → 反作弊检测代码段完整性 → 闪退
- ❌ x86 libc hook → ARM 引擎的 I/O 走 houdini 翻译层的 ARM libc → 无效
- ❌ DNS 拦截 → 活动数据来自 `down.qq.com`（不能全拦）+ 本地缓存
- ❌ 文件重命名 → 游戏有完整性校验，重新下载 2GB
- ❌ Frida 大量 Memory.readByteArray → 触发反作弊检测
- ✅ `Memory.read/writeU8` 小量操作不触发反作弊
- ✅ `/proc/pid/mem` 直接读写 → 完全不注入，反作弊管不到
- ✅ memscan Go 二进制 → 纯 pread/pwrite 系统调用

#### 弹窗机制
- 弹窗是 UE4 PixUI 渲染的 H5 页面（不是 Java 层）
- 活动配置从 `faascjm.native.qq.com` / `jsonatm.broker.tplay.qq.com` / `cjm.broker.tplay.qq.com` 拉取
- 图片从 `cgugccdn.pg.qq.com` / `game.gtimg.cn` 加载
- 本地缓存在 UE4 pak 文件和堆内存中

#### 弹窗控制字段（从内存搜索发现）
搜索 `isShow` 找到 101 个匹配，关键字段：
- `isShowToday` — 今日是否显示（对应"今日内不再弹出"）
- `isShowNotice` — 是否显示通知
- `isShowForever` — 永久显示
- `isShowTime` — 按时间显示
- `isShowResult` / `isShowReward` — 结果/奖励弹窗

## 下一步计划

### 方案：GG 式内存搜索 + 写入
1. 游戏启动进大厅（不注入任何东西）
2. 用 memscan 搜索 `isShowToday` 找到地址
3. 读取该地址附近的数据结构，找到控制 bool 值
4. 用 `memscan write` 将 `isShowToday` 对应的 bool 从 true 改为 false
5. 验证弹窗是否消失

### 需要解决的问题
- 每次游戏重启地址都会变（ASLR），需要通过特征搜索定位
- 需要找到 `isShowToday` 字段对应的 bool 值的确切偏移
- 使用"勾选前后对比"方法确定哪个字节是控制开关

## 远程连接信息
- tunnel: cloudflared 临时隧道
- ADB serial: emulator-5556（可能变）
- 密码通过隧道 header x-auth 传递
