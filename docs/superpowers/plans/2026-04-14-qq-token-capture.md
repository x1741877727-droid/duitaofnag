# QQ Token 抓号上号系统 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 通过 game_proxy MITM 拦截 QQ OAuth 回调，提取 openid/access_token/pay_token，实现自动抓号和 scheme URL 上号。

**Architecture:** FightMaster VPN 增加"抓号模式"，将 QQ auth 流量导向 game_proxy。game_proxy 对 QQ OAuth 域名做 TLS MITM，解析 `_Callback` 响应提取 token。抓完立即断 VPN 防止 ACE 检测。上号时用 `adb am start` 发 scheme URL。

**Tech Stack:** Python asyncio + ssl (game_proxy), Java/Android (FightMaster), FastAPI (backend)

---

## File Structure

### 新建文件
| File | Responsibility |
|------|---------------|
| `proxy/tls_mitm.py` | CA 证书管理，per-hostname 证书生成，SSL context 工厂 |
| `proxy/token_capture.py` | QQ OAuth 响应解析，token 提取和存储，HTTP API |
| `proxy/test_tls_mitm.py` | tls_mitm 单元测试 |
| `proxy/test_token_capture.py` | token 解析单元测试 |

### 修改文件
| File | Changes |
|------|---------|
| `proxy/game_proxy.py` | 抓号模式开关，QQ auth 域名 MITM 路由，response 拦截 |
| `vpn-app/.../FightMasterVpnService.java` | 双 v2ray config（正常/抓号），模式切换 |
| `vpn-app/.../CommandReceiver.java` | 新增 CAPTURE_MODE broadcast action |

---

## Task 1: 实现 tls_mitm.py — CA 证书管理

**Files:**
- Create: `proxy/tls_mitm.py`
- Create: `proxy/test_tls_mitm.py`
- Reference: `proxy/certs/ca.crt`, `proxy/certs/ca.key`

这是 game_proxy MITM 的核心缺失模块。game_proxy.py 已经在 line 356 import 它但文件不存在。

- [ ] **Step 1: 写失败测试 — CA 加载和证书生成**

```python
# proxy/test_tls_mitm.py
import os
import ssl
import pytest

CERTS_DIR = os.path.join(os.path.dirname(__file__), "certs")
CA_CERT = os.path.join(CERTS_DIR, "ca.crt")
CA_KEY = os.path.join(CERTS_DIR, "ca.key")


def test_ca_loads():
    from tls_mitm import CertificateAuthority
    ca = CertificateAuthority(CA_CERT, CA_KEY)
    assert ca is not None


def test_generate_cert_for_hostname():
    from tls_mitm import CertificateAuthority
    ca = CertificateAuthority(CA_CERT, CA_KEY)
    ctx = ca.get_ssl_context_for_client("ssl.ptlogin2.qq.com")
    assert isinstance(ctx, ssl.SSLContext)


def test_upstream_context():
    from tls_mitm import CertificateAuthority
    ctx = CertificateAuthority.get_ssl_context_for_upstream()
    assert isinstance(ctx, ssl.SSLContext)
    # upstream 不验证证书
    assert ctx.check_hostname is False


def test_cert_caching():
    """同一 hostname 应返回缓存的 context"""
    from tls_mitm import CertificateAuthority
    ca = CertificateAuthority(CA_CERT, CA_KEY)
    ctx1 = ca.get_ssl_context_for_client("test.qq.com")
    ctx2 = ca.get_ssl_context_for_client("test.qq.com")
    assert ctx1 is ctx2
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/Zhuanz/Vexa/game-automation && python -m pytest proxy/test_tls_mitm.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tls_mitm'`

- [ ] **Step 3: 实现 tls_mitm.py**

```python
# proxy/tls_mitm.py
"""
TLS MITM 证书管理 — 为 game_proxy 提供 per-hostname 伪造证书。
用法：game_proxy.py line 356 已经 import 本模块。
"""
import ssl
import datetime
import threading

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa


class CertificateAuthority:

    def __init__(self, ca_cert_path: str, ca_key_path: str):
        with open(ca_cert_path, "rb") as f:
            self._ca_cert = x509.load_pem_x509_certificate(f.read())
        with open(ca_key_path, "rb") as f:
            self._ca_key = serialization.load_pem_private_key(f.read(), password=None)
        self._cache: dict[str, ssl.SSLContext] = {}
        self._lock = threading.Lock()

    def _generate_cert(self, hostname: str) -> tuple[bytes, bytes]:
        """生成 hostname 的伪造证书，用 CA 签名"""
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

        subject = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, hostname),
        ])

        now = datetime.datetime.utcnow()
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(self._ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=365))
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName(hostname)]),
                critical=False,
            )
            .sign(self._ca_key, hashes.SHA256())
        )

        cert_pem = cert.public_bytes(serialization.Encoding.PEM)
        key_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
        return cert_pem, key_pem

    def get_ssl_context_for_client(self, hostname: str) -> ssl.SSLContext:
        """返回 server-side SSL context（面向客户端），带 hostname 伪造证书"""
        with self._lock:
            if hostname in self._cache:
                return self._cache[hostname]

        cert_pem, key_pem = self._generate_cert(hostname)

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain_from_buffer(cert_pem, key_pem)

        with self._lock:
            self._cache[hostname] = ctx
        return ctx

    @staticmethod
    def get_ssl_context_for_upstream() -> ssl.SSLContext:
        """返回 client-side SSL context（面向上游），不验证证书"""
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
```

注意：`ssl.SSLContext.load_cert_chain_from_buffer` 在 Python 3.12 不存在，需要用临时文件或 `load_cert_chain`。修正实现：

```python
    def get_ssl_context_for_client(self, hostname: str) -> ssl.SSLContext:
        with self._lock:
            if hostname in self._cache:
                return self._cache[hostname]

        cert_pem, key_pem = self._generate_cert(hostname)

        import tempfile, os
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        # 写临时文件加载证书
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pem") as cf:
            cf.write(cert_pem)
            cert_path = cf.name
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pem") as kf:
            kf.write(key_pem)
            key_path = kf.name
        try:
            ctx.load_cert_chain(cert_path, key_path)
        finally:
            os.unlink(cert_path)
            os.unlink(key_path)

        with self._lock:
            self._cache[hostname] = ctx
        return ctx
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /Users/Zhuanz/Vexa/game-automation && pip install cryptography && python -m pytest proxy/test_tls_mitm.py -v`
Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/Zhuanz/Vexa/game-automation
git add proxy/tls_mitm.py proxy/test_tls_mitm.py
git commit -m "feat(proxy): 实现 tls_mitm.py — CA 证书管理和 per-hostname 伪造证书"
```

---

## Task 2: 实现 token_capture.py — QQ OAuth 响应解析

**Files:**
- Create: `proxy/token_capture.py`
- Create: `proxy/test_token_capture.py`

QQ OAuth 回调格式：`_Callback( {"ret":0, "url":"auth://www.qq.com/oauth2.0/...?openid=xxx&access_token=xxx&pay_token=xxx&..."} )`

- [ ] **Step 1: 写失败测试 — _Callback 解析**

```python
# proxy/test_token_capture.py
import pytest
import json
import os


SAMPLE_CALLBACK = (
    '_Callback( {"ret":0, '
    '"url":"auth://www.qq.com/oauth2.0/show?which=Login&'
    'openid=11446279220371186239&'
    'access_token=03FE61DCBC55C86C18B9455D5D91AA89&'
    'pay_token=396A1198D087D987641CD8F6EFFE7D68&'
    'pf=desktop_m_qq-10000144-android-2002-&'
    'pfkey=abcdef1234567890&'
    'expires_in=7776000"} )'
)


def test_parse_callback_extracts_tokens():
    from token_capture import parse_qq_callback
    result = parse_qq_callback(SAMPLE_CALLBACK)
    assert result is not None
    assert result["openid"] == "11446279220371186239"
    assert result["access_token"] == "03FE61DCBC55C86C18B9455D5D91AA89"
    assert result["pay_token"] == "396A1198D087D987641CD8F6EFFE7D68"
    assert result["pf"] == "desktop_m_qq-10000144-android-2002-"


def test_parse_callback_returns_none_on_error():
    from token_capture import parse_qq_callback
    assert parse_qq_callback("not a callback") is None
    assert parse_qq_callback('_Callback( {"ret":1, "msg":"error"} )') is None


def test_parse_callback_from_http_body():
    """HTTP 响应体中提取 _Callback"""
    from token_capture import extract_callback_from_body
    body = f"some prefix\r\n{SAMPLE_CALLBACK}\r\nsome suffix"
    result = extract_callback_from_body(body)
    assert result is not None
    assert result["openid"] == "11446279220371186239"


def test_token_store_save_and_load():
    from token_capture import TokenStore
    store = TokenStore("/tmp/test_tokens.json")
    store.save("user1", {
        "openid": "123",
        "access_token": "AAA",
        "pay_token": "BBB",
        "pf": "desktop_m_qq-10000144-android-2002-",
    })
    tokens = store.get("user1")
    assert tokens["openid"] == "123"
    assert tokens["access_token"] == "AAA"
    # cleanup
    os.unlink("/tmp/test_tokens.json")


def test_token_store_list_all():
    from token_capture import TokenStore
    store = TokenStore("/tmp/test_tokens2.json")
    store.save("user1", {"openid": "111", "access_token": "A", "pay_token": "B", "pf": ""})
    store.save("user2", {"openid": "222", "access_token": "C", "pay_token": "D", "pf": ""})
    all_tokens = store.list_all()
    assert len(all_tokens) == 2
    os.unlink("/tmp/test_tokens2.json")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/Zhuanz/Vexa/game-automation && python -m pytest proxy/test_token_capture.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'token_capture'`

- [ ] **Step 3: 实现 token_capture.py**

```python
# proxy/token_capture.py
"""
QQ OAuth _Callback 响应解析和 token 存储。
QQ 扫码登录后，OAuth 端点返回 JSONP 回调：
  _Callback( {"ret":0, "url":"auth://www.qq.com/...?openid=xxx&access_token=xxx&pay_token=xxx"} )
本模块负责从 HTTP 响应体中提取这些 token。
"""
import json
import os
import re
import time
import threading
from urllib.parse import urlparse, parse_qs


def parse_qq_callback(text: str) -> dict | None:
    """解析 _Callback JSONP，提取 token 字段。成功返回 dict，失败返回 None。"""
    m = re.search(r'_Callback\s*\(\s*(\{.*?\})\s*\)', text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None

    if data.get("ret") != 0:
        return None

    url_str = data.get("url", "")
    if not url_str:
        return None

    parsed = urlparse(url_str)
    params = parse_qs(parsed.query)

    openid = params.get("openid", [None])[0]
    access_token = params.get("access_token", [None])[0]
    pay_token = params.get("pay_token", [None])[0]

    if not openid or not access_token:
        return None

    return {
        "openid": openid,
        "access_token": access_token,
        "pay_token": pay_token or "",
        "pf": params.get("pf", [""])[0],
        "pfkey": params.get("pfkey", [""])[0],
        "expires_in": params.get("expires_in", [""])[0],
    }


def extract_callback_from_body(body: str) -> dict | None:
    """从 HTTP 响应体中查找并解析 _Callback"""
    return parse_qq_callback(body)


class TokenStore:
    """简单的 JSON 文件 token 存储"""

    def __init__(self, path: str):
        self._path = path
        self._lock = threading.Lock()

    def _load(self) -> dict:
        if not os.path.exists(self._path):
            return {}
        with open(self._path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save(self, data: dict):
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def save(self, user_id: str, tokens: dict):
        with self._lock:
            data = self._load()
            tokens["captured_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            data[user_id] = tokens
            self._save(data)

    def get(self, user_id: str) -> dict | None:
        with self._lock:
            data = self._load()
            return data.get(user_id)

    def list_all(self) -> dict:
        with self._lock:
            return self._load()

    def delete(self, user_id: str):
        with self._lock:
            data = self._load()
            data.pop(user_id, None)
            self._save(data)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /Users/Zhuanz/Vexa/game-automation && python -m pytest proxy/test_token_capture.py -v`
Expected: 5 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/Zhuanz/Vexa/game-automation
git add proxy/token_capture.py proxy/test_token_capture.py
git commit -m "feat(proxy): 实现 QQ OAuth _Callback 解析和 token 存储"
```

---

## Task 3: game_proxy 增加抓号模式 — QQ auth MITM + response 拦截

**Files:**
- Modify: `proxy/game_proxy.py` (lines 314-330 bypass list, lines 336-360 init, lines 482-510 routing, lines 640-773 relay_mitm)

核心改动：增加 `capture_mode` 开关。开启时，QQ auth 域名不再 bypass，走 MITM 路径；在 MITM relay 中拦截 response 匹配 `_Callback`。

- [ ] **Step 1: 在 Socks5Server.__init__ 增加抓号模式状态**

修改 `proxy/game_proxy.py` line 336-360 区域：

```python
# 在 __init__ 中添加（约 line 350 后）：
self._capture_mode = False  # 抓号模式开关
self._token_store = None
self._capture_callback = None  # 抓到 token 后的回调

# QQ OAuth 域名（抓号模式时需要 MITM）
QQ_AUTH_DOMAINS = (
    "ssl.ptlogin2.qq.com",
    "xui.ptlogin2.qq.com",
    "graph.qq.com",
    "auth.qq.com",
)
```

- [ ] **Step 2: 修改路由决策逻辑 — 抓号模式下 QQ auth 走 MITM**

修改 `proxy/game_proxy.py` lines 482-510 的路由判断：

原始代码 line 484-485：
```python
is_ip = _is_ip_address(dst_addr)
bypass_domain = (not is_ip) and any(dst_addr.endswith(s) for s in MITM_BYPASS_SUFFIXES)
```

改为：
```python
is_ip = _is_ip_address(dst_addr)
# 抓号模式下，QQ auth 域名不 bypass，走 MITM
is_qq_auth = (not is_ip) and any(dst_addr == d for d in QQ_AUTH_DOMAINS)
if self._capture_mode and is_qq_auth:
    bypass_domain = False
else:
    bypass_domain = (not is_ip) and any(dst_addr.endswith(s) for s in MITM_BYPASS_SUFFIXES)
```

- [ ] **Step 3: 在 _relay_mitm 中拦截 QQ OAuth response**

修改 `proxy/game_proxy.py` `_relay_mitm()` 方法（约 lines 733-766），在 upstream→client relay 中增加 response 检查：

在 upstream→client 数据转发处（约 line 753）添加：

```python
# 在 upstream_reader.read() 后，client_writer.write() 前
if self._capture_mode and hostname in QQ_AUTH_DOMAINS:
    try:
        body_text = data.decode("utf-8", errors="ignore")
        from token_capture import extract_callback_from_body
        tokens = extract_callback_from_body(body_text)
        if tokens:
            log.info(f"[抓号] 捕获到 token! openid={tokens['openid'][:8]}...")
            if self._token_store:
                self._token_store.save("latest", tokens)
            if self._capture_callback:
                self._capture_callback(tokens)
    except Exception as e:
        log.warning(f"[抓号] 解析 response 失败: {e}")
```

- [ ] **Step 4: 增加 HTTP API 控制抓号模式开关**

在 `proxy/game_proxy.py` 末尾的 `main()` 函数中，增加一个简单的 HTTP 控制端口。在已有 asyncio server 旁边加一个 aiohttp 或简单 HTTP handler：

```python
# 在 game_proxy.py 末尾 main() 中添加控制 API
import aiohttp.web

async def handle_capture_mode(request):
    """POST /capture/start — 开启抓号模式"""
    server = request.app["socks5_server"]
    server._capture_mode = True
    return aiohttp.web.json_response({"ok": True, "capture_mode": True})

async def handle_capture_stop(request):
    """POST /capture/stop — 关闭抓号模式"""
    server = request.app["socks5_server"]
    server._capture_mode = False
    return aiohttp.web.json_response({"ok": True, "capture_mode": False})

async def handle_capture_status(request):
    """GET /capture/status — 查询状态和已捕获的 token"""
    server = request.app["socks5_server"]
    tokens = server._token_store.list_all() if server._token_store else {}
    return aiohttp.web.json_response({
        "capture_mode": server._capture_mode,
        "tokens": tokens,
    })

async def handle_capture_tokens(request):
    """GET /capture/tokens — 获取所有已捕获的 token"""
    server = request.app["socks5_server"]
    tokens = server._token_store.list_all() if server._token_store else {}
    return aiohttp.web.json_response(tokens)

# 在 main() 中 asyncio.start_server 后添加：
app = aiohttp.web.Application()
app["socks5_server"] = socks5_server
app.router.add_post("/capture/start", handle_capture_mode)
app.router.add_post("/capture/stop", handle_capture_stop)
app.router.add_get("/capture/status", handle_capture_status)
app.router.add_get("/capture/tokens", handle_capture_tokens)
runner = aiohttp.web.AppRunner(app)
await runner.setup()
api_site = aiohttp.web.TCPSite(runner, "0.0.0.0", args.port + 1)  # SOCKS5 port + 1
await api_site.start()
log.info(f"[控制API] 监听 0.0.0.0:{args.port + 1}")
```

- [ ] **Step 5: 初始化 TokenStore**

在 `Socks5Server.__init__` 中添加：

```python
from token_capture import TokenStore
self._token_store = TokenStore(
    os.path.join(capture_dir or ".", "tokens.json")
)
```

- [ ] **Step 6: 手动验证 — 在服务器上启动 game_proxy 并测试控制 API**

```bash
# 在服务器 38.22.234.228 上
python game_proxy.py --port 9900 --rules rules.json --capture-dir C:/captures/token_test --ca-cert certs/ca.crt --ca-key certs/ca.key

# 测试控制 API（从另一个终端）
curl http://38.22.234.228:9901/capture/status
# 预期: {"capture_mode": false, "tokens": {}}

curl -X POST http://38.22.234.228:9901/capture/start
# 预期: {"ok": true, "capture_mode": true}
```

- [ ] **Step 7: Commit**

```bash
cd /Users/Zhuanz/Vexa/game-automation
git add proxy/game_proxy.py
git commit -m "feat(proxy): game_proxy 增加抓号模式 — QQ auth MITM + token 拦截 + 控制 API"
```

---

## Task 4: FightMaster 增加抓号模式路由切换

**Files:**
- Modify: `vpn-app/app/src/main/java/com/fightmaster/vpn/FightMasterVpnService.java` (lines 36-56 v2ray config, lines 82-150 startVpn)
- Modify: `vpn-app/app/src/main/java/com/fightmaster/vpn/CommandReceiver.java` (lines 28-36 actions)

核心改动：增加 `CAPTURE_MODE` 广播命令。收到后重新生成 v2ray config，把 QQ auth 域名从 direct 改为 proxy。

- [ ] **Step 1: FightMasterVpnService 增加双配置生成**

修改 `FightMasterVpnService.java`，将 v2ray config 生成抽取为方法：

```java
// 在 FightMasterVpnService.java 中添加

private boolean captureMode = false;

private String buildV2RayConfig() {
    // QQ auth 域名列表
    String qqAuthRule;
    if (captureMode) {
        // 抓号模式：QQ auth 走 proxy（被 game_proxy MITM）
        qqAuthRule = "{\"type\":\"field\",\"outboundTag\":\"proxy\","
            + "\"domain\":[\"ssl.ptlogin2.qq.com\",\"xui.ptlogin2.qq.com\","
            + "\"graph.qq.com\",\"auth.qq.com\"]},"
            // 其他 QQ 域名仍然走 direct
            + "{\"type\":\"field\",\"outboundTag\":\"direct\","
            + "\"domain\":[\"qq.com\",\"tencent.com\",\"wechat.com\","
            + "\"weixin.qq.com\",\"gtimg.cn\",\"qpic.cn\",\"idqqimg.com\","
            + "\"qlogo.cn\",\"myqcloud.com\"]},";
    } else {
        // 正常模式：所有 QQ 域名走 direct
        qqAuthRule = "{\"type\":\"field\",\"outboundTag\":\"direct\","
            + "\"domain\":[\"qq.com\",\"tencent.com\",\"wechat.com\","
            + "\"weixin.qq.com\",\"gtimg.cn\",\"qpic.cn\",\"idqqimg.com\","
            + "\"qlogo.cn\",\"myqcloud.com\"]},";
    }

    return "{\"dns\":{\"servers\":[\"223.5.5.5\",\"8.8.8.8\"],"
        + "\"hosts\":{\"gameproxy-verify\":\"1.2.3.4\"}},"
        + "\"outbounds\":["
        + "{\"tag\":\"proxy\",\"protocol\":\"socks\","
        + "\"settings\":{\"servers\":[{\"address\":\"" + PROXY_HOST + "\","
        + "\"port\":" + PROXY_PORT + "}]}},"
        + "{\"tag\":\"direct\",\"protocol\":\"freedom\",\"settings\":{}},"
        + "{\"tag\":\"dns-out\",\"protocol\":\"dns\",\"settings\":{}}"
        + "],"
        + "\"routing\":{\"domainStrategy\":\"IPOnDemand\",\"rules\":["
        + "{\"type\":\"field\",\"outboundTag\":\"dns-out\",\"port\":\"53\"},"
        + qqAuthRule
        + "{\"type\":\"field\",\"outboundTag\":\"direct\","
        + "\"ip\":[\"120.204.207.84\",\"101.226.94.67\",\"101.226.96.203\","
        + "\"116.128.169.94\",\"58.246.163.95\",\"221.181.98.213\","
        + "\"183.192.196.121\",\"116.128.169.68\",\"101.226.101.163\"]},"
        + "{\"type\":\"field\",\"outboundTag\":\"direct\","
        + "\"domain\":[\"googleapis.com\",\"google.com\",\"gstatic.com\"]},"
        + "{\"type\":\"field\",\"outboundTag\":\"proxy\",\"port\":\"0-65535\"}"
        + "]}}";
}
```

- [ ] **Step 2: 修改 startVpn 使用新方法**

替换 `startVpn()` 中 line 112-115 原来硬编码的 config 为：

```java
String config = buildV2RayConfig();
byte[] configBytes = config.getBytes(java.nio.charset.StandardCharsets.UTF_8);
```

- [ ] **Step 3: 添加模式切换方法**

```java
// 在 FightMasterVpnService.java 中添加

public void switchToCaptureMode() {
    captureMode = true;
    restartVpnWithNewConfig();
}

public void switchToNormalMode() {
    captureMode = false;
    restartVpnWithNewConfig();
}

private void restartVpnWithNewConfig() {
    // 停止当前 v2ray
    Tun2socks.stopV2Ray();
    // 用新 config 重启
    String config = buildV2RayConfig();
    byte[] configBytes = config.getBytes(java.nio.charset.StandardCharsets.UTF_8);
    Tun2socks.startV2Ray(
        tunFd, this, new NoOpDBService(), configBytes,
        "proxy", "http,tls", getFilesDir().getAbsolutePath(),
        false, false, ""
    );
    broadcastStatus(captureMode ? "capture_mode" : "connected", "");
}
```

注意：需要将 `tunFd` 保存为成员变量。在 `startVpn()` 中 `Tun2socks.startV2Ray()` 调用前添加 `this.tunFd = tunFd;`。

- [ ] **Step 4: CommandReceiver 增加 CAPTURE_MODE 广播**

修改 `CommandReceiver.java` 添加新 action：

```java
// 在 onReceive() 中添加
case "com.fightmaster.vpn.CAPTURE_ON":
    if (service != null) {
        service.switchToCaptureMode();
    }
    break;
case "com.fightmaster.vpn.CAPTURE_OFF":
    if (service != null) {
        service.switchToNormalMode();
    }
    break;
```

在 `AndroidManifest.xml` 的 CommandReceiver 中添加：
```xml
<action android:name="com.fightmaster.vpn.CAPTURE_ON" />
<action android:name="com.fightmaster.vpn.CAPTURE_OFF" />
```

- [ ] **Step 5: 构建 APK 验证编译通过**

```bash
cd /Users/Zhuanz/Vexa/game-automation/vpn-app
./gradlew assembleDebug
# 预期: BUILD SUCCESSFUL, APK at app/build/outputs/apk/debug/app-debug.apk
```

- [ ] **Step 6: Commit**

```bash
cd /Users/Zhuanz/Vexa/game-automation
git add vpn-app/
git commit -m "feat(vpn): FightMaster 增加抓号模式 — QQ auth 域名动态路由切换"
```

---

## Task 5: 端到端验证 — 抓号流程

**Files:** 无新文件，纯操作验证

这是最关键的验证步骤：确认整条链路能抓到 QQ OAuth token。

- [ ] **Step 1: 在模拟器上安装 CA 证书**

```bash
# 通过 Remote Agent 在 Windows 上执行
# 先把 ca.crt 推到模拟器
adb -s emulator-5558 push proxy/certs/1a7ab97c.0 /sdcard/
adb -s emulator-5558 shell su 0 mount -o rw,remount /system
adb -s emulator-5558 shell su 0 cp /sdcard/1a7ab97c.0 /system/etc/security/cacerts/
adb -s emulator-5558 shell su 0 chmod 644 /system/etc/security/cacerts/1a7ab97c.0
adb -s emulator-5558 shell su 0 mount -o ro,remount /system
```

- [ ] **Step 2: 部署新版 game_proxy 到服务器**

```bash
# 上传 tls_mitm.py, token_capture.py 和更新的 game_proxy.py 到服务器
# 安装依赖: pip install cryptography aiohttp
# 启动 game_proxy（带 CA 证书参数）
python game_proxy.py --port 9900 --rules rules.json \
  --ca-cert certs/ca.crt --ca-key certs/ca.key \
  --capture-dir C:/captures/token_test \
  --capture-ports 443,8443
```

- [ ] **Step 3: 安装新版 FightMaster APK 到模拟器**

```bash
adb -s emulator-5558 install -r app/build/outputs/apk/debug/app-debug.apk
# 启动 FightMaster
adb -s emulator-5558 shell am broadcast -a com.fightmaster.vpn.START
```

- [ ] **Step 4: 开启抓号模式**

```bash
# 1. game_proxy 开启抓号模式
curl -X POST http://38.22.234.228:9901/capture/start

# 2. FightMaster 切换到抓号模式
adb -s emulator-5558 shell am broadcast -a com.fightmaster.vpn.CAPTURE_ON
```

- [ ] **Step 5: 在模拟器中操作 QQ 扫码登录**

手动操作：
1. 打开和平精英
2. 点击 QQ 登录
3. 用手机 QQ 扫码确认

观察 game_proxy 日志：
```bash
# 在服务器上查看日志
type C:\captures\token_test\proxy.log | findstr "抓号"
# 预期看到: [抓号] 捕获到 token! openid=XXXXXXXX...
```

- [ ] **Step 6: 验证 token 已保存**

```bash
# 查询捕获的 token
curl http://38.22.234.228:9901/capture/tokens
# 预期: {"latest": {"openid": "...", "access_token": "...", "pay_token": "...", ...}}
```

- [ ] **Step 7: 立即关闭抓号模式**

```bash
# 关闭抓号模式（防止游戏服务器流量走 MITM）
curl -X POST http://38.22.234.228:9901/capture/stop
adb -s emulator-5558 shell am broadcast -a com.fightmaster.vpn.CAPTURE_OFF

# 杀掉游戏（不让它在 MITM 残留状态下连服务器）
adb -s emulator-5558 shell am force-stop com.tencent.tmgp.pubgmhd
```

- [ ] **Step 8: 记录结果并 commit**

如果抓到了 token，记录实际的 `_Callback` 响应格式供后续参考：

```bash
cd /Users/Zhuanz/Vexa/game-automation
git add -A
git commit -m "验证: QQ OAuth token 抓取端到端测试"
```

---

## Task 6: 上号功能 — Scheme URL 登录

**Files:**
- Modify: `proxy/game_proxy.py` (增加 /login API)

验证抓号成功后，实现上号功能。

- [ ] **Step 1: 在 game_proxy 控制 API 增加上号接口**

```python
async def handle_login(request):
    """POST /login — 用 token 通过 scheme URL 上号
    Body: {"user_id": "latest", "adb_serial": "emulator-5558", "adb_host": "192.168.0.102:9100", "adb_auth": "xxx"}
    """
    body = await request.json()
    server = request.app["socks5_server"]
    user_id = body.get("user_id", "latest")
    tokens = server._token_store.get(user_id)
    if not tokens:
        return aiohttp.web.json_response({"ok": False, "error": "token not found"}, status=404)

    # 构造 scheme URL（和平精英 AppID: 1104466820）
    scheme_url = (
        f"tencent1104466820://?platform=qq_m"
        f"&current_uin={tokens['openid']}"
        f"&launchfrom=sq_gamecenter"
        f"&user_openid={tokens['openid']}"
        f"&ptoken={tokens['pay_token']}"
        f"&openid={tokens['openid']}"
        f"&atoken={tokens['access_token']}"
    )

    # 通过 Remote Agent 执行 adb 命令
    adb_host = body.get("adb_host")
    adb_auth = body.get("adb_auth")
    adb_serial = body.get("adb_serial", "emulator-5558")
    adb_path = body.get("adb_path", r"D:\leidian\LDPlayer64\adb.exe")

    if adb_host:
        import aiohttp as ah
        cmd = f'{adb_path} -s {adb_serial} shell am start -a android.intent.action.VIEW -d "{scheme_url}"'
        async with ah.ClientSession() as session:
            async with session.post(
                f"http://{adb_host}/exec",
                json={"cmd": cmd},
                headers={"X-Auth": adb_auth, "Content-Type": "application/json"},
            ) as resp:
                result = await resp.json()
                return aiohttp.web.json_response({"ok": result.get("ok"), "result": result})
    else:
        return aiohttp.web.json_response({
            "ok": True,
            "scheme_url": scheme_url,
            "instruction": "手动执行: adb shell am start -a android.intent.action.VIEW -d '<scheme_url>'"
        })

# 注册路由
app.router.add_post("/login", handle_login)
```

- [ ] **Step 2: 验证上号**

```bash
# 先确保游戏已关闭，FightMaster 在正常模式
adb -s emulator-5558 shell am force-stop com.tencent.tmgp.pubgmhd
adb -s emulator-5558 shell am broadcast -a com.fightmaster.vpn.CAPTURE_OFF

# 通过 API 上号
curl -X POST http://38.22.234.228:9901/login \
  -H "Content-Type: application/json" \
  -d '{"user_id": "latest", "adb_serial": "emulator-5558", "adb_host": "192.168.0.102:9100", "adb_auth": "<密码>"}'

# 观察模拟器：游戏应该启动并自动登录
```

- [ ] **Step 3: Commit**

```bash
cd /Users/Zhuanz/Vexa/game-automation
git add proxy/game_proxy.py
git commit -m "feat(proxy): 增加 /login 上号 API — scheme URL 自动登录"
```

---

## 验证清单

| 检查项 | 预期 |
|--------|------|
| tls_mitm 测试通过 | 4/4 PASS |
| token_capture 测试通过 | 5/5 PASS |
| game_proxy 启动不报错（带 --ca-cert/--ca-key） | OK |
| 控制 API `/capture/status` 返回 JSON | OK |
| 抓号模式开启后 QQ auth 域名走 MITM | game_proxy 日志可见 |
| FightMaster CAPTURE_ON 广播生效 | v2ray config 重载 |
| 模拟器 CA 证书安装后 HTTPS 不报错 | 浏览器访问 https 正常 |
| QQ 扫码登录后 `_Callback` 被捕获 | `/capture/tokens` 返回 token |
| Scheme URL 上号成功 | 游戏自动登录进入大厅 |
| 上号过程 ACE 不检测 | 正常模式，无 MITM |
