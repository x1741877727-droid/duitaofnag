"""
tls_mitm.py — TLS MITM 证书管理模块
为每个 hostname 动态生成由本地 CA 签名的伪造证书，用于 MITM 代理。
"""

import ssl
import tempfile
import os
import threading
import datetime

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend


class CertificateAuthority:
    """管理 CA 证书，动态生成 per-hostname 伪造证书并缓存对应的 SSLContext。"""

    def __init__(self, ca_cert_path: str, ca_key_path: str):
        with open(ca_cert_path, "rb") as f:
            self._ca_cert = x509.load_pem_x509_certificate(f.read(), default_backend())

        with open(ca_key_path, "rb") as f:
            self._ca_key = serialization.load_pem_private_key(
                f.read(), password=None, backend=default_backend()
            )

        self._cache: dict[str, ssl.SSLContext] = {}
        self._lock = threading.Lock()

    def get_ssl_context_for_client(self, hostname: str) -> ssl.SSLContext:
        """
        返回服务端 SSLContext，其中包含为 hostname 动态生成的伪造证书。
        结果按 hostname 缓存，线程安全。
        """
        with self._lock:
            if hostname in self._cache:
                return self._cache[hostname]

            ctx = self._make_server_ctx(hostname)
            self._cache[hostname] = ctx
            return ctx

    def _make_server_ctx(self, hostname: str) -> ssl.SSLContext:
        """生成伪造证书并构建服务端 SSLContext。"""
        # 生成新的 RSA 密钥对
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend(),
        )

        # 构建证书
        now = datetime.datetime.now(datetime.timezone.utc)
        subject = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, hostname),
        ])
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(self._ca_cert.subject)
            .public_key(private_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName(hostname)]),
                critical=False,
            )
            .sign(self._ca_key, hashes.SHA256(), default_backend())
        )

        # 序列化为 PEM
        cert_pem = cert.public_bytes(serialization.Encoding.PEM)
        key_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )

        # ssl.SSLContext 不支持从 buffer 直接加载，需写临时文件
        cert_fd, cert_path = tempfile.mkstemp(suffix=".crt")
        key_fd, key_path = tempfile.mkstemp(suffix=".key")
        try:
            os.write(cert_fd, cert_pem)
            os.close(cert_fd)
            os.write(key_fd, key_pem)
            os.close(key_fd)

            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
        finally:
            os.unlink(cert_path)
            os.unlink(key_path)

        return ctx

    @staticmethod
    def get_ssl_context_for_upstream() -> ssl.SSLContext:
        """
        返回客户端 SSLContext（连接上游服务器用），禁用证书验证。
        """
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
