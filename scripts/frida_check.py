import frida
import sys
import time

print("=== Frida Module Checker ===", flush=True)

device = frida.get_usb_device(timeout=5)
print(f"Device: {device.name}", flush=True)

# Attach to existing process
pid = 3391
try:
    session = device.attach(pid)
    print(f"Attached to PID {pid}", flush=True)
except Exception as e:
    print(f"ERROR: {e}", flush=True)
    # Try to find by name
    for p in device.enumerate_processes():
        if 'pubgmhd' in p.name:
            print(f"Found: {p.name} PID={p.pid}", flush=True)
            session = device.attach(p.pid)
            break

script = session.create_script("""
    var modules = Process.enumerateModules();
    var interesting = ['libUE4', 'libtersafe', 'libTPCore', 'libPxKit3', 'libGPixUI', 'libsaf', 'libentryexpro', 'libckguard'];
    console.log('Total modules: ' + modules.length);
    modules.forEach(function(m) {
        for (var i = 0; i < interesting.length; i++) {
            if (m.name.indexOf(interesting[i]) !== -1) {
                console.log('  FOUND: ' + m.name + ' @ ' + m.base + ' size=' + m.size);
            }
        }
    });
    // Also list all modules with size > 1MB
    console.log('\\nLarge modules (>1MB):');
    modules.forEach(function(m) {
        if (m.size > 1024*1024) {
            console.log('  ' + m.name + ' @ ' + m.base + ' size=' + (m.size/1024/1024).toFixed(1) + 'MB');
        }
    });
""")

script.on('message', lambda msg, data: print(f"[{msg['type']}] {msg.get('payload', msg.get('description', ''))}"))

script.load()
time.sleep(3)
print("Done", flush=True)
