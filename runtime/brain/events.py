"""The event spine: per-thread monotonic seq, hash chain, 8 KB inline cap, replay, live fan-out.

Every user-visible fact about a thread is an event. Clients replay from a cursor (after_seq) and then
receive live events; the hash chain lets the phone detect gaps or tampering.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any

from .audit import AuditWriter
from .db import DB
from .redact import SecretRegistry

INLINE_LIMIT = 8 * 1024
GENESIS = "0" * 64
TEXT_FIELDS = ("text", "summary", "output_preview", "input_preview", "diff", "message")


def _canonical(d: dict) -> str:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(prev: str, body: dict) -> str:
    return hashlib.sha256((prev + _canonical(body)).encode()).hexdigest()


class EventStore:
    def __init__(self, db: DB, redactor: SecretRegistry, audit: AuditWriter, blob_dir: Path):
        self.db = db
        self.redactor = redactor
        self.audit = audit
        self.blob_dir = blob_dir
        self.blob_dir.mkdir(parents=True, exist_ok=True)
        self._subs: dict[str, set[asyncio.Queue]] = {}
        self._global_subs: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    # ---- live fan-out ---------------------------------------------------------------------------
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, thread_id: str | None) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        if thread_id is None:
            self._global_subs.add(q)
        else:
            self._subs.setdefault(thread_id, set()).add(q)
        return q

    def unsubscribe(self, thread_id: str | None, q: asyncio.Queue) -> None:
        if thread_id is None:
            self._global_subs.discard(q)
        else:
            self._subs.get(thread_id, set()).discard(q)

    def _publish(self, ev: dict) -> None:
        targets = list(self._subs.get(ev["thread_id"], ())) + list(self._global_subs)
        if not targets:
            return

        def put() -> None:
            for q in targets:
                try:
                    q.put_nowait(ev)
                except asyncio.QueueFull:
                    pass  # slow consumer: it will catch up from the cursor

        if self._loop and self._loop.is_running():
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is self._loop:
                put()
            else:
                self._loop.call_soon_threadsafe(put)
        else:
            put()

    # ---- blobs ----------------------------------------------------------------------------------
    def put_blob(self, data: bytes) -> str:
        bid = hashlib.sha256(data).hexdigest()
        p = self.blob_dir / bid[:2] / bid
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        return bid

    def blob_path(self, bid: str) -> Path | None:
        if not all(c in "0123456789abcdef" for c in bid) or len(bid) != 64:
            return None
        p = self.blob_dir / bid[:2] / bid
        return p if p.exists() else None

    def _cap(self, payload: dict) -> tuple[dict, str | None]:
        raw = _canonical(payload)
        if len(raw.encode()) <= INLINE_LIMIT:
            return payload, None
        ref = self.put_blob(raw.encode())
        capped = dict(payload)
        for k in TEXT_FIELDS:
            v = capped.get(k)
            if isinstance(v, str) and len(v) > 2000:
                capped[k] = v[:1200] + f"\n… [{len(v) - 1800} chars truncated] …\n" + v[-600:]
        capped["truncated"] = True
        if len(_canonical(capped).encode()) > INLINE_LIMIT:
            capped = {k: capped[k] for k in capped if k not in TEXT_FIELDS}
            capped["truncated"] = True
        return capped, ref

    # ---- append / replay ------------------------------------------------------------------------
    def append(
        self,
        thread_id: str,
        type_: str,
        payload: dict[str, Any],
        *,
        visibility: str = "user",
        turn_id: str | None = None,
        actor: dict | None = None,
    ) -> dict:
        payload, n = self.redactor.redact_obj(payload)
        if n:
            payload = dict(payload, redactions=n)
        payload, ref = self._cap(payload)
        actor = actor or {"kind": "system"}
        with self.db.tx() as c:
            row = c.execute(
                "SELECT seq, hash FROM events WHERE thread_id=? ORDER BY seq DESC LIMIT 1", (thread_id,)
            ).fetchone()
            seq = (row["seq"] + 1) if row else 1
            prev = row["hash"] if row else GENESIS
            ev = {
                "event_id": f"evt_{uuid.uuid4().hex}",
                "thread_id": thread_id,
                "seq": seq,
                "ts": time.time(),
                "type": type_,
                "visibility": visibility,
                "turn_id": turn_id,
                "actor": actor,
                "payload": payload,
                "payload_ref": ref,
            }
            h = _hash(prev, ev)
            c.execute(
                "INSERT INTO events(thread_id,seq,event_id,ts,type,visibility,turn_id,actor,payload,payload_ref,prev_hash,hash)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (thread_id, seq, ev["event_id"], ev["ts"], type_, visibility, turn_id, json.dumps(actor),
                 json.dumps(payload, ensure_ascii=False), ref, prev, h),
            )
            text = " ".join(str(payload.get(k, "")) for k in TEXT_FIELDS if payload.get(k))
            if text:
                c.execute("INSERT INTO events_fts(thread_id, seq, text) VALUES (?,?,?)", (thread_id, seq, text))
            c.execute("UPDATE threads SET last_seq=?, updated_at=? WHERE id=?", (seq, ev["ts"], thread_id))
        ev["prev_hash"], ev["hash"] = prev, h
        self.audit.write(ev)
        if visibility == "user":
            self._publish(ev)
        return ev

    @staticmethod
    def _row_to_event(r) -> dict:
        return {
            "event_id": r["event_id"],
            "thread_id": r["thread_id"],
            "seq": r["seq"],
            "ts": r["ts"],
            "type": r["type"],
            "visibility": r["visibility"],
            "turn_id": r["turn_id"],
            "actor": json.loads(r["actor"]),
            "payload": json.loads(r["payload"]),
            "payload_ref": r["payload_ref"],
            "prev_hash": r["prev_hash"],
            "hash": r["hash"],
        }

    def replay(self, thread_id: str, after_seq: int = 0, limit: int = 200, visibility: str = "user") -> list[dict]:
        rows = self.db.q(
            "SELECT * FROM events WHERE thread_id=? AND seq>? AND visibility=? ORDER BY seq LIMIT ?",
            (thread_id, after_seq, visibility, min(max(limit, 1), 1000)),
        )
        return [self._row_to_event(r) for r in rows]

    def last_seq(self, thread_id: str) -> int:
        r = self.db.one("SELECT MAX(seq) AS s FROM events WHERE thread_id=?", (thread_id,))
        return int(r["s"] or 0) if r else 0

    def verify_chain(self, thread_id: str) -> tuple[bool, int]:
        prev = GENESIS
        n = 0
        for r in self.db.q("SELECT * FROM events WHERE thread_id=? ORDER BY seq", (thread_id,)):
            ev = self._row_to_event(r)
            body = {k: ev[k] for k in ev if k not in ("prev_hash", "hash")}
            if r["prev_hash"] != prev or _hash(prev, body) != r["hash"]:
                return False, ev["seq"]
            prev = r["hash"]
            n += 1
        return True, n

    def search(self, query: str, limit: int = 50) -> list[dict]:
        rows = self.db.q(
            "SELECT thread_id, seq, snippet(events_fts, 2, '[', ']', '…', 12) AS snip FROM events_fts"
            " WHERE events_fts MATCH ? LIMIT ?",
            (query, limit),
        )
        return [dict(r) for r in rows]
