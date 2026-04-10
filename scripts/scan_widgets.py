"""扫描 UE4 GNames 和堆内存，找到活动弹窗 Widget 对象"""
import frida, time, subprocess

ADB = r"D:\leidian\LDPlayer9\adb.exe"
r = subprocess.run([ADB, "-s", "emulator-5556", "shell", "pidof com.tencent.tmgp.pubgmhd"],
                   capture_output=True, text=True)
pid = int(r.stdout.strip().split()[0])
print(f"PID: {pid}", flush=True)

d = frida.get_usb_device(5)
s = d.attach(pid)

code = r"""
var fopen = new NativeFunction(Module.findExportByName('libc.so','fopen'),'pointer',['pointer','pointer']);
var fgets = new NativeFunction(Module.findExportByName('libc.so','fgets'),'pointer',['pointer','int','pointer']);
var fclose = new NativeFunction(Module.findExportByName('libc.so','fclose'),'int',['pointer']);

function getArmBase(name) {
    var fp = fopen(Memory.allocUtf8String('/proc/self/maps'), Memory.allocUtf8String('r'));
    var buf = Memory.alloc(512);
    var result = null;
    while (!fgets(buf, 512, fp).isNull()) {
        var line = buf.readUtf8String();
        if (line.indexOf(name) !== -1 && line.indexOf('r--p 00000000') !== -1) {
            result = ptr('0x' + line.split('-')[0].trim());
            break;
        }
    }
    fclose(fp);
    return result;
}

var ue4 = getArmBase('libUE4.so');
send('UE4 base: ' + ue4);

// === 第一步: 在 UE4 数据段搜索 ESlateVisibility / bIsVisible 相关模式 ===
// UE4 中 Widget 的可见性由 ESlateVisibility 枚举控制:
// Visible=0, Collapsed=1, Hidden=2, HitTestInvisible=3, SelfHitTestInvisible=4

// === 第二步: 搜索堆上的 PixUI window 对象 ===
// 从之前分析, PixUI 通过 pxIWindowLoadFromUrl 加载活动页
// 活动页 URL 包含 "scrm.qq.com" 或 "cjm"
// 搜索堆上引用这些 URL 的对象

// 搜集 rw 段
var rwSegs = [];
var fp2 = fopen(Memory.allocUtf8String('/proc/self/maps'), Memory.allocUtf8String('r'));
var buf2 = Memory.alloc(1024);
while (!fgets(buf2, 1024, fp2).isNull()) {
    var line = buf2.readUtf8String();
    if (line.indexOf('rw-p') !== -1) {
        var parts = line.split(' ')[0].split('-');
        try {
            var start = ptr('0x' + parts[0]);
            var end = ptr('0x' + parts[1]);
            var size = end.sub(start).toInt32();
            if (size > 0x10000 && size < 0x8000000) {
                rwSegs.push({start: start, size: size});
            }
        } catch(e) {}
    }
}
fclose(fp2);
send('rw segments: ' + rwSegs.length);

// === 搜索活动页相关的 PixUI Window 指针 ===
// 搜索 "scrm.qq.com" 字符串在堆上的地址
var scrmPattern = '73 63 72 6d 2e 71 71 2e 63 6f 6d'; // "scrm.qq.com"
var cjmPattern = '63 6a 6d 2e 62 72 6f 6b 65 72'; // "cjm.broker"
var actPattern = '41 63 74 69 76 69 74 79'; // "Activity" (UE4 类名)

var scrmAddrs = [];
var actAddrs = [];

for (var i = 0; i < rwSegs.length && i < 40; i++) {
    try {
        // 搜 scrm.qq.com
        var matches = Memory.scanSync(rwSegs[i].start, rwSegs[i].size, scrmPattern);
        matches.forEach(function(m) { scrmAddrs.push(m.address); });

        // 搜 "Activity" 相关的 Widget 类名
        matches = Memory.scanSync(rwSegs[i].start, rwSegs[i].size, actPattern);
        matches.forEach(function(m) {
            try {
                var ctx = m.address.readUtf8String(80).split('\x00')[0];
                if (ctx.indexOf('Widget') !== -1 || ctx.indexOf('UI') !== -1 ||
                    ctx.indexOf('Panel') !== -1 || ctx.indexOf('Window') !== -1 ||
                    ctx.indexOf('Popup') !== -1 || ctx.indexOf('Show') !== -1) {
                    actAddrs.push({addr: m.address, name: ctx});
                }
            } catch(e) {}
        });
    } catch(e) {}
}

send('scrm.qq.com 在堆上出现 ' + scrmAddrs.length + ' 次');
scrmAddrs.forEach(function(a) {
    send('  scrm @ ' + a);
    // 看看这个字符串前后有没有指针 (可能是对象的成员)
    try {
        // 回溯找对象头: 往前找 8 字节对齐的指针
        for (var off = -256; off < 0; off += 8) {
            try {
                var val = a.add(off).readPointer();
                // 如果这个指针看起来像 vtable (在 libUE4 范围内)
                if (val.compare(ue4) > 0 && val.compare(ue4.add(0x14000000)) < 0) {
                    send('  可能的 vtable @ offset ' + off + ': ' + val);
                }
            } catch(e2) {}
        }
    } catch(e) {}
});

send('\nActivity 相关 Widget 名: ' + actAddrs.length);
actAddrs.slice(0, 30).forEach(function(a) {
    send('  ' + a.addr + ': ' + a.name);
});

// === 搜索 "ShowActivityUI" 和 "ActivityUI" 作为 FName ===
var showActPattern = '53 68 6f 77 41 63 74 69 76 69 74 79'; // "ShowActivity"
var showActAddrs = [];
for (var i = 0; i < rwSegs.length && i < 40; i++) {
    try {
        var matches = Memory.scanSync(rwSegs[i].start, rwSegs[i].size, showActPattern);
        matches.forEach(function(m) {
            try {
                var ctx = m.address.readUtf8String(60).split('\x00')[0];
                showActAddrs.push({addr: m.address, name: ctx});
            } catch(e) {}
        });
    } catch(e) {}
}
send('\nShowActivity 在堆上: ' + showActAddrs.length);
showActAddrs.forEach(function(a) {
    send('  ' + a.addr + ': ' + a.name);
});

// === 搜索 "Visibility" 和 "bIsVisible" ===
var visPattern = '62 49 73 56 69 73 69 62 6c 65'; // "bIsVisible"
var visAddrs = [];
for (var i = 0; i < rwSegs.length && i < 40; i++) {
    try {
        var matches = Memory.scanSync(rwSegs[i].start, rwSegs[i].size, visPattern);
        matches.forEach(function(m) { visAddrs.push(m.address); });
    } catch(e) {}
}
send('\nbIsVisible 在堆上: ' + visAddrs.length);

send('\n=== 扫描完成 ===');
"""

sc = s.create_script(code, runtime='v8')
sc.on('message', lambda msg, data: print(msg.get('payload', msg.get('description', ''))[:300], flush=True))
sc.load()
time.sleep(25)
s.detach()
print("Done", flush=True)
