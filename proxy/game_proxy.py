"""
game_proxy.py — 自研游戏代理服务
替代 CCProxy + Charles + WPE 三件套

功能：
1. SOCKS5 代理入口（带 Token 鉴权）
2. TLS MITM（解密 → 封包改写 → 重加密）
3. TCP 封包改写（翻译 WPE .fp 规则）
4. HTTP 拦截（m.baidu.com → 状态页面）

启动：
  python game_proxy.py --port 9900 --rules rules.json --ca-cert certs/ca.crt --ca-key certs/ca.key
"""

import asyncio
import json
import logging
import ssl
import struct
import sys
import time
import argparse
import os
import threading
from dataclasses import dataclass, field

# 确保脚本所在目录在 sys.path（方便 import tls_mitm）
_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("game_proxy")


# ═══════════════════════════════════════
# 封包改写规则引擎
# ═══════════════════════════════════════

@dataclass
class PacketRule:
    """一条封包改写规则"""
    name: str
    enabled: bool = True
    # 匹配条件
    search: list[tuple[int, int]] = field(default_factory=list)  # [(position, byte_value), ...]
    # 修改操作
    modify: list[tuple[int, int]] = field(default_factory=list)  # [(position, new_value), ...]
    # 可选过滤条件
    header_match: bytes | None = None       # 封包头匹配
    length_min: int = 0                      # 最小长度
    length_max: int = 0                      # 最大长度 (0=不限)
    action: str = "replace"                  # replace=修改匹配位置, change=整包替换

    def matches(self, data: bytes) -> bool:
        """检查封包是否匹配此规则"""
        if not self.enabled:
            return False

        # 长度过滤
        if self.length_min > 0 and len(data) < self.length_min:
            return False
        if self.length_max > 0 and len(data) > self.length_max:
            return False

        # header 过滤
        # WPE 中 search/modify 位置是从 header 之后开始算的
        offset = 0
        if self.header_match is not None:
            if len(data) < len(self.header_match):
                return False
            if data[:len(self.header_match)] != self.header_match:
                return False
            offset = len(self.header_match)

        # 字节模式匹配（位置 = header 之后的偏移）
        for pos, val in self.search:
            actual_pos = pos + offset
            if actual_pos >= len(data):
                return False
            if data[actual_pos] != val:
                return False

        return True

    def match_debug(self, data: bytes) -> tuple[bool, str]:
        """检查封包是否匹配，返回 (matched, reason_string) 用于诊断"""
        if not self.enabled:
            return False, "disabled"
        if self.length_min > 0 and len(data) < self.length_min:
            return False, f"too_short({len(data)}<{self.length_min})"
        if self.length_max > 0 and len(data) > self.length_max:
            return False, f"too_long({len(data)}>{self.length_max})"

        offset = 0
        if self.header_match is not None:
            if len(data) < len(self.header_match):
                return False, f"header_short({len(data)}<{len(self.header_match)})"
            if data[:len(self.header_match)] != self.header_match:
                actual = data[:len(self.header_match)].hex()
                expected = self.header_match.hex()
                return False, f"header_mismatch(got={actual},want={expected})"
            offset = len(self.header_match)

        for pos, val in self.search:
            actual_pos = pos + offset
            if actual_pos >= len(data):
                return False, f"pos{pos}_oob(need>{actual_pos},have={len(data)})"
            if data[actual_pos] != val:
                return False, f"pos{pos}_mismatch(got=0x{data[actual_pos]:02x},want=0x{val:02x},abs={actual_pos})"

        return True, "matched"

    def apply(self, data: bytes) -> bytes:
        """应用修改"""
        # WPE 中 modify 位置也是从 header 之后开始算的
        offset = len(self.header_match) if self.header_match else 0

        if self.action == "change":
            max_pos = max(pos + offset for pos, _ in self.modify) if self.modify else 0
            new_data = bytearray(max(len(data), max_pos + 1))
            new_data[:len(data)] = data
            for pos, val in self.modify:
                actual_pos = pos + offset
                if actual_pos < len(new_data):
                    new_data[actual_pos] = val
            return bytes(new_data[:max_pos + 1])
        else:
            # replace：只修改指定位置
            buf = bytearray(data)
            for pos, val in self.modify:
                actual_pos = pos + offset
                if actual_pos < len(buf):
                    buf[actual_pos] = val
            return bytes(buf)


class RuleEngine:
    """封包改写规则引擎"""

    def __init__(self, dry_run: bool = False, rule_debug: bool = False):
        self.rules: list[PacketRule] = []
        self.dry_run = dry_run  # True = 只记录匹配，不实际修改
        self.rule_debug = rule_debug  # True = 记录每条规则不匹配的原因
        self._stats = {"total_packets": 0, "modified_packets": 0, "matched_rules": {}}

    def load_from_json(self, path: str):
        """从 JSON 文件加载规则"""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.rules = []
        for r in data.get("rules", []):
            rule = PacketRule(
                name=r["name"],
                enabled=r.get("enabled", True),
                search=[(s["pos"], s["val"]) for s in r.get("search", [])],
                modify=[(m["pos"], m["val"]) for m in r.get("modify", [])],
                header_match=bytes.fromhex(r["header"]) if r.get("header") else None,
                length_min=r.get("length_min", 0),
                length_max=r.get("length_max", 0),
                action=r.get("action", "replace"),
            )
            self.rules.append(rule)
        logger.info(f"加载 {len(self.rules)} 条规则")

    def process(self, data: bytes, direction: str = "send") -> bytes:
        """处理一个封包，返回修改后的数据"""
        self._stats["total_packets"] += 1

        # 记录前 64 字节 hex（扩展自 16 字节）
        if len(data) >= 4 and self._stats["total_packets"] <= 500:
            header_hex = data[:min(64, len(data))].hex()
            logger.debug(f"封包 ({direction}) #{self._stats['total_packets']} "
                        f"len={len(data)}: {header_hex}")
            if self._stats["total_packets"] % 20 == 1:
                logger.info(f"封包样本 ({direction}) len={len(data)}: {header_hex}")

        for rule in self.rules:
            if self.rule_debug:
                matched, reason = rule.match_debug(data)
                if matched:
                    logger.info(f"[RULE-DEBUG] [{rule.name}] MATCHED ({direction}) "
                                f"len={len(data)} head={data[:16].hex()}")
                elif self._stats["total_packets"] <= 200:
                    logger.debug(f"[RULE-DEBUG] [{rule.name}] skip: {reason} "
                                f"({direction}) len={len(data)}")
                if not matched:
                    continue
            else:
                if not rule.matches(data):
                    continue

            self._stats["matched_rules"][rule.name] = \
                self._stats["matched_rules"].get(rule.name, 0) + 1

            if self.dry_run:
                logger.info(f"[DRY-RUN] 规则 [{rule.name}] 命中 ({direction}), "
                            f"len={len(data)}, head={data[:16].hex()}")
                return data  # 不修改

            modified = rule.apply(data)
            if modified != data:
                self._stats["modified_packets"] += 1
                logger.info(f"规则 [{rule.name}] 命中并修改 ({direction}), "
                            f"len={len(data)}→{len(modified)}, head={data[:16].hex()}")
                return modified
        return data

    @property
    def stats(self) -> dict:
        return dict(self._stats)


# ═══════════════════════════════════════
# 完整封包抓取
# ═══════════════════════════════════════

class PacketCapture:
    """完整封包抓取 — 每个封包存 .bin + .json 元数据"""

    def __init__(self, capture_dir: str, capture_ports: set[int] | None = None):
        self.capture_dir = capture_dir
        self.capture_ports = capture_ports  # None = 抓所有端口
        self._conn_counter = 0
        self._pkt_counter = 0
        self._lock = threading.Lock()
        os.makedirs(capture_dir, exist_ok=True)
        logger.info(f"[CAPTURE] 启用 → {capture_dir}, 端口={capture_ports or 'all'}")

    def new_connection(self, dst_addr: str, dst_port: int) -> str:
        """注册新连接，返回 conn_id"""
        with self._lock:
            self._conn_counter += 1
            conn_id = f"conn_{self._conn_counter:06d}"
        conn_dir = os.path.join(self.capture_dir, conn_id)
        os.makedirs(conn_dir, exist_ok=True)
        meta = {
            "conn_id": conn_id,
            "dst_addr": dst_addr,
            "dst_port": dst_port,
            "start_time": time.time(),
            "start_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        with open(os.path.join(conn_dir, "meta.json"), "w") as f:
            json.dump(meta, f, indent=2)
        return conn_id

    def should_capture(self, dst_port: int) -> bool:
        if self.capture_ports is None:
            return True
        return dst_port in self.capture_ports

    def save_packet(self, conn_id: str, data: bytes, direction: str, sequence: int):
        """保存一个封包为 .bin + .json"""
        with self._lock:
            self._pkt_counter += 1
            pkt_num = self._pkt_counter

        prefix = "c2s" if direction == "send" else "s2c"
        if direction.endswith("_mod"):
            prefix = "c2s_mod"
        base = f"{prefix}_{sequence:06d}"
        conn_dir = os.path.join(self.capture_dir, conn_id)

        # 二进制完整数据
        with open(os.path.join(conn_dir, f"{base}.bin"), "wb") as f:
            f.write(data)

        # 元数据
        pkt_meta = {
            "pkt_num": pkt_num,
            "direction": direction,
            "sequence": sequence,
            "length": len(data),
            "timestamp": time.time(),
            "hex_head_64": data[:64].hex() if len(data) >= 64 else data.hex(),
        }
        with open(os.path.join(conn_dir, f"{base}.json"), "w") as f:
            json.dump(pkt_meta, f, indent=2)


# ═══════════════════════════════════════
# SOCKS5 代理服务器
# ═══════════════════════════════════════

# SOCKS5 常量
SOCKS5_VER = 0x05
SOCKS5_AUTH_NONE = 0x00
SOCKS5_AUTH_USERPASS = 0x02
SOCKS5_AUTH_REJECT = 0xFF
SOCKS5_CMD_CONNECT = 0x01
SOCKS5_CMD_UDP_ASSOCIATE = 0x03
SOCKS5_ATYP_IPV4 = 0x01
SOCKS5_ATYP_DOMAIN = 0x03
SOCKS5_ATYP_IPV6 = 0x04


import ipaddress

def _is_ip_address(addr: str) -> bool:
    """判断地址是 IP 还是域名"""
    try:
        ipaddress.ip_address(addr)
        return True
    except ValueError:
        return False


# MITM 绕过域名列表 — 这些域名不做 MITM，直接透传
# QQ 登录、微信登录、CDN 等不能用自签证书的域名
MITM_BYPASS_SUFFIXES = (
    ".qq.com",
    ".tencent.com",
    ".wechat.com",
    ".weixin.qq.com",
    ".gtimg.cn",
    ".myqcloud.com",
    ".qpic.cn",
    ".idqqimg.com",
    ".googleapis.com",
    ".gstatic.com",
    ".google.com",
    ".cdn-go.cn",
    ".gcloudsdk.com",
    ".anticheatexpert.com",
    ".crashsight.qq.com",
)


class Socks5Server:
    """SOCKS5 代理服务器，集成 TLS MITM + 封包改写"""

    def __init__(self, host: str = "0.0.0.0", port: int = 9900,
                 tokens: set[str] | None = None,
                 rule_engine: RuleEngine | None = None,
                 ca_cert: str | None = None,
                 ca_key: str | None = None,
                 capture: PacketCapture | None = None,
                 mitm_ports: set[int] | None = None):
        self.host = host
        self.port = port
        self.tokens = tokens  # None = 不鉴权
        self.rule_engine = rule_engine or RuleEngine()
        self._capture = capture
        self._active_connections = 0
        self._total_connections = 0
        self._mitm_connections = 0      # MITM 成功次数
        self._start_time = time.time()
        self._ca = None
        self._mitm_ports = mitm_ports or {443, 8443, 10012}

        # 初始化 TLS MITM
        if ca_cert and ca_key and os.path.exists(ca_cert) and os.path.exists(ca_key):
            try:
                from tls_mitm import CertificateAuthority
                self._ca = CertificateAuthority(ca_cert, ca_key)
                logger.info(f"TLS MITM: 已启用 (CA: {ca_cert})")
            except Exception as e:
                logger.warning(f"TLS MITM 初始化失败: {e}, 将只做纯转发")


    async def start(self, api_port: int | None = None):
        server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )
        addr = server.sockets[0].getsockname()
        logger.info(f"SOCKS5 代理启动: {addr[0]}:{addr[1]}")
        if self.tokens:
            logger.info(f"鉴权: 已配置 {len(self.tokens)} 个 Token")
        else:
            logger.info("鉴权: 无（开放访问）")
        logger.info(f"规则: {len(self.rule_engine.rules)} 条")

        # 启动控制 API（HTTP，默认端口 = SOCKS5 端口 + 1）
        if api_port is None:
            api_port = self.port + 1

        async def handle_api(reader, writer):
            """简单 HTTP API handler"""
            try:
                request_line = await asyncio.wait_for(reader.readline(), timeout=5)
                if not request_line:
                    writer.close()
                    return

                # 读取 headers
                content_length = 0
                while True:
                    line = await reader.readline()
                    if line == b"\r\n" or line == b"\n" or not line:
                        break
                    if b":" in line:
                        k, v = line.decode().split(":", 1)
                        if k.strip().lower() == "content-length":
                            content_length = int(v.strip())

                # 读取 body
                if content_length > 0:
                    await asyncio.wait_for(reader.read(content_length), timeout=5)

                req = request_line.decode().strip()
                method, path, *_ = req.split(" ")

                # 路由
                if method == "GET" and path == "/status":
                    result = self._get_verify_json()
                else:
                    result = {"error": "not found", "endpoints": ["GET /status"]}

                resp_body = json.dumps(result, ensure_ascii=False).encode("utf-8")
                response = (
                    f"HTTP/1.1 200 OK\r\n"
                    f"Content-Type: application/json; charset=utf-8\r\n"
                    f"Content-Length: {len(resp_body)}\r\n"
                    f"Access-Control-Allow-Origin: *\r\n"
                    f"Connection: close\r\n"
                    f"\r\n"
                ).encode("utf-8") + resp_body
                writer.write(response)
                await writer.drain()
            except Exception as e:
                logger.debug(f"API 请求异常: {e}")
            finally:
                writer.close()

        api_server = await asyncio.start_server(handle_api, "0.0.0.0", api_port)
        logger.info(f"[控制API] 监听 0.0.0.0:{api_port}")

        async with server:
            async with api_server:
                await asyncio.gather(
                    server.serve_forever(),
                    api_server.serve_forever(),
                )

    async def _handle_client(self, reader: asyncio.StreamReader,
                             writer: asyncio.StreamWriter):
        """处理一个 SOCKS5 客户端连接"""
        client_addr = writer.get_extra_info('peername')
        self._active_connections += 1
        self._total_connections += 1

        try:
            # ── 1. 握手：协商认证方式 ──
            header = await asyncio.wait_for(reader.read(2), timeout=10)
            if len(header) < 2 or header[0] != SOCKS5_VER:
                return

            n_methods = header[1]
            methods = await asyncio.wait_for(reader.read(n_methods), timeout=10)

            if self.tokens:
                # 要求用户名/密码认证
                writer.write(bytes([SOCKS5_VER, SOCKS5_AUTH_USERPASS]))
                await writer.drain()

                # ── 2. 用户名/密码认证 ──
                auth_ver = await asyncio.wait_for(reader.read(1), timeout=10)
                if not auth_ver or auth_ver[0] != 0x01:
                    return

                ulen = (await reader.read(1))[0]
                username = (await reader.read(ulen)).decode("utf-8", errors="replace")
                plen = (await reader.read(1))[0]
                password = (await reader.read(plen)).decode("utf-8", errors="replace")

                # Token 验证：用户名或密码作为 token
                token = password or username
                if token not in self.tokens:
                    logger.warning(f"认证失败: {client_addr} token={token[:8]}...")
                    writer.write(bytes([0x01, 0x01]))  # 失败
                    await writer.drain()
                    return

                writer.write(bytes([0x01, 0x00]))  # 成功
                await writer.drain()
            else:
                # 无认证
                writer.write(bytes([SOCKS5_VER, SOCKS5_AUTH_NONE]))
                await writer.drain()

            # ── 3. 连接请求 ──
            req = await asyncio.wait_for(reader.read(4), timeout=10)
            if len(req) < 4:
                return

            cmd = req[1]
            atyp = req[3]
            if atyp == SOCKS5_ATYP_IPV4:
                raw_addr = await reader.read(4)
                dst_addr = ".".join(str(b) for b in raw_addr)
            elif atyp == SOCKS5_ATYP_DOMAIN:
                addr_len = (await reader.read(1))[0]
                raw_addr = await reader.read(addr_len)
                dst_addr = raw_addr.decode("utf-8", errors="replace")
            elif atyp == SOCKS5_ATYP_IPV6:
                raw_addr = await reader.read(16)
                dst_addr = ":".join(f"{raw_addr[i]:02x}{raw_addr[i+1]:02x}"
                                    for i in range(0, 16, 2))
            else:
                return

            raw_port = await reader.read(2)
            dst_port = struct.unpack("!H", raw_port)[0]

            # ── 4. UDP ASSOCIATE ──
            if cmd == SOCKS5_CMD_UDP_ASSOCIATE:
                await self._handle_udp_associate(reader, writer, client_addr)
                return

            if cmd != SOCKS5_CMD_CONNECT:
                writer.write(bytes([SOCKS5_VER, 0x07, 0x00, 0x01,
                                    0, 0, 0, 0, 0, 0]))
                await writer.drain()
                return

            # ── 5. 检查是否需要拦截（验证端点等） ──
            intercept_type = self._should_intercept(dst_addr, dst_port)
            if intercept_type:
                await self._handle_intercept(reader, writer, dst_addr, dst_port, intercept_type)
                return

            # ── 6. 连接目标服务器 ──
            try:
                remote_reader, remote_writer = await asyncio.wait_for(
                    asyncio.open_connection(dst_addr, dst_port),
                    timeout=10
                )
            except Exception as e:
                logger.debug(f"连接失败: {dst_addr}:{dst_port} - {e}")
                writer.write(bytes([SOCKS5_VER, 0x05, 0x00, 0x01,
                                    0, 0, 0, 0, 0, 0]))
                await writer.drain()
                return

            # 返回连接成功
            writer.write(bytes([SOCKS5_VER, 0x00, 0x00, 0x01,
                                0, 0, 0, 0, 0, 0]))
            await writer.drain()

            # ── 7. TLS MITM 或纯转发 ──
            # 判断目标是 IP 还是域名
            is_ip = _is_ip_address(dst_addr)
            bypass_domain = (not is_ip) and any(dst_addr.endswith(s) for s in MITM_BYPASS_SUFFIXES)

            # 设置抓包
            capture = self._capture
            conn_id = None
            if capture and capture.should_capture(dst_port):
                conn_id = capture.new_connection(dst_addr, dst_port)
                logger.info(f"[CAPTURE] {conn_id}: {dst_addr}:{dst_port}")

            if is_ip:
                # IP 地址 → 游戏服务器私有协议，不做 MITM，直接 TCP 转发 + 规则
                logger.info(f"直连+规则: {dst_addr}:{dst_port} (IP, 私有协议)")
                await self._relay(reader, writer, remote_reader, remote_writer,
                                  dst_addr, dst_port,
                                  capture=capture, conn_id=conn_id)
            elif bypass_domain:
                # QQ/微信/Google 等域名 → 不做 MITM，直接透传（不改包）
                logger.info(f"直连透传: {dst_addr}:{dst_port} (bypass domain)")
                await self._relay_passthrough(reader, writer, remote_reader, remote_writer)
            elif self._ca and dst_port in self._mitm_ports:
                # 其他域名 443 → MITM 解密 + 规则
                await self._relay_mitm(reader, writer, dst_addr, dst_port, remote_reader, remote_writer)
            else:
                await self._relay(reader, writer, remote_reader, remote_writer,
                                  dst_addr, dst_port,
                                  capture=capture, conn_id=conn_id)

        except asyncio.TimeoutError:
            pass
        except ConnectionError:
            pass
        except Exception as e:
            logger.debug(f"连接异常: {client_addr} - {e}")
        finally:
            self._active_connections -= 1
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _relay_passthrough(self, client_reader, client_writer,
                                remote_reader, remote_writer):
        """纯透传，不过规则引擎（用于 QQ 登录等不能改包的域名）"""

        async def c2r():
            try:
                while True:
                    data = await client_reader.read(65536)
                    if not data:
                        break
                    remote_writer.write(data)
                    await remote_writer.drain()
            except Exception:
                pass
            finally:
                try:
                    remote_writer.close()
                except Exception:
                    pass

        async def r2c():
            try:
                while True:
                    data = await remote_reader.read(65536)
                    if not data:
                        break
                    client_writer.write(data)
                    await client_writer.drain()
            except Exception:
                pass
            finally:
                try:
                    client_writer.close()
                except Exception:
                    pass

        await asyncio.gather(c2r(), r2c())

    async def _relay(self, client_reader, client_writer,
                     remote_reader, remote_writer,
                     dst_addr: str, dst_port: int,
                     capture: 'PacketCapture | None' = None,
                     conn_id: str | None = None):
        """双向转发数据，对发送方向应用封包改写规则，可选完整抓包"""

        async def client_to_remote():
            """客户端 → 远程服务器（游戏发出的包，应用改写规则）"""
            apply_rules = True
            seq = 0
            try:
                first_packet = True
                while True:
                    data = await client_reader.read(65536)
                    if not data:
                        break
                    # 第一个包检测：如果是 TLS ClientHello (16 03 xx)，跳过规则
                    if first_packet:
                        first_packet = False
                        if len(data) >= 3 and data[0] == 0x16 and data[1] == 0x03:
                            apply_rules = False
                            logger.info(f"TLS 检测: {dst_addr}:{dst_port} 是 TLS 连接，跳过规则")

                    # 抓包：保存原始封包
                    if capture and conn_id:
                        capture.save_packet(conn_id, data, "send", seq)

                    if apply_rules:
                        modified = self.rule_engine.process(data, direction="send")
                        # 抓包：如果修改了，也保存修改后的版本
                        if capture and conn_id and modified != data:
                            capture.save_packet(conn_id, modified, "send_mod", seq)
                        remote_writer.write(modified)
                    else:
                        remote_writer.write(data)
                    await remote_writer.drain()
                    seq += 1
            except Exception:
                pass
            finally:
                try:
                    remote_writer.close()
                except Exception:
                    pass

        async def remote_to_client():
            """远程服务器 → 客户端（游戏收到的包，抓包 + 规则观察）"""
            seq = 0
            try:
                while True:
                    data = await remote_reader.read(65536)
                    if not data:
                        break

                    # 抓包：保存服务端返回
                    if capture and conn_id:
                        capture.save_packet(conn_id, data, "recv", seq)

                    # 对 recv 方向也过规则引擎（用于观察/统计，不实际修改）
                    if self.rule_engine.dry_run or self.rule_engine.rule_debug:
                        self.rule_engine.process(data, direction="recv")

                    client_writer.write(data)
                    await client_writer.drain()
                    seq += 1
            except Exception:
                pass
            finally:
                try:
                    client_writer.close()
                except Exception:
                    pass

        await asyncio.gather(client_to_remote(), remote_to_client())

    async def _relay_mitm(self, client_reader, client_writer,
                          dst_addr: str, dst_port: int,
                          remote_reader=None, remote_writer=None):
        """TLS MITM 或私有协议转发

        先 peek 判断 TLS vs 非 TLS：
        - TLS → start_tls() MITM 解密 + 封包改写
        - 非 TLS → 直连 + 封包改写（游戏私有协议）
        """
        from tls_mitm import CertificateAuthority

        hostname = dst_addr

        try:
            # TLS 连接 → MITM
            logger.info(f"MITM: {hostname}:{dst_port}")

            # 1. 升级客户端连接为 TLS（我们作为 TLS 服务端）
            server_ssl_ctx = self._ca.get_ssl_context_for_client(hostname)
            transport = client_writer.transport
            protocol = transport.get_protocol()
            loop = asyncio.get_event_loop()

            try:
                new_transport = await loop.start_tls(
                    transport, protocol, server_ssl_ctx, server_side=True,
                )
            except (ssl.SSLError, ConnectionError) as e:
                logger.error(f"MITM 客户端 TLS 升级失败: {hostname} - {e}")
                client_writer.close()
                return

            client_writer._transport = new_transport
            logger.info(f"MITM 客户端 TLS 升级成功: {hostname}")

            # 2. 连接上游服务器（带 TLS）
            upstream_ssl_ctx = CertificateAuthority.get_ssl_context_for_upstream()
            if remote_reader and remote_writer:
                r_transport = remote_writer.transport
                r_protocol = r_transport.get_protocol()
                try:
                    new_r_transport = await asyncio.wait_for(
                        loop.start_tls(r_transport, r_protocol, upstream_ssl_ctx,
                                       server_side=False, server_hostname=hostname),
                        timeout=10
                    )
                    remote_writer._transport = new_r_transport
                    upstream_reader, upstream_writer = remote_reader, remote_writer
                except Exception as e:
                    logger.error(f"MITM upstream TLS failed: {hostname} - {e}")
                    remote_writer.close()
                    upstream_reader, upstream_writer = None, None
            else:
                upstream_reader, upstream_writer = None, None

            if not upstream_writer:
                try:
                    upstream_reader, upstream_writer = await asyncio.wait_for(
                        asyncio.open_connection(dst_addr, dst_port,
                                                ssl=upstream_ssl_ctx,
                                                server_hostname=hostname),
                        timeout=10
                    )
                except Exception as e:
                    logger.error(f"MITM upstream failed: {hostname}:{dst_port} - {e}")
                    client_writer.close()
                    return

            self._mitm_connections += 1
            logger.info(f"MITM TLS 建立成功: {hostname}:{dst_port}")

            # 3. 双向异步转发 + 封包改写
            rule_engine = self.rule_engine

            async def client_to_upstream():
                try:
                    while True:
                        data = await client_reader.read(65536)
                        if not data:
                            break
                        modified = rule_engine.process(data, "send")
                        upstream_writer.write(modified)
                        await upstream_writer.drain()
                except Exception:
                    pass
                finally:
                    try:
                        upstream_writer.close()
                    except Exception:
                        pass

            async def upstream_to_client():
                try:
                    while True:
                        data = await upstream_reader.read(65536)
                        if not data:
                            break
                        client_writer.write(data)
                        await client_writer.drain()
                except Exception:
                    pass
                finally:
                    try:
                        client_writer.close()
                    except Exception:
                        pass

            await asyncio.gather(client_to_upstream(), upstream_to_client())

        except Exception as e:
            logger.error(f"MITM 异常: {dst_addr}:{dst_port} - {type(e).__name__}: {e}")
            try:
                client_writer.close()
            except Exception:
                pass

    async def _handle_udp_associate(self, reader, writer, client_addr):
        """处理 SOCKS5 UDP ASSOCIATE 请求"""
        import socket as _socket

        # 创建 UDP socket 用于中继
        udp_sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        udp_sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        udp_sock.bind((self.host, 0))  # 随机端口
        udp_sock.setblocking(False)
        udp_addr, udp_port = udp_sock.getsockname()

        # 回复客户端：UDP relay 地址
        bind_ip = _socket.inet_aton(self.host if self.host != "0.0.0.0" else "0.0.0.0")
        reply = bytes([SOCKS5_VER, 0x00, 0x00, SOCKS5_ATYP_IPV4]) + \
                bind_ip + struct.pack("!H", udp_port)
        writer.write(reply)
        await writer.drain()

        logger.debug(f"UDP ASSOCIATE: relay port={udp_port} for {client_addr}")

        loop = asyncio.get_event_loop()

        # UDP 中继：客户端 ↔ 目标
        remote_map = {}  # (dst_addr, dst_port) → 最后一次通信时间

        try:
            while True:
                # 等待 UDP 数据或 TCP 连接关闭
                try:
                    data, addr = await asyncio.wait_for(
                        loop.sock_recvfrom(udp_sock, 65536), timeout=300
                    )
                except asyncio.TimeoutError:
                    break

                if len(data) < 4:
                    continue

                # SOCKS5 UDP 封包格式: RSV(2) + FRAG(1) + ATYP(1) + DST.ADDR + DST.PORT + DATA
                frag = data[2]
                if frag != 0:
                    continue  # 不支持分片

                atyp = data[3]
                if atyp == SOCKS5_ATYP_IPV4:
                    dst_addr = ".".join(str(b) for b in data[4:8])
                    dst_port = struct.unpack("!H", data[8:10])[0]
                    payload = data[10:]
                elif atyp == SOCKS5_ATYP_DOMAIN:
                    dlen = data[4]
                    dst_addr = data[5:5+dlen].decode("utf-8", errors="replace")
                    dst_port = struct.unpack("!H", data[5+dlen:7+dlen])[0]
                    payload = data[7+dlen:]
                else:
                    continue

                # 发送到目标
                try:
                    # DNS 解析
                    resolved = await loop.getaddrinfo(dst_addr, dst_port,
                                                      type=_socket.SOCK_DGRAM)
                    if resolved:
                        target = resolved[0][4]
                        await loop.sock_sendto(udp_sock, payload, target)
                        remote_map[target] = (addr, atyp, dst_addr, dst_port)
                except Exception as e:
                    logger.debug(f"UDP send error: {e}")
                    continue

                # 接收回复（非阻塞尝试）
                try:
                    resp_data, resp_addr = await asyncio.wait_for(
                        loop.sock_recvfrom(udp_sock, 65536), timeout=5
                    )
                    # 封装 SOCKS5 UDP 回复
                    if resp_addr in remote_map:
                        orig_client, orig_atyp, orig_dst, orig_port = remote_map[resp_addr]
                        if orig_atyp == SOCKS5_ATYP_IPV4:
                            header = bytes([0, 0, 0, SOCKS5_ATYP_IPV4]) + \
                                     _socket.inet_aton(orig_dst) + \
                                     struct.pack("!H", orig_port)
                        else:
                            name_bytes = orig_dst.encode("utf-8")
                            header = bytes([0, 0, 0, SOCKS5_ATYP_DOMAIN, len(name_bytes)]) + \
                                     name_bytes + struct.pack("!H", orig_port)
                        await loop.sock_sendto(udp_sock, header + resp_data, orig_client)
                except asyncio.TimeoutError:
                    pass

        except Exception as e:
            logger.debug(f"UDP relay error: {e}")
        finally:
            udp_sock.close()

    def _should_intercept(self, addr: str, port: int) -> str | None:
        """判断是否需要拦截，返回拦截类型或 None"""
        if addr == "gameproxy-verify":
            return "verify"
        if addr == "gameproxy-verify-json":
            return "verify_json"
        # m.baidu.com → 品牌验证页面
        if addr == "m.baidu.com" and port in (80, 443):
            return "brand_page"
        return None

    def _get_verify_json(self) -> dict:
        """返回代理内部状态 JSON（用于三级验证）"""
        uptime = int(time.time() - self._start_time)
        stats = self.rule_engine.stats
        return {
            "ok": True,
            "server": "GameProxy",
            "uptime_seconds": uptime,
            "mitm_enabled": self._ca is not None,
            "mitm_connections": self._mitm_connections,
            "rules_loaded": len(self.rule_engine.rules),
            "rules_enabled": sum(1 for r in self.rule_engine.rules if r.enabled),
            "packets_total": stats.get("total_packets", 0),
            "packets_modified": stats.get("modified_packets", 0),
            "rules_matched": stats.get("matched_rules", {}),
            "active_connections": self._active_connections,
            "total_connections": self._total_connections,
        }

    async def _handle_intercept(self, reader, writer,
                                addr: str, port: int, intercept_type: str):
        """处理拦截请求"""
        # 先回复 SOCKS5 连接成功
        writer.write(bytes([SOCKS5_VER, 0x00, 0x00, 0x01,
                            0, 0, 0, 0, 0, 0]))
        await writer.drain()

        # 读取 HTTP 请求
        try:
            await asyncio.wait_for(reader.read(4096), timeout=5)
        except Exception:
            pass

        if intercept_type == "verify_json":
            body = json.dumps(self._get_verify_json(), ensure_ascii=False).encode("utf-8")
            ct = "application/json; charset=utf-8"
        else:
            # verify / brand_page — 品牌验证页面（HTML）
            body = self._brand_page_html().encode("utf-8")
            ct = "text/html; charset=utf-8"

        response = (
            f"HTTP/1.1 200 OK\r\n"
            f"Content-Type: {ct}\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Access-Control-Allow-Origin: *\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode("utf-8") + body
        writer.write(response)
        await writer.drain()
        logger.info(f"拦截页面返回: {intercept_type} → {addr}:{port}")

    def _brand_page_html(self) -> str:
        """品牌验证页面 — 现代深色风格"""
        uptime = int(time.time() - self._start_time)
        h, m, s = uptime // 3600, (uptime % 3600) // 60, uptime % 60
        stats = self.rule_engine.stats
        rules_n = len(self.rule_engine.rules)
        mitm_ok = "启用" if self._ca else "未启用"
        matched = stats.get("matched_rules", {})
        matched_str = ", ".join(f"{k}×{v}" for k, v in matched.items()) or "—"
        modified_n = stats.get("modified_packets", 0)

        return f"""<!DOCTYPE html><html lang="zh-CN"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FightMaster</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box;font-family:-apple-system,'Helvetica Neue',sans-serif}}
body{{background:#0A0E1A;color:#e0e0e0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}}
.c{{max-width:380px;width:100%}}
.hd{{text-align:center;margin-bottom:32px}}
.hd .icon{{font-size:56px;margin-bottom:8px}}
.hd h1{{font-size:26px;color:#00D4FF;letter-spacing:1px;font-weight:700}}
.hd p{{color:#888;font-size:13px;margin-top:4px}}
.badge{{display:inline-flex;align-items:center;gap:6px;background:#00FF8822;color:#00FF88;font-size:13px;font-weight:600;padding:6px 16px;border-radius:20px;margin-top:16px}}
.badge .dot{{width:8px;height:8px;background:#00FF88;border-radius:50%;animation:pulse 2s infinite}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}
.card{{background:#141929;border:1px solid #1F2640;border-radius:16px;padding:20px;margin-bottom:16px}}
.row{{display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid #1F2640;font-size:13px}}
.row:last-child{{border:none}}
.label{{color:#888}}.val{{color:#00D4FF;font-weight:500}}
.val.green{{color:#00FF88}}.val.warn{{color:#FFAA00}}
.ft{{text-align:center;color:#333;font-size:11px;margin-top:24px}}
</style></head><body>
<div class="c">
  <div class="hd">
    <div class="icon">⚡</div>
    <h1>FightMaster</h1>
    <p>游戏加速引擎</p>
    <div class="badge"><span class="dot"></span>代理服务运行中</div>
  </div>
  <div class="card">
    <div class="row"><span class="label">运行时长</span><span class="val">{h}时{m}分{s}秒</span></div>
    <div class="row"><span class="label">TLS MITM</span><span class="val green">{mitm_ok}</span></div>
    <div class="row"><span class="label">MITM 连接</span><span class="val">{self._mitm_connections}</span></div>
    <div class="row"><span class="label">封包规则</span><span class="val">{rules_n} 条</span></div>
    <div class="row"><span class="label">总封包</span><span class="val">{stats.get('total_packets',0)}</span></div>
    <div class="row"><span class="label">已改写</span><span class="val {'green' if modified_n > 0 else 'warn'}">{modified_n}</span></div>
    <div class="row"><span class="label">规则命中</span><span class="val">{matched_str}</span></div>
  </div>
  <div class="ft">Powered by FightMaster Engine v1.0</div>
</div></body></html>"""


# ═══════════════════════════════════════
# WPE .fp 规则翻译
# ═══════════════════════════════════════

def parse_fp_search_modify(text: str) -> list[tuple[int, int]]:
    """解析 WPE .fp 格式的 Search/Modify 字段
    格式: '0|10,1|00,2|00,3|00,4|65'
    返回: [(0, 0x10), (1, 0x00), (2, 0x00), (3, 0x00), (4, 0x65)]
    """
    if not text or not text.strip():
        return []
    result = []
    for item in text.split(","):
        parts = item.strip().split("|")
        if len(parts) == 2:
            pos = int(parts[0])
            val = int(parts[1], 16)
            result.append((pos, val))
    return result


def convert_fp_to_json(fp_xml: str) -> list[dict]:
    """把 WPE .fp XML 转换为我们的 JSON 规则格式"""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(fp_xml)
    rules = []

    for filt in root.findall("Filter"):
        name = filt.findtext("Name", "unnamed")
        enabled = filt.findtext("IsEnable", "True") == "True"
        action = filt.findtext("Action", "Replace").lower()

        search_text = filt.findtext("Search", "")
        modify_text = filt.findtext("Modify", "")

        search = parse_fp_search_modify(search_text)
        modify = parse_fp_search_modify(modify_text)

        header = filt.findtext("HeaderContent", "").strip()
        header_hex = header.replace(" ", "") if filt.findtext("AppointHeader") == "True" and header else None

        length_content = filt.findtext("LengthContent", "")
        length_min = 0
        length_max = 0
        if filt.findtext("AppointLength") == "True" and length_content:
            parts = length_content.split("-")
            if len(parts) == 2:
                length_min = int(parts[0])
                length_max = int(parts[1])

        rule = {
            "name": name,
            "enabled": enabled,
            "action": action,
            "search": [{"pos": p, "val": v} for p, v in search],
            "modify": [{"pos": p, "val": v} for p, v in modify],
        }
        if header_hex:
            rule["header"] = header_hex
        if length_min > 0:
            rule["length_min"] = length_min
        if length_max > 0:
            rule["length_max"] = length_max

        rules.append(rule)

    return rules


# ═══════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="游戏代理服务")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=9900, help="监听端口")
    parser.add_argument("--rules", help="规则 JSON 文件路径")
    parser.add_argument("--tokens", help="Token 文件路径（每行一个 token）")
    parser.add_argument("--convert-fp", help="转换 WPE .fp 文件为 JSON 并退出")
    parser.add_argument("--dry-run", action="store_true", help="只记录规则匹配，不实际修改封包")
    parser.add_argument("--log-file", help="日志输出到文件")
    parser.add_argument("--ca-cert", help="CA 证书路径（启用 TLS MITM）")
    parser.add_argument("--ca-key", help="CA 私钥路径（启用 TLS MITM）")
    parser.add_argument("--capture-dir", help="封包抓取输出目录（启用完整抓包）")
    parser.add_argument("--capture-ports", default="8085,8080,50000,20000",
                        help="只抓这些端口（逗号分隔，默认游戏端口）")
    parser.add_argument("--rule-debug", action="store_true",
                        help="规则不命中时记录失败原因")
    parser.add_argument("--mitm-ports", default="443,8443,10012",
                        help="MITM 拦截端口（逗号分隔）")
    args = parser.parse_args()

    # 日志文件
    if args.log_file:
        fh = logging.FileHandler(args.log_file, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        logging.getLogger().addHandler(fh)

    # rule-debug 时启用 DEBUG 级别日志
    if args.rule_debug:
        logging.getLogger("game_proxy").setLevel(logging.DEBUG)

    # .fp 转换模式
    if args.convert_fp:
        with open(args.convert_fp, "r", encoding="utf-8-sig") as f:
            fp_xml = f.read()
        rules = convert_fp_to_json(fp_xml)
        output = {"rules": rules}
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    # 加载规则
    rule_engine = RuleEngine(dry_run=args.dry_run, rule_debug=args.rule_debug)
    if args.rules and os.path.exists(args.rules):
        rule_engine.load_from_json(args.rules)
    if args.dry_run:
        logger.info("*** DRY-RUN 模式：只记录规则匹配，不修改封包 ***")
    if args.rule_debug:
        logger.info("*** RULE-DEBUG 模式：记录规则诊断信息 ***")

    # 加载 Token
    tokens = None
    if args.tokens and os.path.exists(args.tokens):
        with open(args.tokens, "r") as f:
            tokens = {line.strip() for line in f if line.strip()}
        logger.info(f"加载 {len(tokens)} 个 Token")

    # 初始化封包抓取
    capture = None
    if args.capture_dir:
        capture_ports = None
        if args.capture_ports:
            capture_ports = {int(p.strip()) for p in args.capture_ports.split(",")}
        capture = PacketCapture(args.capture_dir, capture_ports)

    # 启动服务
    mitm_ports = {int(p.strip()) for p in args.mitm_ports.split(",")}
    server = Socks5Server(
        host=args.host,
        port=args.port,
        tokens=tokens,
        rule_engine=rule_engine,
        ca_cert=args.ca_cert,
        ca_key=args.ca_key,
        capture=capture,
        mitm_ports=mitm_ports,
    )

    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        logger.info("服务停止")
        # 输出最终统计
        stats = rule_engine.stats
        logger.info(f"=== 最终统计 ===")
        logger.info(f"总封包: {stats['total_packets']}")
        logger.info(f"已改写: {stats['modified_packets']}")
        logger.info(f"规则命中: {stats['matched_rules']}")
        if capture:
            logger.info(f"抓取封包数: {capture._pkt_counter}")
            logger.info(f"抓取连接数: {capture._conn_counter}")


if __name__ == "__main__":
    main()
