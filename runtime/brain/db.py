"""SQLite state store with a single-opener lease.

SQLite on NFS/Lustre is only safe with exactly one opener. We enforce that two ways:
  1. A lease file {host, pid, job_id, heartbeat} that a second Brain refuses to override while fresh.
  2. locking_mode=EXCLUSIVE + WAL, which keeps the WAL index in heap memory instead of a shared-memory
     file (https://www.sqlite.org/wal.html, "WAL without shared memory").
"""
from __future__ import annotations

import json
import logging
import os
import socket
import sqlite3
import threading
import time
from pathlib import Path

LEASE_STALE_SECONDS = 90
HEARTBEAT_SECONDS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);

CREATE TABLE IF NOT EXISTS threads (
  id TEXT PRIMARY KEY,
  repo_id TEXT NOT NULL,
  title TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT 'readonly',
  branch TEXT,
  worktree TEXT,
  provider TEXT NOT NULL DEFAULT 'claude',
  state TEXT NOT NULL DEFAULT 'idle',
  pinned INTEGER NOT NULL DEFAULT 0,
  archived INTEGER NOT NULL DEFAULT 0,
  muted INTEGER NOT NULL DEFAULT 0,
  parent_id TEXT,
  fork_turn INTEGER,
  last_seq INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
  thread_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  event_id TEXT NOT NULL UNIQUE,
  ts REAL NOT NULL,
  type TEXT NOT NULL,
  visibility TEXT NOT NULL,
  turn_id TEXT,
  actor TEXT NOT NULL,
  payload TEXT NOT NULL,
  payload_ref TEXT,
  prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL,
  PRIMARY KEY (thread_id, seq)
);
CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(thread_id UNINDEXED, seq UNINDEXED, text);

CREATE TABLE IF NOT EXISTS turns (
  turn_id TEXT PRIMARY KEY,
  thread_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  started_seq INTEGER NOT NULL,
  ended_seq INTEGER,
  started_at REAL NOT NULL,
  ended_at REAL,
  reads INTEGER NOT NULL DEFAULT 0,
  edits INTEGER NOT NULL DEFAULT 0,
  commands INTEGER NOT NULL DEFAULT 0,
  failed INTEGER NOT NULL DEFAULT 0,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  stop_reason TEXT,
  text TEXT
);

CREATE TABLE IF NOT EXISTS provider_sessions (
  thread_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  native_session_id TEXT,
  sync_seq INTEGER NOT NULL DEFAULT 0,
  updated_at REAL NOT NULL,
  PRIMARY KEY (thread_id, provider)
);

-- Lossless provider-neutral transcript (brain/transcript.py item dicts); what a switched-to provider is hydrated from.
CREATE TABLE IF NOT EXISTS transcript_items (
  thread_id TEXT NOT NULL,
  idx INTEGER NOT NULL,
  provider TEXT NOT NULL,
  turn_id TEXT,
  kind TEXT NOT NULL,
  data TEXT NOT NULL,
  ts REAL NOT NULL,
  PRIMARY KEY (thread_id, idx)
);

CREATE TABLE IF NOT EXISTS listening_tests (
  id TEXT PRIMARY KEY,
  thread_id TEXT NOT NULL,
  path TEXT NOT NULL,
  title TEXT NOT NULL,
  n_items INTEGER NOT NULL,
  n_systems INTEGER NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending',
  summary TEXT,
  created_at REAL NOT NULL,
  rated_at REAL
);

CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY,
  thread_id TEXT NOT NULL,
  tool_use_id TEXT,
  action_type TEXT NOT NULL,
  summary TEXT NOT NULL,
  decision_summary TEXT NOT NULL,
  tool_input TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending',
  note TEXT,
  device_id TEXT,
  created_at REAL NOT NULL,
  decided_at REAL
);

CREATE TABLE IF NOT EXISTS waits (
  id TEXT PRIMARY KEY,
  thread_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  predicate TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'open',
  created_at REAL NOT NULL,
  fired_at REAL,
  next_check_at REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS waits_open ON waits(state, next_check_at);

CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY,
  thread_id TEXT,
  run_id TEXT,
  name TEXT NOT NULL,
  partition TEXT,
  gpus INTEGER,
  time_limit TEXT,
  sentinel TEXT,
  state TEXT NOT NULL DEFAULT 'PENDING',
  exit_code INTEGER,
  submitted_at REAL NOT NULL,
  updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  public_key TEXT NOT NULL,
  fcm_token TEXT,
  revoked INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  last_seen REAL
);

CREATE TABLE IF NOT EXISTS pairings (
  code TEXT PRIMARY KEY,
  secret TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  expires_at REAL NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  used INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS challenges (
  nonce TEXT PRIMARY KEY,
  device_id TEXT NOT NULL,
  expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS refresh_tokens (
  token_hash TEXT PRIMARY KEY,
  device_id TEXT NOT NULL,
  expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS idempotency (
  key TEXT PRIMARY KEY,
  device_id TEXT,
  request_sha256 TEXT NOT NULL,
  response TEXT NOT NULL,
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS ledger (
  day TEXT NOT NULL,
  scope TEXT NOT NULL,
  metric TEXT NOT NULL,
  value REAL NOT NULL DEFAULT 0,
  PRIMARY KEY (day, scope, metric)
);

CREATE TABLE IF NOT EXISTS digest (
  ts REAL NOT NULL,
  thread_id TEXT,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL
);
"""


# Additive column migrations for databases created by earlier versions: (table, column, declaration).
MIGRATIONS = [
    ("provider_sessions", "native_seen", "INTEGER NOT NULL DEFAULT 0"),   # non-user items read from the native session
    ("provider_sessions", "canon_synced", "INTEGER NOT NULL DEFAULT 0"),  # transcript items the native session holds
    ("approvals", "raw_input", "TEXT"),                                   # the tool input, for providers that need a rule
    ("threads", "vault_dir", "TEXT"),                                     # a training run's own vault folder
    ("devices", "web_push", "TEXT"),                                      # PWA Web Push subscription (JSON)
    ("devices", "approved", "INTEGER NOT NULL DEFAULT 1"),                # 0 = paired but waiting for an existing device
    ("jobs", "label", "TEXT"),                                            # what the job is, e.g. its script name
    ("turns", "prompt", "TEXT"),                                          # what the turn was asked, for Retry
    ("turns", "wake_reason", "TEXT"),
    ("threads", "imported_from", "TEXT"),                                 # "<provider>:<session id>" of an imported terminal session
    ("threads", "seen_at", "REAL"),                                       # when the owner last opened it (read marker)
    ("threads", "import_items", "INTEGER"),                               # source transcript items already copied in
    ("threads", "import_mtime", "REAL"),                                  # the source file's mtime when last copied
]


class LeaseHeld(RuntimeError):
    pass


class Lease:
    """Single-opener lease next to the database. A fresh lease held by another process blocks startup."""

    def __init__(self, path: Path):
        self.path = path
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _me(self) -> dict:
        return {
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "job_id": os.environ.get("SLURM_JOB_ID", ""),
            "heartbeat": time.time(),
        }

    def read(self) -> dict | None:
        try:
            return json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def acquire(self) -> None:
        cur = self.read()
        me = self._me()
        if cur and (cur.get("host"), cur.get("pid")) != (me["host"], me["pid"]):
            age = time.time() - float(cur.get("heartbeat", 0))
            if age < LEASE_STALE_SECONDS:
                raise LeaseHeld(
                    f"Brain is already running on {cur.get('host')} pid {cur.get('pid')} "
                    f"(heartbeat {age:.0f}s ago). Refusing to open the database twice."
                )
        self._write(me)
        self._thread = threading.Thread(target=self._beat, daemon=True)
        self._thread.start()

    def _write(self, data: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def _beat(self) -> None:
        while not self._stop.wait(HEARTBEAT_SECONDS):
            try:
                self._write(self._me())
            except OSError as e:  # e.g. a full disk for a moment: one missed beat must not stop all later ones
                logging.getLogger("brain").warning("lease heartbeat failed: %s", e)

    def release(self) -> None:
        self._stop.set()
        cur = self.read()
        if cur and cur.get("pid") == os.getpid():
            self.path.unlink(missing_ok=True)


def connect(path: Path | str, exclusive: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    if exclusive:
        conn.execute("PRAGMA locking_mode=EXCLUSIVE")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    for table, col, decl in MIGRATIONS:
        if col not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    if str(path) != ":memory:":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return conn


class DB:
    """Thin wrapper: one connection, one writer lock (Brain runs a single process)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.lock = threading.RLock()

    def q(self, sql: str, args: tuple | list = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def one(self, sql: str, args: tuple | list = ()) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute(sql, args).fetchone()

    def x(self, sql: str, args: tuple | list = ()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, args)

    def tx(self):
        return _Tx(self)


class _Tx:
    def __init__(self, db: DB):
        self.db = db

    def __enter__(self):
        self.db.lock.acquire()
        self.db.conn.execute("BEGIN IMMEDIATE")
        return self.db.conn

    def __exit__(self, et, ev, tb):
        try:
            self.db.conn.execute("ROLLBACK" if et else "COMMIT")
        finally:
            self.db.lock.release()
        return False


def row_dict(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r is not None else None
