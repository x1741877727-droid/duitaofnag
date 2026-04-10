"""GG式内存对比: dump前/后对比找变化的地址"""
import frida, time, subprocess, sys, os

ADB = r"D:\leidian\LDPlayer9\adb.exe"
DUMP_DIR = r"C:\Users\Administrator\Desktop\memdump"

r = subprocess.run([ADB, "-s", "emulator-5556", "shell", "pidof com.tencent.tmgp.pubgmhd"],
                   capture_output=True, text=True)
pid = int(r.stdout.strip().split()[0])
print(f"PID: {pid}", flush=True)

mode = sys.argv[1] if len(sys.argv) > 1 else "dump1"
print(f"Mode: {mode}", flush=True)

os.makedirs(DUMP_DIR, exist_ok=True)

d = frida.get_usb_device(5)
s = d.attach(pid)

if mode == "dump1" or mode == "dump2":
    # Dump 关键内存区域
    code = r"""
    var fopen = new NativeFunction(Module.findExportByName('libc.so','fopen'),'pointer',['pointer','pointer']);
    var fgets = new NativeFunction(Module.findExportByName('libc.so','fgets'),'pointer',['pointer','int','pointer']);
    var fclose = new NativeFunction(Module.findExportByName('libc.so','fclose'),'int',['pointer']);

    // 找 libUE4.so 的 rw 段 (活动配置最可能在这里)
    var segs = [];
    var fp = fopen(Memory.allocUtf8String('/proc/self/maps'), Memory.allocUtf8String('r'));
    var buf = Memory.alloc(512);
    while (!fgets(buf, 512, fp).isNull()) {
        var line = buf.readUtf8String();
        if (line.indexOf('rw-p') !== -1) {
            var parts = line.split(' ')[0].split('-');
            try {
                var start = ptr('0x' + parts[0]);
                var end = ptr('0x' + parts[1]);
                var size = end.sub(start).toInt32();
                // UE4 rw 段 或 大的匿名堆段
                if (line.indexOf('libUE4.so') !== -1 && size > 0x1000) {
                    segs.push({start: start, size: size, name: 'ue4_rw'});
                } else if (size > 0x100000 && size < 0x4000000 && line.indexOf('[') === -1) {
                    segs.push({start: start, size: size, name: 'heap'});
                }
            } catch(e) {}
        }
    }
    fclose(fp);

    send({type: 'info', msg: 'Segments: ' + segs.length});

    // Dump 每个段的数据
    var totalSize = 0;
    segs.forEach(function(seg, idx) {
        try {
            var data = Memory.readByteArray(seg.start, seg.size);
            send({type: 'dump', idx: idx, start: seg.start.toString(), size: seg.size, name: seg.name}, data);
            totalSize += seg.size;
        } catch(e) {
            send({type: 'info', msg: 'skip seg ' + idx + ': ' + e});
        }
    });

    send({type: 'info', msg: 'Total dumped: ' + (totalSize/1024/1024).toFixed(1) + 'MB in ' + segs.length + ' segments'});
    send({type: 'done'});
    """

    sc = s.create_script(code, runtime='v8')

    dump_file = os.path.join(DUMP_DIR, f"{mode}.bin")
    meta_file = os.path.join(DUMP_DIR, f"{mode}_meta.txt")
    dump_f = open(dump_file, 'wb')
    meta_f = open(meta_file, 'w')
    offset = 0

    def on_msg(msg, data):
        global offset
        if msg['type'] == 'send':
            p = msg['payload']
            if p.get('type') == 'dump' and data:
                dump_f.write(data)
                meta_f.write(f"{p['idx']}\t{p['start']}\t{p['size']}\t{p['name']}\t{offset}\n")
                offset += p['size']
                print(f"  seg {p['idx']}: {p['start']} size={p['size']} ({p['name']})", flush=True)
            elif p.get('type') == 'info':
                print(p['msg'], flush=True)
            elif p.get('type') == 'done':
                print("Dump complete", flush=True)

    sc.on('message', on_msg)
    sc.load()
    time.sleep(30)
    dump_f.close()
    meta_f.close()
    s.detach()
    print(f"Saved to {dump_file} ({offset} bytes)", flush=True)

elif mode == "diff":
    s.detach()
    # 对比 dump1 和 dump2
    d1 = open(os.path.join(DUMP_DIR, "dump1.bin"), 'rb').read()
    d2 = open(os.path.join(DUMP_DIR, "dump2.bin"), 'rb').read()
    meta = open(os.path.join(DUMP_DIR, "dump1_meta.txt")).readlines()

    if len(d1) != len(d2):
        print(f"WARNING: size mismatch d1={len(d1)} d2={len(d2)}", flush=True)

    min_len = min(len(d1), len(d2))
    changes = []
    for i in range(min_len):
        if d1[i] != d2[i]:
            changes.append((i, d1[i], d2[i]))

    print(f"Total changed bytes: {len(changes)}", flush=True)

    # 找出 0->1 的变化 (checkbox 勾选)
    zero_to_one = [(i, d1[i], d2[i]) for i, a, b in changes if a == 0 and b == 1]
    print(f"0->1 changes: {len(zero_to_one)}", flush=True)

    # 解析 meta 找到实际地址
    seg_info = []
    for line in meta:
        parts = line.strip().split('\t')
        seg_info.append({
            'idx': int(parts[0]),
            'start': parts[1],
            'size': int(parts[2]),
            'name': parts[3],
            'file_offset': int(parts[4])
        })

    def file_offset_to_addr(foff):
        for seg in seg_info:
            if seg['file_offset'] <= foff < seg['file_offset'] + seg['size']:
                offset_in_seg = foff - seg['file_offset']
                return f"0x{int(seg['start'], 16) + offset_in_seg:x}", seg['name']
        return "unknown", "unknown"

    print("\n=== 0->1 变化 (最可能是 checkbox) ===")
    for foff, old, new in zero_to_one[:50]:
        addr, name = file_offset_to_addr(foff)
        print(f"  {addr} [{name}] : {old} -> {new}")

    print("\n=== 所有变化 (前100) ===")
    for foff, old, new in changes[:100]:
        addr, name = file_offset_to_addr(foff)
        print(f"  {addr} [{name}] : {old} -> {new}")

print("Done", flush=True)
