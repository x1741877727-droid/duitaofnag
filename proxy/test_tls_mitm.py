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
    assert ctx.check_hostname is False


def test_cert_caching():
    from tls_mitm import CertificateAuthority
    ca = CertificateAuthority(CA_CERT, CA_KEY)
    ctx1 = ca.get_ssl_context_for_client("test.qq.com")
    ctx2 = ca.get_ssl_context_for_client("test.qq.com")
    assert ctx1 is ctx2
