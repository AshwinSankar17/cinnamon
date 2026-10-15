"""Standard Web Push (no Firebase): VAPID (RFC 8292) + aes128gcm payload encryption (RFC 8291 / RFC 8188).

Brain holds one P-256 VAPID key at ~/.brain/vapid.pem (0600). The PWA subscribes with its public key and sends
Brain its subscription {endpoint, keys:{p256dh, auth}}. Each notification is encrypted for that browser, so the
push service (Google, Mozilla, Apple) only relays ciphertext. Built on `cryptography` + `httpx` (no new deps).
"""
from __future__ import annotations

import json
import logging
import os
import struct
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .identity import b64d, b64e

log = logging.getLogger("brain.webpush")
RECORD_SIZE = 4096


def _hkdf(salt: bytes, ikm: bytes, info: bytes, n: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=n, salt=salt, info=info).derive(ikm)


def _pub_bytes(k: ec.EllipticCurvePublicKey) -> bytes:
    return k.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def encrypt(payload: bytes, p256dh_b64: str, auth_b64: str, *, salt: bytes | None = None,
            sender: ec.EllipticCurvePrivateKey | None = None) -> bytes:
    """RFC 8291 aes128gcm body: header(salt|rs|idlen|sender pub) + one encrypted record."""
    ua_pub = b64d(p256dh_b64)
    auth = b64d(auth_b64)
    sender = sender or ec.generate_private_key(ec.SECP256R1())
    salt = salt or os.urandom(16)
    as_pub = _pub_bytes(sender.public_key())
    shared = sender.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub))
    ikm = _hkdf(auth, shared, b"WebPush: info\x00" + ua_pub + as_pub, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    if len(payload) > RECORD_SIZE - 17:
        raise ValueError("push payload too large for one record")
    record = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)  # 0x02 = last-record padding delimiter
    return salt + struct.pack(">IB", RECORD_SIZE, len(as_pub)) + as_pub + record


class WebPush:
    def __init__(self, key_path: Path, subject: str = "mailto:brain@cluster-brain.invalid"):
        self.key_path, self.subject = key_path, subject
        if key_path.exists():
            self.key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        else:
            self.key = ec.generate_private_key(ec.SECP256R1())
            key_path.write_bytes(self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                        serialization.NoEncryption()))
            os.chmod(key_path, 0o600)

    @property
    def public_key(self) -> str:
        """applicationServerKey for PushManager.subscribe (b64url, uncompressed P-256 point)."""
        return b64e(_pub_bytes(self.key.public_key()))

    def _vapid(self, endpoint: str) -> str:
        u = urlsplit(endpoint)
        header = b64e(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
        claims = b64e(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600,
                                  "sub": self.subject}).encode())
        r, s = decode_dss_signature(self.key.sign(f"{header}.{claims}".encode(), ec.ECDSA(hashes.SHA256())))
        return f"vapid t={header}.{claims}.{b64e(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}, k={self.public_key}"

    def send(self, subscription: dict, message: dict, *, urgency: str = "normal", ttl: int = 86400) -> int:
        """POST one notification. Returns the HTTP status (201 = accepted; 404/410 = subscription is gone)."""
        endpoint = subscription["endpoint"]
        body = encrypt(json.dumps(message, separators=(",", ":")).encode(), subscription["keys"]["p256dh"],
                       subscription["keys"]["auth"])
        try:
            r = httpx.post(endpoint, content=body, timeout=20, headers={
                "Authorization": self._vapid(endpoint), "Content-Encoding": "aes128gcm",
                "Content-Type": "application/octet-stream", "TTL": str(ttl), "Urgency": urgency})
        except httpx.HTTPError as e:
            log.warning("web push error: %s", e)
            return 0
        if r.status_code >= 400:
            log.warning("web push %s: %s", r.status_code, r.text[:200])
        return r.status_code
