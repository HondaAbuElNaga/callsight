"""TLS trust for the PBX (spec 2026-11-14-hardening, decision 6). Never silently turns verification off.

- YEASTAR_CA_FILE=/app/config/pbx-ca.pem: httpx verifies the PBX against this file (the PBX's own self-signed
  certificate, saved with `python -m callsight pbx-cert --fetch` + `--accept <sha256>`). A missing file is an error,
  not a fallback. YEASTAR_TLS_CHECK_HOSTNAME=false only when the certificate does not name the PBX's LAN address
  (the chain is still checked against that one certificate).
- YEASTAR_CERT_SHA256: extra fingerprint pin, checked before every login. Only allowed together with
  YEASTAR_CA_FILE: the pin is checked on a separate probe connection, so on its own (httpx verify=False) it would
  give no protection against a man in the middle on the real connection. Pin without CA file = startup error.
- Neither set and YEASTAR_VERIFY_SSL=false: today's setting; a warning is logged at every start.
"""
from __future__ import annotations

import hashlib
import logging
import socket
import ssl
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from .base import PbxError

log = logging.getLogger(__name__)


class CertMismatch(PbxError):
    """The PBX presented a different certificate than the pinned/accepted one."""


def verify_setting(ca_file: str | None, verify_ssl: bool, check_hostname: bool = True,
                   cert_sha256: str | None = None) -> ssl.SSLContext | bool:
    """What to pass as httpx `verify=`."""
    if cert_sha256 and not ca_file:
        raise RuntimeError("YEASTAR_CERT_SHA256 alone does not protect the API secret: save the certificate with "
                           "`pbx-cert --accept <sha256>` and set YEASTAR_CA_FILE as well")
    if ca_file:
        path = Path(ca_file)
        if not path.is_file():
            raise RuntimeError("YEASTAR_CA_FILE is set but the file does not exist (run pbx-cert --fetch/--accept)")
        ctx = ssl.create_default_context(cafile=str(path))
        ctx.check_hostname = check_hostname            # CERT_REQUIRED stays on: only certs from this file pass
        return ctx
    if not verify_ssl:
        log.warning("PBX TLS certificate is NOT verified (YEASTAR_VERIFY_SSL=false). "
                    "Set YEASTAR_CA_FILE (python -m callsight pbx-cert --fetch) to fix this.")
    return verify_ssl


def normalize_fp(text: str) -> str:
    """'aa:bb:..' / 'AABB..' / 'sha256 Fingerprint=AA:BB' -> 'AA:BB:..' (64 hex digits)."""
    raw = text.split("=")[-1].replace(":", "").replace(" ", "").strip().upper()
    if len(raw) != 64 or any(c not in "0123456789ABCDEF" for c in raw):
        raise ValueError("not a SHA-256 fingerprint (64 hex digits)")
    return ":".join(raw[i:i + 2] for i in range(0, 64, 2))


def fingerprint(der: bytes) -> str:
    return normalize_fp(hashlib.sha256(der).hexdigest())


def host_port(base_url: str) -> tuple[str, int]:
    u = urlparse(base_url)
    if u.scheme != "https" or not u.hostname:
        raise ValueError("YEASTAR_BASE_URL must be an https:// URL for certificate checks")
    return u.hostname, u.port or 443


def fetch_cert(host: str, port: int, timeout: float = 10.0) -> bytes:
    """The certificate the server presents (DER). Reads it only: nothing is sent after the TLS handshake."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE                  # we want to SEE the cert; trust is decided by the fingerprint
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    if not der:
        raise PbxError("the PBX presented no certificate")
    return der


def check_pin(base_url: str, expected: str, fetch: Callable[[str, int], bytes] = fetch_cert) -> None:
    host, port = host_port(base_url)
    got = fingerprint(fetch(host, port))
    if got != normalize_fp(expected):
        raise CertMismatch("PBX certificate fingerprint differs from YEASTAR_CERT_SHA256: not logging in")


def accept(base_url: str, expected: str, out: Path, fetch: Callable[[str, int], bytes] = fetch_cert) -> str:
    """Save the presented certificate as PEM only when it matches the fingerprint the user typed."""
    host, port = host_port(base_url)
    der = fetch(host, port)
    got = fingerprint(der)
    if got != normalize_fp(expected):
        raise CertMismatch("the fingerprint you typed does not match the certificate the PBX presents: nothing saved")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(ssl.DER_cert_to_PEM_cert(der), encoding="ascii")
    tmp.replace(out)
    return got
