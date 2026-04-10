"""测试在 x86 模拟器上通过地址直接 hook ARM 库"""
import frida, sys, time

print("=== ARM Hook Test ===", flush=True)

device = frida.get_usb_device(timeout=5)

# Find main game process
import subprocess
# Get PID from adb directly since Frida enumeration misses the main process
r = subprocess.run([r"D:\leidian\LDPlayer9\adb.exe", "-s", "emulator-5556", "shell",
    "pidof com.tencent.tmgp.pubgmhd"], capture_output=True, text=True)
pids = r.stdout.strip().split()
target_pid = int(pids[0]) if pids else None

if not target_pid:
    print("Game not running!", flush=True)
    sys.exit(1)

print(f"Attaching to PID {target_pid}", flush=True)
session = device.attach(target_pid)

test_code = """
// 测试1: 通过 /proc/self/maps 解析 ARM 库基址
function getArmModuleBase(libName) {
    var maps = null;
    try {
        // 直接读 /proc/self/maps 文件
        var fd = new File('/proc/self/maps', 'r');
        maps = fd.read();
        fd.close();
    } catch(e) {
        console.log('无法读取 maps: ' + e);
        return null;
    }

    var lines = maps.split('\\n');
    for (var i = 0; i < lines.length; i++) {
        if (lines[i].indexOf(libName) !== -1 && lines[i].indexOf('r--p 00000000') !== -1) {
            var addr = lines[i].split('-')[0];
            return ptr('0x' + addr);
        }
    }
    return null;
}

// 测试2: 检测 ARM 库
var libs = ['libUE4.so', 'libtersafe.so', 'libPxKit3.so', 'libGPixUI.so', 'libTPCore-master.so'];
libs.forEach(function(lib) {
    var base = getArmModuleBase(lib);
    if (base) {
        console.log('找到 ' + lib + ' @ ' + base);
        // 尝试读取 ELF magic
        try {
            var magic = Memory.readByteArray(base, 4);
            var bytes = new Uint8Array(magic);
            var isElf = (bytes[0] === 0x7f && bytes[1] === 0x45 && bytes[2] === 0x4c && bytes[3] === 0x46);
            console.log('  ELF magic: ' + (isElf ? 'YES ✓' : 'NO ✗'));
        } catch(e) {
            console.log('  无法读取内存: ' + e);
        }
    } else {
        console.log('未找到 ' + lib);
    }
});

// 测试3: 尝试 hook libtersafe.so 的 TssSDKInit (偏移 0x001cc280)
var tersafeBase = getArmModuleBase('libtersafe.so');
if (tersafeBase) {
    var tssInitOffset = 0x001cc280;
    var tssInitAddr = tersafeBase.add(tssInitOffset);
    console.log('\\nTssSDKInit 地址: ' + tssInitAddr);

    try {
        Interceptor.attach(tssInitAddr, {
            onEnter: function(args) {
                console.log('[TSS] TssSDKInit 被调用!');
            }
        });
        console.log('TssSDKInit hook 安装成功 ✓');
    } catch(e) {
        console.log('TssSDKInit hook 失败: ' + e);
    }
}

// 测试4: 尝试 hook PixUI 的 pxIWindowLoadFromUrl (偏移 0x000f2ac8)
var pxkitBase = getArmModuleBase('libPxKit3.so');
if (pxkitBase) {
    var loadUrlOffset = 0x000f2ac8;
    var loadUrlAddr = pxkitBase.add(loadUrlOffset);
    console.log('\\npxIWindowLoadFromUrl 地址: ' + loadUrlAddr);

    try {
        Interceptor.attach(loadUrlAddr, {
            onEnter: function(args) {
                console.log('[PixUI] pxIWindowLoadFromUrl 被调用!');
                // Try to read args
                for (var i = 0; i < 4; i++) {
                    try {
                        var s = args[i].readUtf8String();
                        if (s && s.length > 0 && s.length < 500) {
                            console.log('  arg' + i + ': ' + s.substring(0, 200));
                        }
                    } catch(e) {}
                }
            }
        });
        console.log('pxIWindowLoadFromUrl hook 安装成功 ✓');
    } catch(e) {
        console.log('pxIWindowLoadFromUrl hook 失败: ' + e);
    }
}

// 测试5: 检查 Frida 的 Module.findBaseAddress 和 Process.findModuleByAddress
console.log('\\n=== Frida API 测试 ===');
if (tersafeBase) {
    var mod = Process.findModuleByAddress(tersafeBase);
    console.log('findModuleByAddress(tersafe): ' + (mod ? mod.name : 'null'));
}
console.log('findModuleByName(libtersafe.so): ' + Process.findModuleByName('libtersafe.so'));
console.log('findModuleByName(libUE4.so): ' + Process.findModuleByName('libUE4.so'));

console.log('\\n=== 测试完成 ===');
"""

script = session.create_script(test_code)
script.on('message', lambda msg, data: print(f"  {msg.get('payload', msg.get('description', ''))}", flush=True))
script.load()
time.sleep(5)
print("Done", flush=True)
