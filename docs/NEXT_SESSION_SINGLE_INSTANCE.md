# 下一个对话的提示词：单实例实机调试

直接复制以下内容作为新对话的第一条消息：

---

我有一个游戏自动化项目，需要你帮我在 Windows 真机上调试单实例自动化流程（加速器→启动游戏→弹窗清理→大厅确认→组队→地图设置）。

## 当前状态

代码已写好但**从未在真机上跑过**，需要实机调试校准。

### 已完成的代码

| 文件 | 说明 | 状态 |
|------|------|------|
| `backend/automation/screen_matcher.py` | 模板匹配引擎（归一化1280×720+多尺度） | ✅ 本地测试100%准确 |
| `backend/automation/popup_dismisser.py` | 弹窗循环清理（X按钮→操作按钮→点击继续→兜底） | ✅ 逻辑完成，未实测 |
| `backend/automation/single_runner.py` | 单实例运行器（加速器→游戏→弹窗→大厅→组队→地图） | ⚠️ 坐标需校准 |
| `fixtures/templates/` | 22个模板图片（X按钮×7、操作按钮×6、大厅锚点×4等） | ✅ 从真实截图裁剪 |
| `tests/test_template_matching.py` | 模板匹配测试（19/19通过） | ✅ |

### 已知需要调整的问题

1. **组队流程的按钮坐标是估的** — `single_runner.py` 里 `phase_team_create` 和 `phase_team_join` 的 tap 坐标（如组队tab、组队码按钮、分享口令码按钮）都是看截图估算的，实机上可能偏移
2. **地图设置的坐标也是估的** — `phase_map_setup` 里点击地图区域、团队竞技按钮的位置需要校准
3. **剪贴板读写未验证** — `get_clipboard`/`set_clipboard` 用的 `am broadcast -a clipper.get/set`，模拟器上可能需要别的方式
4. **加速器包名未确认** — 代码里写的 `com.tencent.lhjsqxfb`，需要确认是否正确
5. **登录检测不完整** — 目前只有超时检测，没有扫码登录的模板

### 不需要改的（已验证OK）

- 模板匹配引擎和所有模板
- 弹窗清理循环逻辑
- ADB截图/点击基础功能
- 大厅检测（"开始游戏"按钮匹配）
- 加速器状态检测（▶/⏸ 按钮匹配）

## 环境信息

- **Windows机器**通过 cloudflared tunnel 暴露了 Remote Agent
- Remote Agent: FastAPI on port 9100, 密码通过 X-Auth header 认证
- ADB路径: `D:\leidian\LDPlayer9\adb.exe`
- 模拟器实例: emulator-5554 (实例0), emulator-5556 (实例1), emulator-5558 (实例2)
- 游戏包名: `com.tencent.tmgp.pubgmhd`
- 游戏截图分辨率: 1280×720
- Frida已安装: frida-server 16.7.19 在 `/data/local/tmp/frida-server`
- hosts屏蔽已配置: `announcecdn.pg.qq.com` 和 `sy.qq.com` 已屏蔽公告弹窗

## 项目位置

- Mac代码目录: `/Users/Zhuanz/Vexa/game-automation/`
- GitHub: `https://github.com/x1741877727-droid/duitaofnag.git`
- 文档: `docs/FULL_FLOW.md`（完整流程）、`docs/POPUP_BLOCKING.md`（弹窗拦截方案）
- 截图: `fixtures/screenshots/`（31张真实截图）
- 模板: `fixtures/templates/`（22个模板）

## 你需要做的事

### 第一步：远程连接验证环境
1. 我会给你 cloudflared tunnel URL 和密码
2. 通过 Remote Agent 确认 ADB 连接正常
3. 确认游戏在实例0上运行
4. 远程截图一张，确认模板匹配能正常工作

### 第二步：逐阶段实机调试
在实例0上，一个阶段一个阶段跑，每个阶段：截图→确认状态→执行操作→截图验证结果→修正坐标

**阶段0 - 加速器：**
- 启动加速器APK → 截图确认状态 → 点击启动 → 截图确认已连接 → Home键回桌面
- 校准: 确认加速器包名、▶按钮的点击响应

**阶段1 - 启动游戏：**
- 启动游戏 → 等待加载 → 处理公告/内存弹窗 → 等到登录或大厅
- 校准: 公告X按钮的点击位置、内存提醒确定按钮位置

**阶段3 - 弹窗清理：**
- 运行 PopupDismisser → 观察每个弹窗的识别和点击效果
- 校准: 各种X按钮的匹配阈值、操作按钮位置、"点击屏幕继续"的点击位置

**阶段4 - 组队（队长）：**
- 点击组队tab → 点击组队码 → 分享口令码 → 读取剪贴板
- 校准: 组队tab精确位置、组队码按钮位置、分享按钮位置、剪贴板方式

**阶段5 - 组队（队员）：**
- 写剪贴板 → 等弹出加入提示 → 点击加入
- 校准: 加入按钮位置、手动路径的各按钮位置

**阶段6 - 地图设置：**
- 点击地图区域 → 检查模式 → 选地图 → 关补位 → 退出
- 校准: 地图入口位置、团队竞技按钮位置、各地图位置、补位开关位置

### 第三步：端到端运行
把校准后的参数写入代码，从头到尾跑一遍完整的 `run_to_lobby()`，验证能否自动走到大厅。

### 第四步：补充缺失截图
运行过程中截取后续流程的截图（匹配中画面、对手信息、断网退出等），为后续阶段做准备。

## 调试方法

不需要在Windows上直接运行Python脚本。通过 Remote Agent 逐步执行：

```bash
# 截图并下载到本地查看
curl -H "X-Auth: $PWD" -X POST "$URL/exec" -d '{"cmd": "powershell -Command \"& ADB -s emulator-5554 exec-out screencap -p > C:\\Users\\Administrator\\Desktop\\screen.png\""}'
curl -H "X-Auth: $PWD" "$URL/download?path=C:\\Users\\Administrator\\Desktop\\screen.png" -o screen.png

# 点击特定坐标
curl -H "X-Auth: $PWD" -X POST "$URL/exec" -d '{"cmd": "powershell -Command \"& ADB -s emulator-5554 shell input tap 640 360\""}'

# 运行Python脚本
curl -H "X-Auth: $PWD" -X POST "$URL/exec" -d '{"cmd": "python C:\\path\\to\\script.py"}'
```

也可以写临时Python脚本推到Windows执行，一步步测试每个模块。

## 注意事项

- Remote Agent 的命令白名单包含: adb(需通过powershell调用带路径的adb), python, pip, git, powershell 等
- ADB需要先 `adb root` 才能访问游戏数据目录
- hosts屏蔽用的 bind-mount，模拟器重启后失效，需重新执行
- 弹窗清理可能需要多次迭代调参（阈值、等待时间、最大循环次数）
- 每次调整后截图验证，确保改对了再进下一步
