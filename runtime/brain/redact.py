"""Secret registry: exact-value redaction of secrets Brain can find on this account.

Pattern-matching misses most real secrets (a W&B key has no prefix). Instead we load the actual secret
values from the user's credential stores at startup, keep them only in memory, and replace any exact
occurrence before an event reaches the event log, the audit log, the IR log or a push payload.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable

MIN_LEN = 8
ENV_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)", re.I)


def _netrc_passwords(path: Path) -> list[str]:
    out: list[str] = []
    try:
        toks = path.read_text().split()
    except OSError:
        return out
    for i, t in enumerate(toks[:-1]):
        if t in ("password", "passwd"):
            out.append(toks[i + 1])
    return out


def _json_strings(path: Path, keys_like: re.Pattern = re.compile(r"token|key|secret", re.I)) -> list[str]:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    out: list[str] = []

    def walk(o: Any, k: str = "") -> None:
        if isinstance(o, dict):
            for kk, vv in o.items():
                walk(vv, kk)
        elif isinstance(o, list):
            for vv in o:
                walk(vv, k)
        elif isinstance(o, str) and keys_like.search(k or ""):
            out.append(o)

    walk(data)
    return out


class SecretRegistry:
    def __init__(self, values: Iterable[str] = ()):
        self._values: set[str] = set()
        for v in values:
            self.add(v)

    def add(self, value: str | None) -> None:
        if value and len(value) >= MIN_LEN and not value.isspace():
            self._values.add(value.strip())

    def __len__(self) -> int:
        return len(self._values)

    @staticmethod
    def marker(value: str) -> str:
        return f"[REDACTED:{hashlib.sha256(value.encode()).hexdigest()[:8]}]"

    def redact(self, text: str) -> tuple[str, int]:
        n = 0
        # Longest first so a secret that contains another is replaced whole.
        for v in sorted(self._values, key=len, reverse=True):
            if v in text:
                n += text.count(v)
                text = text.replace(v, self.marker(v))
        return text, n

    def redact_obj(self, obj: Any) -> tuple[Any, int]:
        total = 0

        def walk(o: Any) -> Any:
            nonlocal total
            if isinstance(o, str):
                r, n = self.redact(o)
                total += n
                return r
            if isinstance(o, dict):
                return {k: walk(v) for k, v in o.items()}
            if isinstance(o, list):
                return [walk(v) for v in o]
            return o

        return walk(obj), total

    @classmethod
    def from_environment(cls, home: Path | None = None, extra_files: Iterable[Path] = ()) -> "SecretRegistry":
        home = home or Path.home()
        reg = cls()
        for v in _netrc_passwords(home / ".netrc"):
            reg.add(v)
        for p in (home / ".claude" / ".credentials.json", home / ".codex" / "auth.json"):
            for v in _json_strings(p):
                reg.add(v)
        hf = home / ".cache" / "huggingface" / "token"
        if hf.exists():
            try:
                reg.add(hf.read_text().strip())
            except OSError:
                pass
        for k, v in os.environ.items():
            if ENV_NAME.search(k):
                reg.add(v)
        for f in extra_files:
            if f.suffix == ".json":
                for v in _json_strings(f, re.compile(r"key|token|secret", re.I)):
                    reg.add(v)
            else:
                try:
                    for line in f.read_text().splitlines():
                        if "=" in line:
                            reg.add(line.split("=", 1)[1].strip().strip('"\''))
                except OSError:
                    pass
        return reg
