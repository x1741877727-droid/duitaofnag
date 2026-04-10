import frida
import sys
import time

print("=== Frida Launcher ===", flush=True)

try:
    device = frida.get_usb_device(timeout=5)
    print(f"Device: {device.name}", flush=True)
except Exception as e:
    print(f"ERROR getting device: {e}", flush=True)
    sys.exit(1)

try:
    pid = device.spawn(["com.tencent.tmgp.pubgmhd"])
    print(f"Spawned PID: {pid}", flush=True)
except Exception as e:
    print(f"ERROR spawning: {e}", flush=True)
    sys.exit(1)

try:
    session = device.attach(pid)
    print("Attached to process", flush=True)
except Exception as e:
    print(f"ERROR attaching: {e}", flush=True)
    sys.exit(1)

script_path = r"C:\Users\Administrator\Desktop\full_hook.js"
with open(script_path, "r", encoding="utf-8") as f:
    script_code = f.read()
print(f"Script loaded: {len(script_code)} chars", flush=True)

output_lines = []

script = session.create_script(script_code)

def on_message(message, data):
    if message["type"] == "send":
        line = f"[MSG] {message['payload']}"
    elif message["type"] == "error":
        desc = message.get("description", "")
        stack = message.get("stack", "")[:300]
        line = f"[ERR] {desc}\n{stack}"
    else:
        line = f"[???] {message}"
    output_lines.append(line)
    print(line, flush=True)

script.on("message", on_message)

try:
    script.load()
    print("Script injected successfully!", flush=True)
except Exception as e:
    print(f"ERROR loading script: {e}", flush=True)
    sys.exit(1)

device.resume(pid)
print("Game resumed, monitoring for 60 seconds...", flush=True)

time.sleep(60)

print(f"\n=== Monitoring complete, {len(output_lines)} messages captured ===", flush=True)
