"""
game_proxy.py ?????????g????
??? CCProxy + Charles + WPE ?????

?????
1. SOCKS5 ?g??????? Token ?????
2. TLS MITM????????????? ????????
3. TCP ???????????WPE .fp ?????
4. HTTP ?????.baidu.com ??????>???

?????
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

# ?????????????? sys.path?????import tls_mitm??
_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("game_proxy")


# ???????????????????????????????????????????????????????????
# ????????????
# ???????????????????????????????????????????????????????????

@dataclass
class PacketRule:
    """?????????????""
    name: str
    enabled: bool = True
    # ????'?
    search: list[tuple[int, int]] = field(default_factory=list)  # [(position, byte_value), ...]
    # ??????
    modify: list[tuple[int, int]] = field(default_factory=list)  # [(position, new_value), ...]
    # ??????????
    header_match: bytes | None = None       # ????????
    length_min: int = 0                      # ???????
    length_max: int = 0                      # ???????(0=???)
    action: str = "replace"                  # replace=?????????, change=??????

    def matches(self, data: bytes) -> bool:
        """?????????????????"""
        if not self.enabled:
            return False

        # ??????
        if self.length_min > 0 and len(data) < self.length_min:
            return False
        if self.length_max > 0 and len(data) > self.length_max:
            return False

        # header ???
        if self.header_match is not None:
            if len(data) < len(self.header_match):
                return False
            if data[:len(self.header_match)] != self.header_match:
                return False

        # ?????????
        for pos, val in self.search:
            if pos >= len(data):
                return False
            if data[pos] != val:
                return False

        return True

    def apply(self, data: bytes) -> bytes:
        """??????"""
        if self.action == "change":
            # ????????odify ????????????
            max_pos = max(pos for pos, _ in self.modify) if self.modify else 0
            new_data = bytearray(max(len(data), max_pos + 1))
            # ?????????
            new_data[:len(data)] = data
            # ??????
            for pos, val in self.modify:
                if pos < len(new_data):
                    new_data[pos] = val
            return bytes(new_data[:max_pos + 1])
        else:
            # replace????????????
            buf = bytearray(data)
            for pos, val in self.modify:
                if pos < len(buf):
                    buf[pos] = val
            return bytes(buf)


class RuleEngine:
    """????????????"""

    def __init__(self, dry_run: bool = False):
        self.rules: list[PacketRule] = []
        self.dry_run = dry_run  # True = ?????????????????
        self._stats = {"total_packets": 0, "modified_packets": 0, "matched_rules": {}}

    def load_from_json(self, path: str):
        """??JSON ?????????"""
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
        logger.info(f"??? {len(self.rules)} ????)

    def process(self, data: bytes, direction: str = "send") -> bytes:
        """???????????????????????"""
        self._stats["total_packets"] += 1

        # ?????????????????
        if len(data) >= 4 and self._stats["total_packets"] <= 200:
            header_hex = data[:min(16, len(data))].hex()
            if self._stats["total_packets"] % 50 == 1:
                logger.info(f"?????? ({direction}) len={len(data)}: {header_hex}")

        for rule in self.rules:
            if rule.matches(data):
                self._stats["matched_rules"][rule.name] = \
                    self._stats["matched_rules"].get(rule.name, 0) + 1

                if self.dry_run:
                    logger.info(f"[DRY-RUN] ??? [{rule.name}] ??? ({direction}), "
                                f"len={len(data)}, head={data[:8].hex()}")
                    return data  # ?????

                modified = rule.apply(data)
                if modified != data:
                    self._stats["modified_packets"] += 1
                    logger.info(f"??? [{rule.name}] ????????({direction}), "
                                f"len={len(data)}??len(modified)}, head={data[:8].hex()}")
                    return modified
        return data

    @property
    def stats(self) -> dict:
        return dict(self._stats)


# ???????????????????????????????????????????????????????????
# SOCKS5 ?g??????
# ???????????????????????????????????????????????????????????

# SOCKS5 ???
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
    """SOCKS5 ?g?????????? TLS MITM + ??????"""

    def __init__(self, host: str = "0.0.0.0", port: int = 9900,
                 tokens: set[str] | None = None,
                 rule_engine: RuleEngine | None = None,
                 ca_cert: str | None = None,
                 ca_key: str | None = None):
        self.host = host
        self.port = port
        self.tokens = tokens  # None = ?????
        self.rule_engine = rule_engine or RuleEngine()
        self._active_connections = 0
        self._total_connections = 0
        self._mitm_connections = 0      # MITM ??????
        self._start_time = time.time()
        self._ca = None

        # ?????TLS MITM
        if ca_cert and ca_key and os.path.exists(ca_cert) and os.path.exists(ca_key):
            try:
                from tls_mitm import CertificateAuthority
                self._ca = CertificateAuthority(ca_cert, ca_key)
                logger.info(f"TLS MITM: ?????(CA: {ca_cert})")
            except Exception as e:
                logger.warning(f"TLS MITM ???????? {e}, ?????????")

    async def start(self):
        server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )
        addr = server.sockets[0].getsockname()
        logger.info(f"SOCKS5 ?g????: {addr[0]}:{addr[1]}")
        if self.tokens:
            logger.info(f"???: ?????{len(self.tokens)} ??Token")
        else:
            logger.info("???: ???????????")
        logger.info(f"???: {len(self.rule_engine.rules)} ??)

        async with server:
            await server.serve_forever()

    async def _handle_client(self, reader: asyncio.StreamReader,
                             writer: asyncio.StreamWriter):
        """???????SOCKS5 ????????""
        client_addr = writer.get_extra_info('peername')
        self._active_connections += 1
        self._total_connections += 1

        try:
            # ???? 1. ??????????????????
            header = await asyncio.wait_for(reader.read(2), timeout=10)
            if len(header) < 2 or header[0] != SOCKS5_VER:
                return

            n_methods = header[1]
            methods = await asyncio.wait_for(reader.read(n_methods), timeout=10)

            if self.tokens:
                # ??????????????
                writer.write(bytes([SOCKS5_VER, SOCKS5_AUTH_USERPASS]))
                await writer.drain()

                # ???? 2. ??????????? ????
                auth_ver = await asyncio.wait_for(reader.read(1), timeout=10)
                if not auth_ver or auth_ver[0] != 0x01:
                    return

                ulen = (await reader.read(1))[0]
                username = (await reader.read(ulen)).decode("utf-8", errors="replace")
                plen = (await reader.read(1))[0]
                password = (await reader.read(plen)).decode("utf-8", errors="replace")

                # Token ?????????????????token
                token = password or username
                if token not in self.tokens:
                    logger.warning(f"??????: {client_addr} token={token[:8]}...")
                    writer.write(bytes([0x01, 0x01]))  # ???
                    await writer.drain()
                    return

                writer.write(bytes([0x01, 0x00]))  # ???
                await writer.drain()
            else:
                # ?????
                writer.write(bytes([SOCKS5_VER, SOCKS5_AUTH_NONE]))
                await writer.drain()

            # ???? 3. ?????? ????
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

            # ???? 4. UDP ASSOCIATE ????
            if cmd == SOCKS5_CMD_UDP_ASSOCIATE:
                await self._handle_udp_associate(reader, writer, client_addr)
                return

            if cmd != SOCKS5_CMD_CONNECT:
                writer.write(bytes([SOCKS5_VER, 0x07, 0x00, 0x01,
                                    0, 0, 0, 0, 0, 0]))
                await writer.drain()
                return

            # ???? 5. ??????????????????????? ????
            intercept_type = self._should_intercept(dst_addr, dst_port)
            if intercept_type:
                await self._handle_intercept(reader, writer, dst_addr, dst_port, intercept_type)
                return

            # ???? 6. ???????????????
            try:
                remote_reader, remote_writer = await asyncio.wait_for(
                    asyncio.open_connection(dst_addr, dst_port),
                    timeout=10
                )
            except Exception as e:
                logger.debug(f"??????: {dst_addr}:{dst_port} - {e}")
                writer.write(bytes([SOCKS5_VER, 0x05, 0x00, 0x01,
                                    0, 0, 0, 0, 0, 0]))
                await writer.drain()
                return

            # ?????????
            writer.write(bytes([SOCKS5_VER, 0x00, 0x00, 0x01,
                                0, 0, 0, 0, 0, 0]))
            await writer.drain()

            # ???? 7. TLS MITM ?????? ????
            # JustTrustMe ????????? SSL Pinning?????MITM ???
            if self._ca and dst_port in (443, 8443, 10012):
                # ????????????????ITM ??????
                await self._relay_mitm(reader, writer, dst_addr, dst_port, remote_reader, remote_writer)
            else:
                await self._relay(reader, writer, remote_reader, remote_writer,
                                  dst_addr, dst_port)

        except asyncio.TimeoutError:
            pass
        except ConnectionError:
            pass
        except Exception as e:
            logger.debug(f"??????: {client_addr} - {e}")
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
        """???????????????????????????????""

        async def client_to_remote():
            """????????????????????????????????????"""
            try:
                while True:
                    data = await client_reader.read(65536)
                    if not data:
                        break
                    # ????????????????????????
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
            """???????????????????????????????""
            try:
                while True:
                    data = await remote_reader.read(65536)
                    if not data:
                        break
                    # ??????????????????????????? direction="recv" ?????
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
        """TLS MITM ???????????

        ??peek ??? TLS vs ??TLS??
        - TLS ??start_tls() MITM ?e? + ??????
        - ??TLS ????? + ??????????????????
        """
        from tls_mitm import CertificateAuthority

        hostname = dst_addr

        try:
            # 0. peek ??????????????transport ??? socket peek??
            # Direct TLS - no peek needed
            is_tls = True
            if False:
                # ??TLS ?????? ???????? + ??????
                logger.info(f"??????: {hostname}:{dst_port}, "
                            f"peek={peek_data[:8].hex() if peek_data else 'empty'}")
                try:
                    r_reader, r_writer = await asyncio.wait_for(
                        asyncio.open_connection(dst_addr, dst_port), timeout=10
                    )
                except Exception as e:
                    logger.debug(f"????????????: {dst_addr}:{dst_port} - {e}")
                    client_writer.close()
                    return
                await self._relay(client_reader, client_writer,
                                  r_reader, r_writer, dst_addr, dst_port)
                return

            # TLS ??? ??MITM
            logger.info(f"MITM: {hostname}:{dst_port}")

            # 1. ???????????? TLS????????TLS ??????
            server_ssl_ctx = self._ca.get_ssl_context_for_client(hostname)
            transport = client_writer.transport
            protocol = transport.get_protocol()
            loop = asyncio.get_event_loop()

            try:
                new_transport = await loop.start_tls(
                    transport, protocol, server_ssl_ctx, server_side=True,
                )
            except (ssl.SSLError, ConnectionError) as e:
                logger.error(f"MITM ?????TLS ??????: {hostname} - {e}")
                client_writer.close()
                return

            client_writer._transport = new_transport
            logger.info(f"MITM ?????TLS ??????: {hostname}")

            # 2. ??????????????TLS??
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
                    logger.error(f"MITM upstream TLS upgrade failed: {hostname} - {e}")
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
                    logger.error(f"MITM upstream connect failed: {hostname}:{dst_port} - {e}")
                    client_writer.close()
                    return

            self._mitm_connections += 1
            logger.info(f"MITM TLS ??????: {hostname}:{dst_port}")

            # 3. ????????? + ??????
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
            logger.error(f"MITM ???: {dst_addr}:{dst_port} - {type(e).__name__}: {e}")
            try:
                client_writer.close()
            except Exception:
                pass

    async def _handle_udp_associate(self, reader, writer, client_addr):
        """??? SOCKS5 UDP ASSOCIATE ???"""
        import socket as _socket

        # ??? UDP socket ??????
        udp_sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        udp_sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        udp_sock.bind((self.host, 0))  # ??????
        udp_sock.setblocking(False)
        udp_addr, udp_port = udp_sock.getsockname()

        # ?????????UDP relay ???
        bind_ip = _socket.inet_aton(self.host if self.host != "0.0.0.0" else "0.0.0.0")
        reply = bytes([SOCKS5_VER, 0x00, 0x00, SOCKS5_ATYP_IPV4]) + \
                bind_ip + struct.pack("!H", udp_port)
        writer.write(reply)
        await writer.drain()

        logger.debug(f"UDP ASSOCIATE: relay port={udp_port} for {client_addr}")

        loop = asyncio.get_event_loop()

        # UDP ????????? ?????
        remote_map = {}  # (dst_addr, dst_port) ???????????????

        try:
            while True:
                # ??? UDP ?????TCP ??????
                try:
                    data, addr = await asyncio.wait_for(
                        loop.sock_recvfrom(udp_sock, 65536), timeout=300
                    )
                except asyncio.TimeoutError:
                    break

                if len(data) < 4:
                    continue

                # SOCKS5 UDP ??????: RSV(2) + FRAG(1) + ATYP(1) + DST.ADDR + DST.PORT + DATA
                frag = data[2]
                if frag != 0:
                    continue  # ????????

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

                # ????????
                try:
                    # DNS ?f?
                    resolved = await loop.getaddrinfo(dst_addr, dst_port,
                                                      type=_socket.SOCK_DGRAM)
                    if resolved:
                        target = resolved[0][4]
                        await loop.sock_sendto(udp_sock, payload, target)
                        remote_map[target] = (addr, atyp, dst_addr, dst_port)
                except Exception as e:
                    logger.debug(f"UDP send error: {e}")
                    continue

                # ?????????????????
                try:
                    resp_data, resp_addr = await asyncio.wait_for(
                        loop.sock_recvfrom(udp_sock, 65536), timeout=5
                    )
                    # ??? SOCKS5 UDP ???
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
        """?????????????????????????None"""
        if addr == "gameproxy-verify":
            return "verify"
        # m.baidu.com ?????????????TTP 80 ?????
        if addr == "m.baidu.com" and port == 80:
            return "brand_page"
        return None

    def _get_verify_json(self) -> dict:
        """????g????????JSON????????????"""
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
        """?????????"""
        # ?????SOCKS5 ??????
        writer.write(bytes([SOCKS5_VER, 0x00, 0x00, 0x01,
                            0, 0, 0, 0, 0, 0]))
        await writer.drain()

        # ??? HTTP ???
        try:
            await asyncio.wait_for(reader.read(4096), timeout=5)
        except Exception:
            pass

        if intercept_type == "verify":
            body = json.dumps(self._get_verify_json(), ensure_ascii=False).encode("utf-8")
            ct = "application/json; charset=utf-8"
        else:
            # brand_page ???????????
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
        logger.info(f"?????????: {intercept_type} ??{addr}:{port}")

    def _brand_page_html(self) -> str:
        """????????? ???????????"""
        uptime = int(time.time() - self._start_time)
        h, m, s = uptime // 3600, (uptime % 3600) // 60, uptime % 60
        stats = self.rule_engine.stats
        rules_n = len(self.rule_engine.rules)
        mitm_ok = "???" if self._ca else "?????
        matched = stats.get("matched_rules", {})
        matched_str = ", ".join(f"{k}?{v}" for k, v in matched.items()) or "??
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
    <div class="icon">??/div>
    <h1>FightMaster</h1>
    <p>??????????/p>
    <div class="badge"><span class="dot"></span>?g?????????/div>
  </div>
  <div class="card">
    <div class="row"><span class="label">??????</span><span class="val">{h}??m}??s}??/span></div>
    <div class="row"><span class="label">TLS MITM</span><span class="val green">{mitm_ok}</span></div>
    <div class="row"><span class="label">MITM ???</span><span class="val">{self._mitm_connections}</span></div>
    <div class="row"><span class="label">??????</span><span class="val">{rules_n} ??/span></div>
    <div class="row"><span class="label">?????/span><span class="val">{stats.get('total_packets',0)}</span></div>
    <div class="row"><span class="label">?????/span><span class="val {'green' if modified_n > 0 else 'warn'}">{modified_n}</span></div>
    <div class="row"><span class="label">??????</span><span class="val">{matched_str}</span></div>
  </div>
  <div class="ft">Powered by FightMaster Engine v1.0</div>
</div></body></html>"""


# ???????????????????????????????????????????????????????????
# WPE .fp ??????
# ???????????????????????????????????????????????????????????

def parse_fp_search_modify(text: str) -> list[tuple[int, int]]:
    """?f? WPE .fp ?????Search/Modify ???
    ???: '0|10,1|00,2|00,3|00,4|65'
    ???: [(0, 0x10), (1, 0x00), (2, 0x00), (3, 0x00), (4, 0x65)]
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
    """??WPE .fp XML ????????? JSON ??????"""
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


# ???????????????????????????????????????????????????????????
# CLI ???
# ???????????????????????????????????????????????????????????

def main():
    parser = argparse.ArgumentParser(description="????g????")
    parser.add_argument("--host", default="0.0.0.0", help="??????")
    parser.add_argument("--port", type=int, default=9900, help="??????")
    parser.add_argument("--rules", help="??? JSON ??????")
    parser.add_argument("--tokens", help="Token ??????????????token??)
    parser.add_argument("--convert-fp", help="??? WPE .fp ?????JSON ??????)
    parser.add_argument("--dry-run", action="store_true", help="???????????????????????)
    parser.add_argument("--log-file", help="???????????)
    parser.add_argument("--ca-cert", help="CA ???????????TLS MITM??)
    parser.add_argument("--ca-key", help="CA ???????????TLS MITM??)
    args = parser.parse_args()

    # ??????
    if args.log_file:
        fh = logging.FileHandler(args.log_file, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        logging.getLogger().addHandler(fh)

    # .fp ??????
    if args.convert_fp:
        with open(args.convert_fp, "r", encoding="utf-8-sig") as f:
            fp_xml = f.read()
        rules = convert_fp_to_json(fp_xml)
        output = {"rules": rules}
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    # ??????
    rule_engine = RuleEngine(dry_run=args.dry_run)
    if args.rules and os.path.exists(args.rules):
        rule_engine.load_from_json(args.rules)
    if args.dry_run:
        logger.info("*** DRY-RUN ???????????????????????? ***")

    # ??? Token
    tokens = None
    if args.tokens and os.path.exists(args.tokens):
        with open(args.tokens, "r") as f:
            tokens = {line.strip() for line in f if line.strip()}
        logger.info(f"??? {len(tokens)} ??Token")

    # ??????
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
        logger.info("??????")


if __name__ == "__main__":
    main()

