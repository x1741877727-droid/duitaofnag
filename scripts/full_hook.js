// full_hook.js — 完整 Frida Hook 脚本 v2
// 用法: python frida_run.py (通过 Frida Python API spawn)
// 功能: 反作弊 bypass + MSDK 弹窗拦截 + PixUI 弹窗拦截 + UE4 Widget 弹窗拦截
// 环境: LDPlayer 9 (x86_64), Android 9, UE4 4.18
// 注意: ARM 库通过 houdini 翻译, 需要用 /proc/self/maps 获取基址

'use strict';

var CONFIG = {
    logLevel: 2,  // 0=静默, 1=关键, 2=详细
    pixuiBlockKeywords: [
        'activity', 'announce', 'notice', 'popup', 'reward',
        'signin', 'checkin', 'event', 'season', 'gift',
        'lucky', 'draw', 'shop', 'pay', 'recharge',
        'subscribe', 'vip', 'pass', 'festival', 'holiday',
        'banner', 'promo', 'ads', 'splash'
    ],
    pixuiAllowKeywords: [
        'game', 'battle', 'match', 'lobby', 'settings',
        'inventory', 'loadout', 'map', 'team', 'chat',
        'friend', 'clan', 'rank', 'profile', 'store'
    ]
};

function log(level, tag, msg) {
    if (level <= CONFIG.logLevel) {
        send('[' + tag + '] ' + msg);
    }
}

// ============================================================
// 工具: 读取 /proc/self/maps 获取 ARM 库基址
// (Frida 在 x86 模拟器上看不到 houdini 翻译的 ARM 模块)
// ============================================================
var _fopen = new NativeFunction(Module.findExportByName('libc.so', 'fopen'), 'pointer', ['pointer', 'pointer']);
var _fgets = new NativeFunction(Module.findExportByName('libc.so', 'fgets'), 'pointer', ['pointer', 'int', 'pointer']);
var _fclose = new NativeFunction(Module.findExportByName('libc.so', 'fclose'), 'int', ['pointer']);

function getArmModuleBase(libName) {
    var pathStr = Memory.allocUtf8String('/proc/self/maps');
    var modeStr = Memory.allocUtf8String('r');
    var fp = _fopen(pathStr, modeStr);
    if (fp.isNull()) return null;

    var buf = Memory.alloc(512);
    var result = null;
    while (!_fgets(buf, 512, fp).isNull()) {
        var line = buf.readUtf8String();
        if (line.indexOf(libName) !== -1 && line.indexOf('r--p 00000000') !== -1) {
            var addr = line.split('-')[0].trim();
            result = ptr('0x' + addr);
            break;
        }
    }
    _fclose(fp);
    return result;
}

// ============================================================
// 模块1: 反作弊 Bypass (libc层 + TSS SDK)
// ============================================================
function installAntiCheatBypass() {
    log(1, 'AC', '安装反作弊 bypass...');

    // --- 1a. 完全拦截的路径 ---
    var blockPathKeywords = ['frida', 'xposed', 'magisk', 'substrate'];
    // --- 1b. 内容过滤的路径 ---
    var filterPathKeywords = ['/proc/net/tcp', '/proc/net/tcp6',
                              '/proc/self/maps', '/proc/self/status'];
    var filterFds = {};

    // 活动资源文件拦截 — 阻止加载活动 pak/sav 文件
    var activityFileKeywords = [
        '/act/',                  // 活动 pak 目录
        'activitypaks',           // 活动 pak 列表
        'activitytmps',           // 活动临时文件
        'map_act_ui',             // 活动 UI 资源
        'map_time_act_ui',        // 限时活动 UI
        'map_commerce',           // 商城/促销
        'sg_act',                 // 活动存档
        'tmp_act',                // 临时活动 pak
    ];

    function shouldBlockActivityFile(p) {
        if (!p) return false;
        var l = p.toLowerCase();
        for (var i = 0; i < activityFileKeywords.length; i++)
            if (l.indexOf(activityFileKeywords[i]) !== -1) return true;
        return false;
    }

    function shouldBlockPath(p) {
        if (!p) return false;
        var l = p.toLowerCase();
        for (var i = 0; i < blockPathKeywords.length; i++)
            if (l.indexOf(blockPathKeywords[i]) !== -1) return true;
        return false;
    }
    function shouldFilterPath(p) {
        if (!p) return false;
        var l = p.toLowerCase();
        for (var i = 0; i < filterPathKeywords.length; i++)
            if (l.indexOf(filterPathKeywords[i]) !== -1) return true;
        return false;
    }

    // Hook open
    var openPtr = Module.findExportByName('libc.so', 'open');
    if (openPtr) {
        Interceptor.attach(openPtr, {
            onEnter: function(args) {
                this.path = args[0].readCString();
                this.block = shouldBlockPath(this.path);
                this.filter = shouldFilterPath(this.path);
            },
            onLeave: function(retval) {
                if (this.block) {
                    log(2, 'AC', 'BLOCKED open: ' + this.path);
                    retval.replace(ptr(-1));
                } else if (this.filter && retval.toInt32() > 0) {
                    filterFds[retval.toInt32()] = this.path;
                }
            }
        });
    }

    // Hook openat
    var openatPtr = Module.findExportByName('libc.so', 'openat');
    if (openatPtr) {
        Interceptor.attach(openatPtr, {
            onEnter: function(args) {
                this.path = args[1].readCString();
                this.block = shouldBlockPath(this.path);
                this.filter = shouldFilterPath(this.path);
            },
            onLeave: function(retval) {
                if (this.block) {
                    retval.replace(ptr(-1));
                } else if (this.filter && retval.toInt32() > 0) {
                    filterFds[retval.toInt32()] = this.path;
                }
            }
        });
    }

    // Hook read: 过滤敏感 fd 内容
    var readPtr = Module.findExportByName('libc.so', 'read');
    if (readPtr) {
        Interceptor.attach(readPtr, {
            onEnter: function(args) {
                this.fd = args[0].toInt32();
                this.buf = args[1];
            },
            onLeave: function(retval) {
                var n = retval.toInt32();
                if (n > 0 && filterFds[this.fd]) {
                    try {
                        var c = this.buf.readUtf8String(n);
                        if (c) {
                            var m = c.replace(/TracerPid:\s*\d+/g, 'TracerPid:\t0')
                                     .replace(/.*frida.*/gi, '')
                                     .replace(/.*gadget.*/gi, '')
                                     .replace(/.*gum-js.*/gi, '')
                                     .replace(/.*:69D2\s.*/gi, '');
                            if (m !== c) {
                                this.buf.writeUtf8String(m);
                                log(2, 'AC', '过滤 ' + filterFds[this.fd]);
                            }
                        }
                    } catch(e) {}
                }
            }
        });
    }

    // Hook close: 清理 filterFds
    var closePtr = Module.findExportByName('libc.so', 'close');
    if (closePtr) {
        Interceptor.attach(closePtr, {
            onEnter: function(args) {
                var fd = args[0].toInt32();
                if (filterFds[fd]) delete filterFds[fd];
            }
        });
    }

    // Hook ptrace
    var ptracePtr = Module.findExportByName('libc.so', 'ptrace');
    if (ptracePtr) {
        Interceptor.attach(ptracePtr, {
            onEnter: function(args) { this.req = args[0].toInt32(); },
            onLeave: function(retval) {
                if (this.req === 0) retval.replace(ptr(0));
            }
        });
    }

    // Hook strstr: 防止字符串特征检测
    var strstrPtr = Module.findExportByName('libc.so', 'strstr');
    if (strstrPtr) {
        var needles = ['frida', 'FRIDA', 'gum-js', 'gmain', 'linjector',
                       'frida-agent', 'frida-server', 'frida-gadget'];
        Interceptor.attach(strstrPtr, {
            onEnter: function(args) {
                try { this.n = args[1].readCString(); } catch(e) { this.n = null; }
            },
            onLeave: function(retval) {
                if (this.n && !retval.isNull()) {
                    for (var i = 0; i < needles.length; i++) {
                        if (this.n.indexOf(needles[i]) !== -1) {
                            retval.replace(ptr(0));
                            break;
                        }
                    }
                }
            }
        });
    }

    log(1, 'AC', 'libc hooks 已安装');

    // TSS SDK: 不 hook ARM 代码（反作弊会检测代码完整性篡改）
    // 只通过 libc 层间接防护
    log(1, 'AC', 'TSS SDK: 仅 libc 层防护 (不修改 ARM 代码)');
}

// ============================================================
// 模块1b: 网络层拦截 (x86 libc, 安全)
// ============================================================
function installNetworkHook() {
    log(1, 'Net', '安装网络层监控/拦截...');

    // 活动页/弹窗相关的域名关键词（用于 DNS 拦截）
    var blockDomains = [
        // 精准活动域名 (从堆内存扫描发现)
        'cgugccdn.pg.qq.com',           // 活动图标/广告 CDN
        'jsonatm.broker.tplay.qq.com',  // 活动 JSON 数据
        'cjm.broker.tplay.qq.com',      // 活动 CJM 数据
        'scrm.qq.com',                  // 活动页主入口 H5
        'down.pandora.qq.com',          // 活动表情/图片资源
        'h5.igame.qq.com',             // 赛事活动页
        'youxi.gamecenter.qq.com',      // 游戏中心活动
        'announcecdn',                  // 公告 CDN
        'faascjm.native.qq.com',        // 活动 FaaS 接口
    ];

    // Hook getaddrinfo: DNS 解析拦截
    var getaddrinfoPtr = Module.findExportByName('libc.so', 'getaddrinfo');
    if (getaddrinfoPtr) {
        Interceptor.attach(getaddrinfoPtr, {
            onEnter: function(args) {
                try {
                    this.host = args[0].readCString();
                } catch(e) {
                    this.host = null;
                }
                this.shouldBlock = false;
                if (this.host) {
                    var lower = this.host.toLowerCase();
                    for (var i = 0; i < blockDomains.length; i++) {
                        if (lower.indexOf(blockDomains[i]) !== -1) {
                            this.shouldBlock = true;
                            break;
                        }
                    }
                    // 记录所有 DNS 查询用于调试
                    if (this.shouldBlock) {
                        log(1, 'Net', 'BLOCKED DNS: ' + this.host);
                    } else {
                        log(2, 'Net', 'DNS: ' + this.host);
                    }
                }
            },
            onLeave: function(retval) {
                if (this.shouldBlock) {
                    retval.replace(ptr(-1)); // EAI_FAIL
                }
            }
        });
        log(1, 'Net', 'getaddrinfo hook ✓');
    }

    // Hook connect: 监控连接目标 (用于发现活动页 IP)
    var connectPtr = Module.findExportByName('libc.so', 'connect');
    if (connectPtr) {
        Interceptor.attach(connectPtr, {
            onEnter: function(args) {
                var sockaddr = args[1];
                try {
                    var family = sockaddr.readU16();
                    if (family === 2) { // AF_INET
                        var port = (sockaddr.add(2).readU8() << 8) | sockaddr.add(3).readU8();
                        var ip = sockaddr.add(4).readU8() + '.' +
                                 sockaddr.add(5).readU8() + '.' +
                                 sockaddr.add(6).readU8() + '.' +
                                 sockaddr.add(7).readU8();
                        if (port === 80 || port === 443 || port === 8080) {
                            log(2, 'Net', 'HTTP connect: ' + ip + ':' + port);
                        }
                    }
                } catch(e) {}
            }
        });
        log(2, 'Net', 'connect hook ✓');
    }

    log(1, 'Net', '网络层 hooks 已安装');
}

// ============================================================
// 模块2: MSDK 弹窗拦截 (Java层)
// ============================================================
function installMSDKHook() {
    log(1, 'MSDK', '安装 MSDK 弹窗拦截...');
    Java.perform(function() {
        try {
            var PM = Java.use('com.itop.gcloud.msdk.popup.MSDKPopupManager');
            PM.shouldPopup.implementation = function(c, f) {
                log(1, 'MSDK', 'BLOCKED shouldPopup');
                return false;
            };
            PM.show.overload('android.app.Activity',
                'com.itop.gcloud.msdk.popup.config.MSDKPopupConfig',
                'com.itop.gcloud.msdk.popup.IMSDKPopupWindowCallback',
                'boolean').implementation = function(a, b, c, d) {
                log(1, 'MSDK', 'BLOCKED show(single)');
            };
            PM.show.overload('android.app.Activity',
                'java.util.ArrayList',
                'com.itop.gcloud.msdk.popup.IMSDKPopupWindowCallback',
                'boolean').implementation = function(a, b, c, d) {
                log(1, 'MSDK', 'BLOCKED show(list)');
            };
            log(1, 'MSDK', 'MSDK hooks 已安装');
        } catch(e) {
            log(1, 'MSDK', 'MSDK 类未找到: ' + e);
        }
    });
}

// ============================================================
// 模块3: PixUI 弹窗拦截 (ARM native - libPxKit3.so)
// ============================================================
function installPixUIHook() {
    log(1, 'PixUI', '安装 PixUI 弹窗拦截...');

    function hookPixUI() {
        var base = getArmModuleBase('libPxKit3.so');
        if (!base) return false;

        log(1, 'PixUI', 'libPxKit3.so @ ' + base);

        // PixUI 函数偏移 (从 pyelftools 分析得到)
        var offsets = {
            'pxIContainerCreateWindow':  0x000f2c44,
            'pxIWindowLoadFromUrl':      0x000f2ac8,
            'pxIWindowLoadFromData':     0x000f2ad4,
            'pxIWindowClose':            0x000f2abc,
            'pxCreateContainer':         0x0011b8e0,
        };

        // Hook pxIWindowLoadFromUrl — 核心拦截点
        try {
            Interceptor.attach(base.add(offsets['pxIWindowLoadFromUrl']), {
                onEnter: function(args) {
                    this.url = null;
                    this.shouldBlock = false;
                    // 尝试从参数中读取 URL
                    for (var i = 1; i <= 3; i++) {
                        try {
                            var s = args[i].readUtf8String();
                            if (s && s.length > 3 && s.length < 2048) {
                                this.url = s;
                                break;
                            }
                        } catch(e) {}
                    }
                    if (this.url) {
                        var lower = this.url.toLowerCase();
                        var whitelisted = false;
                        for (var w = 0; w < CONFIG.pixuiAllowKeywords.length; w++) {
                            if (lower.indexOf(CONFIG.pixuiAllowKeywords[w]) !== -1) {
                                whitelisted = true; break;
                            }
                        }
                        if (!whitelisted) {
                            for (var b = 0; b < CONFIG.pixuiBlockKeywords.length; b++) {
                                if (lower.indexOf(CONFIG.pixuiBlockKeywords[b]) !== -1) {
                                    this.shouldBlock = true; break;
                                }
                            }
                        }
                        if (this.shouldBlock) {
                            log(1, 'PixUI', 'BLOCKED: ' + this.url);
                        } else {
                            log(2, 'PixUI', 'ALLOW: ' + this.url);
                        }
                    } else {
                        log(2, 'PixUI', 'LoadFromUrl (无法解析URL)');
                    }
                },
                onLeave: function(retval) {
                    if (this.shouldBlock) retval.replace(ptr(-1));
                }
            });
            log(1, 'PixUI', 'pxIWindowLoadFromUrl hook ✓');
        } catch(e) {
            log(1, 'PixUI', 'LoadFromUrl hook 失败: ' + e);
        }

        // Hook pxIContainerCreateWindow
        try {
            Interceptor.attach(base.add(offsets['pxIContainerCreateWindow']), {
                onEnter: function(args) {
                    log(2, 'PixUI', 'CreateWindow 被调用');
                }
            });
            log(2, 'PixUI', 'CreateWindow hook ✓');
        } catch(e) {}

        // Hook pxIWindowClose
        try {
            Interceptor.attach(base.add(offsets['pxIWindowClose']), {
                onEnter: function(args) {
                    log(2, 'PixUI', 'WindowClose 被调用');
                }
            });
        } catch(e) {}

        log(1, 'PixUI', 'PixUI hooks 已安装');
        return true;
    }

    if (!hookPixUI()) {
        var t = setInterval(function() { if (hookPixUI()) clearInterval(t); }, 2000);
    }
}

// ============================================================
// 模块4: UE4 Widget + Android Dialog 拦截
// ============================================================
function installUE4AndDialogHook() {
    log(1, 'UE4', '安装 UE4/Android 弹窗拦截...');

    // UE4 ARM hooks 禁用 (触发反作弊代码完整性检测)
    log(1, 'UE4', 'ARM hooks 禁用, 仅 Java 层拦截');

    // Android Java 层 Dialog/WebView hooks
    Java.perform(function() {
        // Dialog.show
        try {
            var Dialog = Java.use('android.app.Dialog');
            Dialog.show.implementation = function() {
                var cls = this.getClass().getName();
                if (cls.indexOf('ProgressDialog') !== -1 ||
                    cls.indexOf('DatePicker') !== -1) {
                    return this.show();
                }
                var lower = cls.toLowerCase();
                if (lower.indexOf('popup') !== -1 || lower.indexOf('notice') !== -1 ||
                    lower.indexOf('announce') !== -1 || lower.indexOf('activity') !== -1) {
                    log(1, 'Dialog', 'BLOCKED: ' + cls);
                    return;
                }
                log(2, 'Dialog', 'ALLOW: ' + cls);
                return this.show();
            };
            log(1, 'Dialog', 'Dialog.show hook ✓');
        } catch(e) {}

        // WebView.loadUrl
        try {
            var WebView = Java.use('android.webkit.WebView');
            WebView.loadUrl.overload('java.lang.String').implementation = function(url) {
                var lower = url.toLowerCase();
                for (var i = 0; i < CONFIG.pixuiBlockKeywords.length; i++) {
                    if (lower.indexOf(CONFIG.pixuiBlockKeywords[i]) !== -1) {
                        log(1, 'WebView', 'BLOCKED: ' + url);
                        return;
                    }
                }
                log(2, 'WebView', 'ALLOW: ' + url);
                return this.loadUrl(url);
            };
            log(2, 'WebView', 'WebView hook ✓');
        } catch(e) {}

        // PopupWindow
        try {
            var PW = Java.use('android.widget.PopupWindow');
            PW.showAtLocation.overload('android.view.View', 'int', 'int', 'int')
                .implementation = function(p, g, x, y) {
                log(1, 'PopupWin', 'BLOCKED showAtLocation');
            };
        } catch(e) {}
    });
}

// ============================================================
// 主入口
// ============================================================
send('========================================');
send('  PUBG Mobile 弹窗拦截 v2.0');
send('  ARM-on-x86 houdini 兼容版');
send('  ' + new Date().toLocaleString());
send('========================================');

// 对照实验: 只启用 libc + Java hooks, 禁用 ARM 库 hooks
try { installAntiCheatBypass(); } catch(e) { log(1, 'Main', 'AC失败: ' + e); }
try { installNetworkHook(); } catch(e) { log(1, 'Main', 'Net失败: ' + e); }
try { installMSDKHook(); } catch(e) { log(1, 'Main', 'MSDK失败: ' + e); }
// PixUI hook 禁用 (Interceptor.attach ARM 代码会触发反作弊)
// try { installPixUIHook(); } catch(e) { log(1, 'Main', 'PixUI失败: ' + e); }
try { installUE4AndDialogHook(); } catch(e) { log(1, 'Main', 'UE4失败: ' + e); }

send('========================================');
send('  所有模块加载完成');
send('========================================');
