"""Brain identity, device pairing and session tokens (protocol/API.md §Identity).

Byte encoding matches mobile/src/crypto.ts exactly: "‖" is plain concatenation; tags and strings are
UTF-8; public keys are raw 32 bytes; nonces and the pairing secret are raw bytes (base64url on the
wire, no padding).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .db import DB

PAIR_TTL = 300
PAIR_ATTEMPTS = 5
CHALLENGE_TTL = 120
ACCESS_TTL = 900
REFRESH_TTL = 7 * 86400
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def host_of(endpoint: str) -> str:
    u = urlsplit(endpoint.strip())
    host = (u.hostname or "").lower()
    port = u.port
    if port and not ((u.scheme == "https" and port == 443) or (u.scheme == "http" and port == 80)):
        host = f"{host}:{port}"
    return host


class AuthError(Exception):
    pass


class PendingApproval(AuthError):
    """A paired device that an existing device has not approved yet."""


class Identity:
    def __init__(self, key_path: Path, token_secret_path: Path):
        self.key_path = key_path
        if key_path.exists():
            self.key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        else:
            self.key = Ed25519PrivateKey.generate()
            key_path.write_bytes(self.key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            os.chmod(key_path, 0o600)
        if token_secret_path.exists():
            self.token_secret = token_secret_path.read_bytes()
        else:
            self.token_secret = secrets.token_bytes(32)
            token_secret_path.write_bytes(self.token_secret)
            os.chmod(token_secret_path, 0o600)

    @property
    def pub_raw(self) -> bytes:
        return self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)

    @property
    def pub_b64(self) -> str:
        return b64e(self.pub_raw)

    def fingerprint(self) -> str:
        h = hashlib.sha256(self.pub_raw).hexdigest()
        return ":".join(h[i:i + 2] for i in range(0, 32, 2))

    def sign(self, msg: bytes) -> str:
        return b64e(self.key.sign(msg))

    # ---- tokens: "<b64(json)>.<b64(hmac)>" ----------------------------------------------------
    def mint_access(self, device_id: str) -> str:
        body = json.dumps({"d": device_id, "exp": int(time.time()) + ACCESS_TTL, "t": "a"}).encode()
        mac = hmac.new(self.token_secret, body, hashlib.sha256).digest()
        return f"{b64e(body)}.{b64e(mac)}"

    def check_access(self, token: str) -> str:
        try:
            b, m = token.split(".", 1)
            body = b64d(b)
            if not hmac.compare_digest(b64d(m), hmac.new(self.token_secret, body, hashlib.sha256).digest()):
                raise AuthError("bad token")
            data = json.loads(body)
        except (ValueError, json.JSONDecodeError) as e:
            raise AuthError("bad token") from e
        if data.get("t") != "a" or data.get("exp", 0) < time.time():
            raise AuthError("expired token")
        return data["d"]


def verify_ed25519(pub_raw: bytes, sig: bytes, msg: bytes) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(pub_raw).verify(sig, msg)
        return True
    except (InvalidSignature, ValueError):
        return False


class Pairing:
    def __init__(self, db: DB, ident: Identity):
        self.db = db
        self.ident = ident

    def create(self, endpoint: str, alternates: list[str] | None = None) -> dict:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        code = f"{code[:4]}-{code[4:]}"
        secret = secrets.token_bytes(32)
        exp = int(time.time()) + PAIR_TTL
        self.db.x("INSERT INTO pairings(code, secret, endpoint, expires_at) VALUES (?,?,?,?)",
                  (code, b64e(secret), endpoint, exp))
        out = {"v": 1, "brain_pub": self.ident.pub_b64, "endpoint": endpoint, "code": code,
               "secret": b64e(secret), "exp": exp}
        if alternates:
            out["alt"] = alternates  # failover relays; the app also refreshes them via GET /v1/endpoints
        return out

    @staticmethod
    def transcript(brain_pub: bytes, device_pub: bytes, endpoint: str, nonce_device: bytes) -> bytes:
        return hashlib.sha256(b"cb-pair-v1" + brain_pub + device_pub + endpoint.encode() + nonce_device).digest()

    def complete(self, code: str, device_pub_b64: str, device_name: str, nonce_b64: str, sig_b64: str,
                 mac_b64: str) -> dict:
        row = self.db.one("SELECT * FROM pairings WHERE code=?", (code,))
        if not row or row["used"] or row["expires_at"] < time.time() or row["attempts"] >= PAIR_ATTEMPTS:
            raise AuthError("pairing code invalid or expired")
        self.db.x("UPDATE pairings SET attempts=attempts+1 WHERE code=?", (code,))
        device_pub = b64d(device_pub_b64)
        nonce = b64d(nonce_b64)
        if len(device_pub) != 32 or len(nonce) != 32:
            raise AuthError("bad key or nonce length")
        t = self.transcript(self.ident.pub_raw, device_pub, row["endpoint"], nonce)
        good_mac = hmac.compare_digest(b64d(mac_b64), hmac.new(b64d(row["secret"]), t, hashlib.sha256).digest())
        if not good_mac or not verify_ed25519(device_pub, b64d(sig_b64), t):
            raise AuthError("pairing proof failed")
        device_id = f"dev_{uuid.uuid4().hex[:12]}"
        self.db.x("UPDATE pairings SET used=1 WHERE code=?", (code,))
        # The first device is trusted on pairing (it needed the code from your terminal). Every later one waits until
        # an already-paired device approves it, so a leaked pairing code alone never yields a working device.
        first = not self.db.one("SELECT 1 FROM devices WHERE revoked=0 AND approved=1")
        self.db.x("INSERT INTO devices(id, name, public_key, created_at, last_seen, approved) VALUES (?,?,?,?,?,?)",
                  (device_id, device_name[:64], device_pub_b64, time.time(), time.time(), 1 if first else 0))
        return {"device_id": device_id, "brain_sig": self.ident.sign(t), "pending_approval": not first}

    # ---- sessions -------------------------------------------------------------------------------
    def _device(self, device_id: str, approved: bool = True):
        d = self.db.one("SELECT * FROM devices WHERE id=?", (device_id,))
        if not d or d["revoked"]:
            raise AuthError("unknown or revoked device")
        if approved and not d["approved"]:
            raise PendingApproval("this device is waiting for approval on your paired phone (or `brain devices approve`)")
        return d

    def challenge(self, device_id: str, host: str) -> dict:
        self._device(device_id, approved=False)  # the token step enforces approval, with a clear message
        nonce = secrets.token_bytes(32)
        self.db.x("INSERT INTO challenges(nonce, device_id, expires_at) VALUES (?,?,?)",
                  (b64e(nonce), device_id, time.time() + CHALLENGE_TTL))
        return {"nonce": b64e(nonce), "brain_sig": self.ident.sign(b"cb-chal-v1" + nonce + host.lower().encode())}

    def token(self, device_id: str, nonce_b64: str, sig_b64: str) -> dict:
        d = self._device(device_id)
        ch = self.db.one("SELECT * FROM challenges WHERE nonce=? AND device_id=?", (nonce_b64, device_id))
        self.db.x("DELETE FROM challenges WHERE nonce=?", (nonce_b64,))
        if not ch or ch["expires_at"] < time.time():
            raise AuthError("challenge expired")
        if not verify_ed25519(b64d(d["public_key"]), b64d(sig_b64), b"cb-tok-v1" + b64d(nonce_b64)):
            raise AuthError("bad signature")
        return self._issue(device_id)

    def _issue(self, device_id: str) -> dict:
        refresh = secrets.token_urlsafe(32)
        self.db.x("INSERT INTO refresh_tokens(token_hash, device_id, expires_at) VALUES (?,?,?)",
                  (hashlib.sha256(refresh.encode()).hexdigest(), device_id, time.time() + REFRESH_TTL))
        self.db.x("UPDATE devices SET last_seen=? WHERE id=?", (time.time(), device_id))
        return {"access_token": self.ident.mint_access(device_id), "expires_in": ACCESS_TTL,
                "refresh_token": refresh, "refresh_expires_in": REFRESH_TTL}

    def refresh(self, device_id: str, refresh_token: str) -> dict:
        self._device(device_id)
        h = hashlib.sha256(refresh_token.encode()).hexdigest()
        row = self.db.one("SELECT * FROM refresh_tokens WHERE token_hash=? AND device_id=?", (h, device_id))
        self.db.x("DELETE FROM refresh_tokens WHERE token_hash=?", (h,))  # rotate on every use
        if not row or row["expires_at"] < time.time():
            raise AuthError("refresh token invalid")
        return self._issue(device_id)

    def authorize(self, token: str) -> str:
        device_id = self.ident.check_access(token)
        self._device(device_id)
        return device_id

    def verify_decision(self, device_id: str, approval_id: str, decision: str, sig_b64: str) -> bool:
        d = self._device(device_id)
        msg = b"cb-dec-v1" + approval_id.encode() + decision.encode()
        return verify_ed25519(b64d(d["public_key"]), b64d(sig_b64), msg)

    def revoke(self, device_id: str) -> None:
        self.db.x("UPDATE devices SET revoked=1 WHERE id=?", (device_id,))
        self.db.x("DELETE FROM refresh_tokens WHERE device_id=?", (device_id,))
        # a device revoked while waiting for approval: close its "new device" card everywhere
        self.db.x("UPDATE approvals SET state='denied', note='revoked', decided_at=? WHERE action_type='pair_device'"
                  " AND state='pending' AND tool_input=?", (time.time(), device_id))
