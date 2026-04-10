"""用 GUObjectArray 的 chunked 结构搜索 GObjects"""
import frida, time, subprocess

ADB = r"D:\leidian\LDPlayer9\adb.exe"
r = subprocess.run([ADB, "-s", "emulator-5556", "shell", "pidof com.tencent.tmgp.pubgmhd"],
                   capture_output=True, text=True)
pid = int(r.stdout.strip().split()[0])
print(f"PID: {pid}", flush=True)

d = frida.get_usb_device(5)
s = d.attach(pid)

# UE4 4.18 GUObjectArray 用 chunked 数组:
# struct FChunkedFixedUObjectArray {
#     FUObjectItem** Objects;    // 指针数组（每个指向一个 chunk）
#     FUObjectItem* PreAllocatedObjects;
#     int MaxElements;
#     int NumElements;
#     int MaxChunks;
#     int NumChunks;
# };
# 每个 chunk 包含 65536 / sizeof(FUObjectItem) 个元素
# FUObjectItem = { UObject* Object; int32 Flags; int32 ClusterRootIndex; int32 SerialNumber; }
# = 24 bytes on 64-bit

code = r"""
var fopen = new NativeFunction(Module.findExportByName('libc.so','fopen'),'pointer',['pointer','pointer']);
var fgets = new NativeFunction(Module.findExportByName('libc.so','fgets'),'pointer',['pointer','int','pointer']);
var fclose = new NativeFunction(Module.findExportByName('libc.so','fclose'),'int',['pointer']);

function getArmBase(name) {
    var fp = fopen(Memory.allocUtf8String('/proc/self/maps'), Memory.allocUtf8String('r'));
    var buf = Memory.alloc(512); var result = null;
    while (!fgets(buf, 512, fp).isNull()) {
        var line = buf.readUtf8String();
        if (line.indexOf(name) !== -1 && line.indexOf('r--p 00000000') !== -1) {
            result = ptr('0x' + line.split('-')[0].trim()); break;
        }
    }
    fclose(fp); return result;
}

function getUE4RWSegs() {
    var segs = [];
    var fp = fopen(Memory.allocUtf8String('/proc/self/maps'), Memory.allocUtf8String('r'));
    var buf = Memory.alloc(512);
    while (!fgets(buf, 512, fp).isNull()) {
        var line = buf.readUtf8String();
        if (line.indexOf('libUE4.so') !== -1 && line.indexOf('rw-p') !== -1) {
            var parts = line.split(' ')[0].split('-');
            var start = ptr('0x' + parts[0]);
            var end = ptr('0x' + parts[1]);
            segs.push({start: start, size: end.sub(start).toInt32()});
        }
    }
    fclose(fp); return segs;
}

var ue4base = getArmBase('libUE4.so');
send('UE4 base: ' + ue4base);

var rwSegs = getUE4RWSegs();
send('UE4 rw segs: ' + rwSegs.length);

// 搜索 chunked GObjects:
// 特征: {ptr Objects, ptr PreAlloc, int32 MaxElements, int32 NumElements, int32 MaxChunks, int32 NumChunks}
// MaxElements > 100000, NumElements > 50000, NumChunks > 1, MaxChunks >= NumChunks
send('\n=== 搜索 chunked GObjects ===');
var candidates = [];

for (var si = 0; si < rwSegs.length; si++) {
    var seg = rwSegs[si];
    for (var off = 0; off < seg.size - 48; off += 8) {
        try {
            var addr = seg.start.add(off);
            var p1 = addr.readPointer();          // Objects (ptr to chunk ptrs)
            var p2 = addr.add(8).readPointer();   // PreAllocatedObjects
            var maxElem = addr.add(16).readS32();
            var numElem = addr.add(20).readS32();
            var maxChunks = addr.add(24).readS32();
            var numChunks = addr.add(28).readS32();

            if (numElem > 30000 && numElem < 500000 &&
                maxElem >= numElem && maxElem < 2000000 &&
                numChunks > 0 && numChunks < 100 &&
                maxChunks >= numChunks && maxChunks < 200 &&
                !p1.isNull()) {
                candidates.push({
                    addr: addr, objects: p1, prealloc: p2,
                    maxElem: maxElem, numElem: numElem,
                    maxChunks: maxChunks, numChunks: numChunks,
                    offset: '0x' + addr.sub(ue4base).toString(16)
                });
            }
        } catch(e) {}
    }
}

send('Chunked GObjects 候选: ' + candidates.length);
candidates.forEach(function(c) {
    send('  @ ' + c.addr + ' (offset ' + c.offset + ')');
    send('    Objects=' + c.objects + ' PreAlloc=' + c.prealloc);
    send('    max=' + c.maxElem + ' num=' + c.numElem + ' chunks=' + c.numChunks + '/' + c.maxChunks);

    // 验证: 读取第一个 chunk 的第一个 UObject
    try {
        var chunk0 = c.objects.readPointer(); // chunk[0] 指针
        send('    chunk[0] @ ' + chunk0);
        if (!chunk0.isNull()) {
            // 尝试不同的 FUObjectItem stride: 16, 24, 32
            for (var stride = 16; stride <= 32; stride += 8) {
                var obj0 = chunk0.readPointer();
                if (!obj0.isNull()) {
                    var vtable = obj0.readPointer();
                    // vtable 应该在 libUE4.so 范围内
                    if (vtable.compare(ue4base) > 0 && vtable.compare(ue4base.add(0x14100000)) < 0) {
                        var nameIdx = obj0.add(24).readU32(); // FName.ComparisonIndex
                        send('    stride=' + stride + ': obj[0]=' + obj0 + ' vtable=' + vtable + ' nameIdx=' + nameIdx + ' ✓');
                    }
                }
            }
        }
    } catch(e) {
        send('    验证失败: ' + e);
    }
});

// === 也尝试搜索非 chunked 的简单数组 (不同 field 布局) ===
send('\n=== 搜索简单数组 GObjects (宽松条件) ===');
var simple = [];
for (var si = 0; si < rwSegs.length; si++) {
    var seg = rwSegs[si];
    for (var off = 0; off < seg.size - 32; off += 4) {
        try {
            var addr = seg.start.add(off);
            // 尝试不同布局: 先 int32 再 pointer
            var n1 = addr.readS32();
            var n2 = addr.add(4).readS32();
            var p = addr.add(8).readPointer();

            if (n1 > 50000 && n1 < 500000 && n2 >= n1 && n2 < 1000000 && !p.isNull()) {
                try {
                    var test = p.readPointer();
                    if (!test.isNull()) {
                        simple.push({addr: addr, num: n1, max: n2, objects: p,
                            offset: '0x' + addr.sub(ue4base).toString(16)});
                    }
                } catch(e2) {}
            }
        } catch(e) {}
    }
}
send('简单 GObjects 候选: ' + simple.length);
simple.slice(0, 5).forEach(function(c) {
    send('  @ ' + c.addr + ' (offset ' + c.offset + ') num=' + c.num + ' max=' + c.max + ' objects=' + c.objects);
});

send('\n=== 完成 ===');
"""

sc = s.create_script(code, runtime='v8')
sc.on('message', lambda msg, data: print(msg.get('payload', msg.get('description', ''))[:300], flush=True))
sc.load()
time.sleep(30)
s.detach()
print("Done", flush=True)
