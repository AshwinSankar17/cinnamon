"""Push notifications via FCM HTTP v1 (or a dry-run outbox when no key is configured).

The service-account key must hold only the "Firebase Cloud Messaging API Admin" role, live at
~/.brain/fcm-service-account.json with mode 0600; Brain refuses to use it otherwise. Payloads never
carry bearer tokens.
"""
from __future__ import annotations

import json
import logging
import stat
import time
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from .identity import b64e

log = logging.getLogger("brain.push")

CHANNELS = {
    # kind -> (android channel id, priority)
    "approval": ("needs_you", "HIGH"),
    "needs_you": ("needs_you", "HIGH"),
    "failed": ("failed", "HIGH"),
    "done": ("done", "NORMAL"),
    "budget": ("budget", "NORMAL"),
    "changed": ("changed", "NORMAL"),
    "transport": ("transport", "NORMAL"),
}
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/firebase.messaging"


class PushSender:
    def __init__(self, key_path: Path, outbox: Path, project_id: str = ""):
        self.key_path = key_path
        self.outbox = outbox
        self.project_id = project_id
        self._sa: dict | None = None
        self._token: tuple[str, float] | None = None
        if key_path.exists():
            mode = stat.S_IMODE(key_path.stat().st_mode)
            if mode & 0o077:
                raise PermissionError(f"{key_path} is {oct(mode)}; chmod 600 it before Brain will use it")
            self._sa = json.loads(key_path.read_text())
            self.project_id = self.project_id or self._sa.get("project_id", "")

    @property
    def live(self) -> bool:
        return self._sa is not None and bool(self.project_id)

    def _access_token(self) -> str:
        if self._token and self._token[1] > time.time() + 60:
            return self._token[0]
        assert self._sa
        now = int(time.time())
        header = b64e(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        claims = b64e(json.dumps({"iss": self._sa["client_email"], "scope": SCOPE, "aud": TOKEN_URL,
                                  "iat": now, "exp": now + 3600}).encode())
        key = serialization.load_pem_private_key(self._sa["private_key"].encode(), password=None)
        sig = key.sign(f"{header}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())
        jwt = f"{header}.{claims}.{b64e(sig)}"
        r = httpx.post(TOKEN_URL, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                        "assertion": jwt}, timeout=20)
        r.raise_for_status()
        tok = r.json()["access_token"]
        self._token = (tok, now + int(r.json().get("expires_in", 3600)))
        return tok

    def send(self, fcm_token: str | None, kind: str, title: str, body: str, data: dict[str, Any]) -> bool:
        channel, priority = CHANNELS.get(kind, ("changed", "NORMAL"))
        data = {k: str(v) for k, v in {"v": "1", "kind": kind, **data}.items() if v is not None}
        silent = kind == "transport"
        message: dict[str, Any] = {
            "token": fcm_token,
            "data": data,
            "android": {"priority": priority},
        }
        if not silent:
            message["android"]["notification"] = {"channel_id": channel, "title": title[:120], "body": body[:240]}
            message["notification"] = {"title": title[:120], "body": body[:240]}
        record = {"ts": time.time(), "kind": kind, "message": message}
        if not self.live or not fcm_token:
            with open(self.outbox, "a") as f:
                f.write(json.dumps(record) + "\n")
            return False
        try:
            r = httpx.post(
                f"https://fcm.googleapis.com/v1/projects/{self.project_id}/messages:send",
                headers={"Authorization": f"Bearer {self._access_token()}"},
                json={"message": message}, timeout=20,
            )
            if r.status_code >= 400:
                log.warning("FCM send failed %s: %s", r.status_code, r.text[:300])
                return False
            return True
        except httpx.HTTPError as e:
            log.warning("FCM send error: %s", e)
            return False
