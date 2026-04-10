"""扫描游戏堆内存中的活动页 URL"""
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

// 搜集大的 rw 内存段 (堆)
var rwSegs = [];
var fp = fopen(Memory.allocUtf8String('/proc/self/maps'), Memory.allocUtf8String('r'));
var buf = Memory.alloc(1024);
while (!fgets(buf, 1024, fp).isNull()) {
    var line = buf.readUtf8String();
    if (line.indexOf('rw-p') !== -1) {
        var addrPart = line.split(' ')[0];
        var parts = addrPart.split('-');
        try {
            var start = ptr('0x' + parts[0]);
            var end = ptr('0x' + parts[1]);
            var size = end.sub(start).toInt32();
            if (size > 0x100000 && size < 0x8000000) {
                rwSegs.push({start: start, size: size});
            }
        } catch(e) {}
    }
}
fclose(fp);
send('rw segments: ' + rwSegs.length);

// 在堆上搜 "http://" 和 "https://" 开头的 URL
var found = [];
for (var i = 0; i < rwSegs.length && i < 30; i++) {
    try {
        // 搜索 http://
        var matches = Memory.scanSync(rwSegs[i].start, rwSegs[i].size, '68 74 74 70 3a 2f 2f');
        for (var j = 0; j < matches.length; j++) {
            try {
                var url = matches[j].address.readUtf8String(300);
                if (url && url.length > 15) {
                    var u = url.split('\x00')[0]; // null terminated
                    if (u.length > 15 && u.length < 500) {
                        var lower = u.toLowerCase();
                        if (lower.indexOf('qq.com') !== -1 || lower.indexOf('tencent') !== -1 ||
                            lower.indexOf('pubg') !== -1 || lower.indexOf('activity') !== -1 ||
                            lower.indexOf('event') !== -1 || lower.indexOf('cgug') !== -1 ||
                            lower.indexOf('pixui') !== -1 || lower.indexOf('announce') !== -1) {
                            if (found.indexOf(u) === -1) {
                                found.push(u);
                                send('[URL] ' + u.substring(0, 250));
                            }
                        }
                    }
                }
            } catch(e) {}
        }
        // 搜索 https://
        matches = Memory.scanSync(rwSegs[i].start, rwSegs[i].size, '68 74 74 70 73 3a 2f 2f');
        for (var j = 0; j < matches.length; j++) {
            try {
                var url = matches[j].address.readUtf8String(300);
                if (url && url.length > 15) {
                    var u = url.split('\x00')[0];
                    if (u.length > 15 && u.length < 500) {
                        var lower = u.toLowerCase();
                        if (lower.indexOf('qq.com') !== -1 || lower.indexOf('tencent') !== -1 ||
                            lower.indexOf('pubg') !== -1 || lower.indexOf('activity') !== -1 ||
                            lower.indexOf('event') !== -1 || lower.indexOf('cgug') !== -1 ||
                            lower.indexOf('pixui') !== -1 || lower.indexOf('announce') !== -1) {
                            if (found.indexOf(u) === -1) {
                                found.push(u);
                                send('[URL] ' + u.substring(0, 250));
                            }
                        }
                    }
                }
            } catch(e) {}
        }
    } catch(e) {}
}
send('Total unique URLs: ' + found.length);
"""

sc = s.create_script(code, runtime='v8')
sc.on('message', lambda msg, data: print(msg.get('payload', msg.get('description', ''))[:300], flush=True))
sc.load()
time.sleep(20)
print("Done", flush=True)
