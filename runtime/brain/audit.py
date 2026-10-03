"""Append-only audit segments and the incident-response (IR) log.

Audit: JSONL, one segment per day, every line carries the event hash so the chain can be re-verified
without the database. IR log: one line per process / job action, grep-able by an admin, 90-day retention.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .redact import SecretRegistry


class AuditWriter:
    def __init__(self, directory: Path, redactor: SecretRegistry):
        self.dir = directory
        self.redactor = redactor
        self.dir.mkdir(parents=True, exist_ok=True)

    def _segment(self) -> Path:
        return self.dir / f"{time.strftime('%Y-%m-%d')}.jsonl"

    def write(self, record: dict[str, Any]) -> None:
        rec, _ = self.redactor.redact_obj(record)
        path = self._segment()
        new = not path.exists()
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, separators=(",", ":"), ensure_ascii=False) + "\n")
        if new:
            os.chmod(path, 0o600)


class IRLog:
    """Admin-facing log: command lines and exit codes, never tool output or diff bodies."""

    RETENTION_DAYS = 90

    def __init__(self, directory: Path, redactor: SecretRegistry):
        self.dir = directory
        self.redactor = redactor
        self.dir.mkdir(parents=True, exist_ok=True)

    def log(self, kind: str, **fields: Any) -> None:
        fields = {k: v for k, v in fields.items() if v is not None}
        line = f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {kind} " + " ".join(
            f"{k}={json.dumps(v) if isinstance(v, str) and ' ' in v else v}" for k, v in fields.items()
        )
        line, _ = self.redactor.redact(line)
        path = self.dir / f"{time.strftime('%Y-%m-%d')}.log"
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        os.chmod(path, 0o600)

    def prune(self) -> None:
        cutoff = time.time() - self.RETENTION_DAYS * 86400
        for p in self.dir.glob("*.log"):
            if p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)

    def dump(self, since_seconds: float) -> str:
        cutoff = time.time() - since_seconds
        lines: list[str] = []
        for p in sorted(self.dir.glob("*.log")):
            if p.stat().st_mtime < cutoff:
                continue
            for line in p.read_text().splitlines():
                ts = line.split(" ", 1)[0]
                try:
                    t = time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
                except ValueError:
                    t = time.time()
                if t >= cutoff:
                    lines.append(line)
        return "\n".join(lines)
