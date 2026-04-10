import frida
import sys
import time
import threading

print("=== Frida Long Monitor ===", flush=True)

device = frida.get_usb_device(timeout=5)
print(f"Device: {device.name}", flush=True)

# Find game process
target_pid = None
for p in device.enumerate_processes():
    if 'pubgmhd' in p.name and 'xg_vip' not in p.name and 'tgaPlugin' not in p.name:
        target_pid = p.pid
        print(f"Found: {p.name} PID={p.pid}", flush=True)
        break

if not target_pid:
    print("Game not running, spawning...", flush=True)
    target_pid = device.spawn(["com.tencent.tmgp.pubgmhd"])
    print(f"Spawned PID: {target_pid}", flush=True)
    session = device.attach(target_pid)

    # Load the full hook script
    with open(r"C:\Users\Administrator\Desktop\full_hook.js", "r", encoding="utf-8") as f:
        script_code = f.read()

    script = session.create_script(script_code)
    script.on('message', lambda msg, data: print(f"[hook] {msg.get('payload', msg.get('description', ''))}", flush=True))
    script.load()
    print("Full hook loaded", flush=True)
    device.resume(target_pid)
    print("Game resumed", flush=True)
else:
    session = device.attach(target_pid)
    print(f"Attached to existing PID {target_pid}", flush=True)

# Monitor script: check for key modules every 5 seconds
monitor_code = """
var checked = {};
var TARGET_LIBS = ['libUE4.so', 'libtersafe.so', 'libTPCore-master.so', 'libPxKit3.so', 'libGPixUI.so'];

function checkModules() {
    var modules = Process.enumerateModules();
    var found = [];
    modules.forEach(function(m) {
        TARGET_LIBS.forEach(function(lib) {
            if (m.name === lib && !checked[lib]) {
                checked[lib] = true;
                found.push(m.name + ' @ ' + m.base + ' size=' + (m.size/1024/1024).toFixed(1) + 'MB');
            }
        });
    });
    if (found.length > 0) {
        send({type: 'module_loaded', modules: found, total: modules.length});
    }
    send({type: 'heartbeat', total_modules: modules.length, checked_count: Object.keys(checked).length});
}

// Check immediately and then every 5 seconds
checkModules();
setInterval(checkModules, 5000);
"""

monitor = session.create_script(monitor_code)

results = []
def on_monitor_msg(msg, data):
    if msg['type'] == 'send':
        payload = msg['payload']
        if payload.get('type') == 'module_loaded':
            for m in payload['modules']:
                print(f"[LOADED] {m}", flush=True)
                results.append(m)
        elif payload.get('type') == 'heartbeat':
            total = payload['total_modules']
            checked = payload['checked_count']
            print(f"[heartbeat] modules={total}, game_libs_found={checked}/5", flush=True)

monitor.on('message', on_monitor_msg)
monitor.load()
print("Monitor started, checking every 5 seconds for 180 seconds...", flush=True)

for i in range(36):  # 36 * 5 = 180 seconds
    time.sleep(5)

print(f"\n=== Monitor complete ===", flush=True)
print(f"Game libraries found: {len(results)}", flush=True)
for r in results:
    print(f"  {r}", flush=True)
