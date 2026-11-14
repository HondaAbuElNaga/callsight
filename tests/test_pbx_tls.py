"""PBX certificate trust (decision 6): CA-file client config, fingerprint pin before login, accept only on match.
No network: the certificate fetch is a fake."""
import hashlib
import logging
import ssl

import certifi
import httpx
import pytest

from callsight.pbx import tls
from callsight.pbx.tls import CertMismatch
from callsight.pbx.yeastar import YeastarAdapter
from test_yeastar_adapter import FakePbx

DER = b"0\x82fake-certificate-bytes"
FP = ":".join(f"{b:02X}" for b in hashlib.sha256(DER).digest())
URL = "https://192.168.50.151:8088"


def fake_fetch(host, port):
    assert (host, port) == ("192.168.50.151", 8088)
    return DER


def test_ca_file_gives_a_verifying_context():
    ctx = tls.verify_setting(certifi.where(), verify_ssl=False)
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname       # CA file wins over VERIFY_SSL=false
    ctx = tls.verify_setting(certifi.where(), verify_ssl=True, check_hostname=False)
    assert ctx.verify_mode == ssl.CERT_REQUIRED and not ctx.check_hostname


def test_missing_ca_file_is_an_error_not_a_fallback(tmp_path):
    with pytest.raises(RuntimeError, match="does not exist"):
        tls.verify_setting(str(tmp_path / "nope.pem"), verify_ssl=False)


def test_no_ca_file_keeps_old_setting_with_a_warning(caplog):
    with caplog.at_level(logging.WARNING):
        assert tls.verify_setting(None, verify_ssl=False) is False
    assert "NOT verified" in caplog.text
    assert tls.verify_setting(None, verify_ssl=True) is True


def test_adapter_uses_the_context():
    ctx = tls.verify_setting(certifi.where(), verify_ssl=True)
    a = YeastarAdapter(URL, "id", "secret", verify_ssl=ctx)
    assert a.http is not None
    a.close()


@pytest.mark.parametrize("text", [FP, FP.lower(), FP.replace(":", ""), f"sha256 Fingerprint={FP}"])
def test_fingerprint_formats(text):
    assert tls.normalize_fp(text) == FP


@pytest.mark.parametrize("bad", ["", "AB:CD", "Z" * 64, FP + "00"])
def test_bad_fingerprints_refused(bad):
    with pytest.raises(ValueError):
        tls.normalize_fp(bad)


def test_pin_check():
    tls.check_pin(URL, FP.lower(), fetch=fake_fetch)
    with pytest.raises(CertMismatch):
        tls.check_pin(URL, "AB" * 32, fetch=fake_fetch)
    with pytest.raises(ValueError):
        tls.host_port("http://192.168.50.151:8088")


def test_accept_saves_only_on_match(tmp_path):
    out = tmp_path / "pbx-ca.pem"
    with pytest.raises(CertMismatch):
        tls.accept(URL, "AB" * 32, out, fetch=fake_fetch)
    assert not out.exists()
    assert tls.accept(URL, FP, out, fetch=fake_fetch) == FP
    assert ssl.PEM_cert_to_DER_cert(out.read_text(encoding="ascii")) == DER


def test_pin_mismatch_stops_login_before_the_secret_is_sent():
    fake = FakePbx()
    calls = []

    def pin(url, expected):
        calls.append(url)
        raise CertMismatch("different certificate")
    a = YeastarAdapter(URL, "id", "secret", http=httpx.Client(transport=httpx.MockTransport(fake.handler)),
                       sleep=lambda s: None, cert_sha256=FP, check_pin=pin)
    with pytest.raises(CertMismatch):
        a._login()
    assert calls == [URL] and fake.logins == 0


def test_pin_ok_then_login():
    fake = FakePbx()
    a = YeastarAdapter(URL, "id", "secret", http=httpx.Client(transport=httpx.MockTransport(fake.handler)),
                       sleep=lambda s: None, cert_sha256=FP, check_pin=lambda u, e: tls.check_pin(u, e, fake_fetch))
    a._login()
    assert fake.logins == 1 and a.access_token
