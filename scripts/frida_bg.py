"""后台 Frida 监控 v2 — spawn 模式，输出到日志"""
import frida, sys, time, subprocess

LOG = r"C:\Users\Administrator\Desktop\frida_output.log"
ADB = r"D:\leidian\LDPlayer9\adb.exe"
SERIAL = "emulator-5556"

def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

open(LOG, "w").close()
log("=== Frida v2 启动 ===")

device = frida.get_usb_device(timeout=5)
log(f"Device: {device.name}")

# Kill existing
subprocess.run([ADB, "-s", SERIAL, "shell", "am force-stop com.tencent.tmgp.pubgmhd"],
               capture_output=True, timeout=10)
time.sleep(2)

# Spawn
pid = device.spawn(["com.tencent.tmgp.pubgmhd"])
log(f"Spawned PID: {pid}")
session = device.attach(pid)

# Load hook
with open(r"C:\Users\Administrator\Desktop\full_hook.js", "r", encoding="utf-8") as f:
    code = f.read()

script = session.create_script(code, runtime='v8')

def on_msg(msg, data):
    t = msg.get('type', '')
    if t == 'send':
        log(f"[send] {msg.get('payload', '')}")
    elif t == 'error':
        log(f"[ERR] {msg.get('description', '')} {msg.get('stack', '')[:200]}")
    elif t == 'log':
        log(f"[log] {msg.get('payload', '')}")
    else:
        log(f"[{t}] {msg}")

script.on('message', on_msg)
script.load()
log("Hook loaded")

device.resume(pid)
log("Game resumed, monitoring 300 seconds...")

# 持续运行直到游戏退出
log("持续监控中 (按 Ctrl+C 退出)...")
time.sleep(30)  # 等游戏充分启动
try:
    while True:
        time.sleep(15)
        r = subprocess.run([ADB, "-s", SERIAL, "shell", f"ps -p {pid} -o pid="],
                          capture_output=True, text=True, timeout=5)
        if str(pid) not in r.stdout:
            log("游戏进程已退出")
            break
except KeyboardInterrupt:
    log("用户中断")

# 优雅 detach，不杀进程
try:
    session.detach()
    log("已 detach (游戏继续运行)")
except:
    pass

log("=== DONE ===")
