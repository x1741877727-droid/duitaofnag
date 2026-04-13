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
        if self.header_match is not None:
            if len(data) < len(self.header_match):
                return False
            if data[:len(self.header_match)] != self.header_match:
                return False

        # 字节模式匹配
        for pos, val in self.search:
            if pos >= len(data):
                return False
            if data[pos] != val:
                return False

        return True

    def apply(self, data: bytes) -> bytes:
        """应用修改"""
        if self.action == "change":
            # 整包替换：modify 定义了完整的新包
            max_pos = max(pos for pos, _ in self.modify) if self.modify else 0
            new_data = bytearray(max(len(data), max_pos + 1))
            # 先复制原数据
            new_data[:len(data)] = data
            # 应用修改
            for pos, val in self.modify:
                if pos < len(new_data):
                    new_data[pos] = val
            return bytes(new_data[:max_pos + 1])
        else:
            # replace：只修改指定位置
            buf = bytearray(data)
            for pos, val in self.modify:
                if pos < len(buf):
                    buf[pos] = val
            return bytes(buf)


class RuleEngine:
    """封包改写规则引擎"""

    def __init__(self, dry_run: bool = False):
        self.rules: list[PacketRule] = []
        self.dry_run = dry_run  # True = 只记录匹配，不实际修改
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

        # 记录前几个字节用于调试
        if len(data) >= 4 and self._stats["total_packets"] <= 200:
            header_hex = data[:min(16, len(data))].hex()
            if self._stats["total_packets"] % 50 == 1:
                logger.info(f"封包样本 ({direction}) len={len(data)}: {header_hex}")

        for rule in self.rules:
            if rule.matches(data):
                self._stats["matched_rules"][rule.name] = \
                    self._stats["matched_rules"].get(rule.name, 0) + 1

                if self.dry_run:
                    logger.info(f"[DRY-RUN] 规则 [{rule.name}] 命中 ({direction}), "
                                f"len={len(data)}, head={data[:8].hex()}")
                    return data  # 不修改

                modified = rule.apply(data)
                if modified != data:
                    self._stats["modified_packets"] += 1
                    logger.info(f"规则 [{rule.name}] 命中并修改 ({direction}), "
                                f"len={len(data)}→{len(modified)}, head={data[:8].hex()}")
                    return modified
        return data

    @property
    def stats(self) -> dict:
        return dict(self._stats)


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


class Socks5Server:
    """SOCKS5 代理服务器，集成 TLS MITM + 封包改写"""

    def __init__(self, host: str = "0.0.0.0", port: int = 9900,
                 tokens: set[str] | None = None,
                 rule_engine: RuleEngine | None = None,
                 ca_cert: str | None = None,
                 ca_key: str | None = None):
        self.host = host
        self.port = port
        self.tokens = tokens  # None = 不鉴权
        self.rule_engine = rule_engine or RuleEngine()
        self._active_connections = 0
        self._total_connections = 0
        self._mitm_connections = 0      # MITM 成功次数
        self._start_time = time.time()
        self._ca = None

        # 初始化 TLS MITM
        if ca_cert and ca_key and os.path.exists(ca_cert) and os.path.exists(ca_key):
            try:
                from tls_mitm import CertificateAuthority
                self._ca = CertificateAuthority(ca_cert, ca_key)
                logger.info(f"TLS MITM: 已启用 (CA: {ca_cert})")
            except Exception as e:
                logger.warning(f"TLS MITM 初始化失败: {e}, 将只做纯转发")

    async def start(self):
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

        async with server:
            await server.serve_forever()

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
            # JustTrustMe 在客户端绕过 SSL Pinning，所以 MITM 安全
            if self._ca and dst_port in (443, 8443, 10012):
                # 关闭预连接的远程连接，MITM 会自己连
                await self._relay_mitm(reader, writer, dst_addr, dst_port, remote_reader, remote_writer)
            else:
                await self._relay(reader, writer, remote_reader, remote_writer,
                                  dst_addr, dst_port)

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

    async def _relay(self, client_reader, client_writer,
                     remote_reader, remote_writer,
                     dst_addr: str, dst_port: int):
        """双向转发数据，对发送方向应用封包改写规则"""

        async def client_to_remote():
            """客户端 → 远程服务器（游戏发出的包，应用改写规则）"""
            try:
                while True:
                    data = await client_reader.read(65536)
                    if not data:
                        break
                    # 对发送到游戏服务器的封包应用规则
                    modified = self.rule_engine.process(data, direction="send")
                    remote_writer.write(modified)
                    await remote_writer.drain()
            except Exception:
                pass
            finally:
                try:
                    remote_writer.close()
                except Exception:
                    pass

        async def remote_to_client():
            """远程服务器 → 客户端（游戏收到的包，透传）"""
            try:
                while True:
                    data = await remote_reader.read(65536)
                    if not data:
                        break
                    # 服务器返回的包暂不修改（如需要可以加 direction="recv" 规则）
                    client_writer.write(data)
                    await client_writer.drain()
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
            # 0. peek 第一个包的内容（用 transport 底层 socket peek）
            # Direct TLS for 443 ports
            is_tls = True
            if False:
                # 非 TLS 私有协议 → 直连上游 + 封包改写
                logger.info(f"游戏协议: {hostname}:{dst_port}, "
                            f"peek={peek_data[:8].hex() if peek_data else 'empty'}")
                try:
                    r_reader, r_writer = await asyncio.wait_for(
                        asyncio.open_connection(dst_addr, dst_port), timeout=10
                    )
                except Exception as e:
                    logger.debug(f"游戏协议连接失败: {dst_addr}:{dst_port} - {e}")
                    client_writer.close()
                    return
                await self._relay(client_reader, client_writer,
                                  r_reader, r_writer, dst_addr, dst_port)
                return

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
        # m.baidu.com → 品牌验证页面（HTTP 80 端口）
        if addr == "m.baidu.com" and port == 80:
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

        if intercept_type == "verify":
            body = json.dumps(self._get_verify_json(), ensure_ascii=False).encode("utf-8")
            ct = "application/json; charset=utf-8"
        else:
            # brand_page — 品牌验证页面
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
    args = parser.parse_args()

    # 日志文件
    if args.log_file:
        fh = logging.FileHandler(args.log_file, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        logging.getLogger().addHandler(fh)

    # .fp 转换模式
    if args.convert_fp:
        with open(args.convert_fp, "r", encoding="utf-8-sig") as f:
            fp_xml = f.read()
        rules = convert_fp_to_json(fp_xml)
        output = {"rules": rules}
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    # 加载规则
    rule_engine = RuleEngine(dry_run=args.dry_run)
    if args.rules and os.path.exists(args.rules):
        rule_engine.load_from_json(args.rules)
    if args.dry_run:
        logger.info("*** DRY-RUN 模式：只记录规则匹配，不修改封包 ***")

    # 加载 Token
    tokens = None
    if args.tokens and os.path.exists(args.tokens):
        with open(args.tokens, "r") as f:
            tokens = {line.strip() for line in f if line.strip()}
        logger.info(f"加载 {len(tokens)} 个 Token")

    # 启动服务
    server = Socks5Server(
        host=args.host,
        port=args.port,
        tokens=tokens,
        rule_engine=rule_engine,
        ca_cert=args.ca_cert,
        ca_key=args.ca_key,
    )

    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        logger.info("服务停止")


if __name__ == "__main__":
    main()
