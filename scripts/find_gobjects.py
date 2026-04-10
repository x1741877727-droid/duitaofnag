"""找到 UE4 GNames 和 GObjects 表"""
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

// 搜集 libUE4.so 的所有 rw 段 (数据段, GObjects/GNames 在这里)
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
    fclose(fp);
    return segs;
}

var ue4base = getArmBase('libUE4.so');
send('UE4 base: ' + ue4base);

var rwSegs = getUE4RWSegs();
send('UE4 rw segments: ' + rwSegs.length);
var totalRW = 0;
rwSegs.forEach(function(s) { totalRW += s.size; });
send('UE4 rw total: ' + (totalRW/1024/1024).toFixed(1) + 'MB');

// === 方法1: 搜索 GNames ===
// UE4 4.18 GNames 是 FNamePool, 里面有大量 FNameEntry
// FNameEntry 格式: [uint16 header][char[] name]
// 搜索已知的引擎内置名称 "None" (index 0), "ByteProperty" (index 1) 等
// GNames 表本身是一个指针数组, 每个元素指向 FNameEntry

// 在 UE4 的 rw 段中搜索 "None" 字符串 (作为 FNameEntry)
// FNameEntry for "None": header(2 bytes) + "None\0"
// header 的 len 字段 = 4 (name length)
var nonePattern = '4e 6f 6e 65 00'; // "None\0"
var noneAddrs = [];

// 搜索所有堆段 (GNames entries 在堆上)
var allRW = [];
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
            if (size > 0x1000 && size < 0x10000000) {
                allRW.push({start: start, size: size});
            }
        } catch(e) {}
    }
}
fclose(fp2);
send('Total rw segments: ' + allRW.length);

// 在 UE4 rw 段中搜索 GObjects 的特征
// GObjects (FUObjectArray) 结构:
//   ObjObjects: TUObjectArray (指向 FUObjectItem* 数组的指针)
//   ObjObjects 内部: FUObjectItem** Objects (指针数组), int MaxElements, int NumElements
// FUObjectItem: UObject* Object (8 bytes) + int Flags (4 bytes) + ...
// 特征: NumElements 是一个大数 (>10000), MaxElements >= NumElements
// Objects 指针指向一大块连续内存

// 在 UE4 的 rw 数据段中搜索 "看起来像 GObjects" 的结构
// 遍历 UE4 rw 段的每 8 字节, 检查是否是 {pointer, int, int} 且 int 值合理
send('\n=== 搜索 GObjects ===');
var candidates = [];

for (var si = 0; si < rwSegs.length; si++) {
    var seg = rwSegs[si];
    for (var off = 0; off < seg.size - 24; off += 8) {
        try {
            var p = seg.start.add(off).readPointer();
            var maxElem = seg.start.add(off + 8).readS32();
            var numElem = seg.start.add(off + 12).readS32();

            // GObjects 特征: numElem > 50000, maxElem >= numElem, 指针非空且对齐
            if (numElem > 50000 && numElem < 500000 &&
                maxElem >= numElem && maxElem < 1000000 &&
                !p.isNull() && p.toInt32() % 8 === 0) {

                // 验证: Objects[0] 应该指向一个有效的 UObject
                try {
                    var firstItem = p.readPointer(); // FUObjectItem[0].Object
                    if (!firstItem.isNull()) {
                        candidates.push({
                            addr: seg.start.add(off),
                            objects: p,
                            maxElem: maxElem,
                            numElem: numElem,
                            offset: '0x' + seg.start.add(off).sub(ue4base).toString(16)
                        });
                    }
                } catch(e2) {}
            }
        } catch(e) {}
    }
}

send('GObjects 候选: ' + candidates.length);
candidates.forEach(function(c) {
    send('  @ ' + c.addr + ' (offset ' + c.offset + ')');
    send('    Objects=' + c.objects + ' max=' + c.maxElem + ' num=' + c.numElem);

    // 尝试读取前几个 UObject, 验证是否合法
    try {
        for (var i = 0; i < 3; i++) {
            // FUObjectItem 大小在 UE4 4.18 通常是 16 或 24 bytes
            // 尝试 16 bytes stride
            var objPtr = c.objects.add(i * 16).readPointer();
            if (!objPtr.isNull()) {
                // UObject 结构: vtable(8) + objectFlags(4) + internalIndex(4) + classPtr(8) + nameIndex(4+4) + outerPtr(8)
                var vtable = objPtr.readPointer();
                var internalIdx = objPtr.add(12).readS32();
                send('    [' + i + '] obj=' + objPtr + ' vtable=' + vtable + ' idx=' + internalIdx);
            }
        }
    } catch(e) {
        send('    读取失败: ' + e);
    }
});

send('\n=== 扫描完成 ===');
"""

sc = s.create_script(code, runtime='v8')
sc.on('message', lambda msg, data: print(msg.get('payload', msg.get('description', ''))[:300], flush=True))
sc.load()
time.sleep(20)
s.detach()
print("Done", flush=True)
