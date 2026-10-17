"""Pick up the owner's own terminal agent sessions (Claude Code, Codex) as Brain threads.

Only sessions whose working directory is inside the configured root and inside a git repo are listed. The cluster
account may be shared, so the root is what keeps teammates' sessions out. Import copies the history into Brain.
`follow` (each scheduler minute) imports new sessions and copies in what the terminal added since; `watch` (each
second) does the second part. A session the thread runs with the CLI it came from is *linked*: the thread and the
terminal share that one session in the owner's own CLI home, one writer at a time. A phone message goes to an open
terminal the way its CLI queues input, as the owner's words (`route`/`deliver`); a closed one runs here in it.
See docs/superpowers/specs/2026-10-15-session-import-design.md.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path

from . import repos, transcript
from .config import RepoConfig
from .redact import _json_strings

AUTOMATED_CODEX = {"codex_exec", "Claude Code"}  # `codex exec` runs and Codex started by Claude's plugin
# ponytail: one import at a time (phone double tap + CLI); per-session locks if imports ever need to overlap
_LOCK = threading.Lock()
log = logging.getLogger(__name__)


def config(settings) -> dict:
    c = settings.raw.get("import") or {}
    return {"root": c.get("root"), "claude_dirs": list(c.get("claude_dirs") or []),
            "codex_dirs": list(c.get("codex_dirs") or []), "days": float(c.get("days", 30))}


def _inside(path: str, root: str) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())  # by path component: /x/ashwin2 is not in /x/ashwin
        return True
    except (ValueError, OSError):
        return False


def _toplevel(cwd: str) -> str | None:
    if not Path(cwd).is_dir():
        return None
    return repos.git(cwd, "rev-parse", "--show-toplevel", check=False).strip() or None


def _live_records(profile: Path) -> list[dict]:
    """<profile>/sessions/<pid>.json of each running `claude`: its pid is alive (not a zombie) and was started then."""
    out = []
    for f in (profile / "sessions").glob("*.json"):
        try:
            d = json.loads(f.read_text())
            stat = Path(f"/proc/{int(d['pid'])}/stat").read_text().rsplit(")", 1)[1].split()
            if stat[0] != "Z" and stat[19] == str(d.get("procStart")):
                out.append(d)
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            continue
    return out


def _claude_live(profile: Path) -> set[str]:
    return {d["sessionId"] for d in _live_records(profile)}


def _claude_meta(path: Path) -> dict | None:
    """cwd, title and whether the conversation moved to another session, without parsing every line."""
    cwd = title = first_user = None
    with path.open(errors="replace") as f:
        for line in f:
            if '"continuedInSessionId"' in line:
                return None
            if '"aiTitle"' in line or '"agentName"' in line:
                try:
                    r = json.loads(line)
                    title = r.get("aiTitle") or r.get("agentName") or title
                except json.JSONDecodeError:
                    pass
            elif cwd is None and '"cwd"' in line:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if str(r.get("entrypoint", "cli")).startswith("sdk"):  # `claude -p` / Agent SDK runs: automated
                    return None
                cwd = r.get("cwd")
                content = (r.get("message") or {}).get("content")
                if r.get("type") == "user" and isinstance(content, str):
                    first_user = content
    # the title is kept long here and cut only after redaction (a secret cut in half no longer matches)
    return None if not cwd else {"cwd": cwd, "title": (title or first_user or "Imported session")[:2000]}


def _claude(profile: Path, cutoff: float) -> list[dict]:
    out, live = [], _claude_live(profile)
    for f in (profile / "projects").glob("*/*.jsonl"):
        try:  # a file deleted or unreadable mid-scan must not break the whole list
            mtime = f.stat().st_mtime
            meta = _claude_meta(f) if mtime >= cutoff else None
        except (OSError, UnicodeDecodeError):
            continue
        if meta:
            out.append({"provider": "claude", "session_id": f.stem, "path": str(f), "last_ts": mtime,
                        "live": f.stem in live, **meta})
    return out


def _holder(lock: Path) -> int | None:
    """The pid holding a flock on `lock`, read from the kernel's lock table (never takes the lock itself). Codex
    and agy terminals hold one per open session; the file stays behind when they close."""
    try:
        st = lock.stat()
        table = Path("/proc/locks").read_text()
    except OSError:
        return None
    key = f"{os.major(st.st_dev):02x}:{os.minor(st.st_dev):02x}:{st.st_ino}"
    for line in table.splitlines():  # "79: FLOCK  ADVISORY  WRITE 2323554 103:09:37106986 0 EOF"; waiters have "->"
        f = line.split()
        if len(f) >= 6 and f[1] == "FLOCK" and f[5] == key:
            return int(f[4])
    return None


def _codex_lock(rollout: Path, sid: str) -> Path:
    return rollout.parents[4] / "thread-writer-locks" / f"{sid}.lock"  # <home>/sessions/Y/M/D/rollout-….jsonl


def _codex(home: Path, cutoff: float) -> list[dict]:
    out = []
    for f in (home / "sessions").glob("*/*/*/rollout-*.jsonl"):
        try:
            c = _codex_one(f, cutoff)
        except (OSError, UnicodeDecodeError, AttributeError):
            continue
        if c:
            out.append(c)
    return out


def _codex_one(f: Path, cutoff: float) -> dict | None:
    mtime = f.stat().st_mtime
    if mtime < cutoff:
        return None
    meta, title = None, None
    with f.open(errors="replace") as fh:
        for i, line in enumerate(fh):
            if i == 0:
                try:
                    meta = (json.loads(line).get("payload") or {})
                except json.JSONDecodeError:
                    break
            elif '"role": "user"' in line or '"role":"user"' in line:
                try:
                    p = json.loads(line).get("payload") or {}
                except json.JSONDecodeError:
                    continue
                text = "\n".join(x.get("text", "") for x in p.get("content") or [] if isinstance(x, dict))
                if text and not text.startswith("<"):
                    title = text[:2000]
                    break
    if not meta or not meta.get("cwd") or meta.get("originator") in AUTOMATED_CODEX:
        return None
    sid = meta.get("id") or f.stem
    return {"provider": "codex", "session_id": sid, "path": str(f), "cwd": meta["cwd"],
            "title": title or "Imported Codex session", "last_ts": mtime, "live": _holder(_codex_lock(f, sid)) is not None}


def discover(settings, now: float | None = None) -> list[dict]:
    c = config(settings)
    if not c["root"]:
        return []
    cutoff = (now or time.time()) - c["days"] * 86400
    found = []
    for d in c["claude_dirs"]:
        found += _claude(Path(d).expanduser(), cutoff)
    for d in c["codex_dirs"]:
        found += _codex(Path(d).expanduser(), cutoff)
    out = []
    for s in found:
        if not _inside(s["cwd"], c["root"]):
            continue
        top = _toplevel(s["cwd"])
        if top and _inside(top, c["root"]):
            out.append({**s, "repo_path": top})
    return sorted(out, key=lambda s: s["last_ts"], reverse=True)


BUDGET_BYTES = 400_000  # what the model is handed on the first phone message; the thread keeps everything


def _cut(items: list[dict]) -> int:
    """Index from which the newest items fit the budget, moved forward to start at a user message."""
    total, start = 0, len(items)
    for i in range(len(items) - 1, -1, -1):
        total += len(json.dumps(items[i]))
        if total > BUDGET_BYTES:
            break
        start = i
    j = start
    while j < len(items) and items[j]["k"] != "user":
        j += 1
    return j if j < len(items) else start


def _default_branch(path: str) -> str:
    head = repos.git(path, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False).strip()
    if head:
        return head.split("/", 1)[-1]
    for b in ("main", "master"):
        if repos.git(path, "rev-parse", "--verify", "-q", f"refs/heads/{b}", check=False).strip():
            return b
    cur = repos.git(path, "rev-parse", "--abbrev-ref", "HEAD", check=False).strip()
    return cur if cur and cur != "HEAD" else "main"


def _ensure_repo(brain, path: str) -> str:
    for r in brain.settings.repos:
        if Path(r.path).resolve() == Path(path).resolve():
            return r.id
    base = Path(path).name
    rid, n = base, 2
    while brain.settings.repo(rid):
        rid, n = f"{base}-{n}", n + 1
    brain.settings.repos.append(RepoConfig(id=rid, path=path, name=base, default_branch=_default_branch(path),
                                           worktree_root=str(Path(path).parent / ".brain-worktrees"), vault_root=""))
    brain.settings.save(brain.p)
    brain.ir.log("import.repo", repo=rid, path=path)
    return rid


def _learn_secrets(brain) -> None:
    """The terminal's sessions ran with the profile's own credentials, not Brain's: teach the redactor those too."""
    c = config(brain.settings)
    for d in c["claude_dirs"]:
        for v in _json_strings(Path(d).expanduser() / ".credentials.json"):
            brain.redactor.add(v)
    for d in c["codex_dirs"]:
        for v in _json_strings(Path(d).expanduser() / "auth.json"):
            brain.redactor.add(v)


def _title(brain, raw: str) -> str:
    return brain.redactor.redact(raw)[0].strip()[:60] or "Imported session"


def _events(brain, thread_id: str, provider: str, items: list[dict], shown: list[tuple] | None = None,
            keep_open: bool = False) -> None:
    """Replay the history as phone events, grouped like live turns: each owner message opens a turn. A message in
    `shown` (sent from the phone and typed into the terminal) is on the phone already: it opens its turn silently.
    keep_open: the terminal may still be writing the last turn, so it stays open for the next batch (see _settle)."""
    actor = {"kind": "import"}
    st = _open_turn(brain, thread_id) or {"turn": None, "text": "", "names": {}}

    def close() -> None:
        if st["turn"]:
            brain.events.append(thread_id, "turn.ended", {
                "turn_id": st["turn"], "text": st["text"][:2000], "counts": {},
                "diffstat": {"files": 0, "added": 0, "removed": 0}, "duration_ms": 0, "usage": {},
                "stop_reason": "end_turn"}, turn_id=st["turn"], actor=actor)
        st.update(turn=None, text="", names={})

    def open_turn() -> None:
        st["turn"] = f"turn_imp_{uuid.uuid4().hex[:12]}"
        brain.events.append(thread_id, "turn.started", {"turn_id": st["turn"], "provider": provider, "wake_reason": None,
                                                        "retry_of": None}, turn_id=st["turn"], actor=actor)

    for it in items:
        if it["k"] == "user":
            close()
            match = next((x for x in shown or [] if x[0].strip() == it["text"].strip()), None)
            if match is not None:
                shown.remove(match)
            else:
                brain.events.append(thread_id, "user.message", {"text": it["text"]}, actor=actor)
            open_turn()
            continue
        if st["turn"] is None:  # history that starts mid-turn
            open_turn()
        turn = st["turn"]
        if it["k"] == "assistant":
            st["text"] = it["text"]
            brain.events.append(thread_id, "assistant.text", {"text": it["text"]}, turn_id=turn, actor=actor)
        elif it["k"] == "call":
            st["names"][it["id"]] = it.get("name") or "?"
            brain.events.append(thread_id, "tool.started", {
                "tool_use_id": it["id"], "tool": it.get("name"), "summary": it.get("shell") or it.get("name"),
                "input_preview": json.dumps(it.get("input"))[:1500]}, turn_id=turn, actor=actor)
        elif it["k"] == "result":
            brain.events.append(thread_id, "tool.completed", {
                "tool_use_id": it["id"], "tool": st["names"].get(it["id"], "?"), "ok": not it.get("error"),
                "output_preview": str(it.get("output", ""))[-4000:]}, turn_id=turn, actor=actor)
    if keep_open and st["turn"]:
        brain._import_turns[thread_id] = st
    else:
        close()
        brain._import_turns.pop(thread_id, None)


def _open_turn(brain, tid: str) -> dict | None:
    """The terminal turn still open on the phone; after a restart it is found from the events."""
    if tid in brain._import_turns:
        return brain._import_turns[tid]
    r = brain.db.one("SELECT type, turn_id, actor FROM events WHERE thread_id=? AND type IN ('turn.started','turn.ended')"
                     " ORDER BY seq DESC LIMIT 1", (tid,))
    if r and r["type"] == "turn.started" and json.loads(r["actor"] or "{}").get("kind") == "import":
        brain._import_turns[tid] = {"turn": r["turn_id"], "text": "", "names": {}}
        return brain._import_turns[tid]
    return None


def _settle(brain, t: dict, force: bool = False) -> None:
    """End the open terminal turn once the terminal is done with it: idle, or no longer holding the session."""
    if not _open_turn(brain, t["id"]):
        return
    if not force and _busy(t):
        return
    unread = brain.thread_view(t)["unseen"]
    _events(brain, t["id"], _src(t), [])  # closes it
    if not unread:
        _arrive_read(brain, t["id"])


def candidates(brain) -> list[dict]:
    _learn_secrets(brain)
    have = {r["imported_from"]: r["id"] for r in
            brain.db.q("SELECT id, imported_from FROM threads WHERE imported_from IS NOT NULL")}
    return [{**c, "title": _title(brain, c["title"]), "thread_id": have.get(f"{c['provider']}:{c['session_id']}")}
            for c in discover(brain.settings)]


def _arrive_read(brain, tid: str) -> None:
    """The owner lived through this history in their terminal: none of its replies is unread news."""
    brain.mark_seen(tid)


def import_session(brain, provider: str, session_id: str, provider_override: str | None = None) -> dict:
    with _LOCK:
        return _import(brain, provider, session_id, provider_override)


def _read(brain, cand: dict) -> tuple[list[dict], int | None]:
    """The session's items, and for Claude the byte offset they end at (the next read starts there)."""
    if cand["provider"] == "claude":
        return transcript.read_claude_from(Path(cand["path"]), 0)
    return transcript.read_codex(Path(cand["path"]))[0], None


def _import(brain, provider: str, session_id: str, provider_override: str | None, cand: dict | None = None) -> dict:
    key = f"{provider}:{session_id}"
    row = brain.db.one("SELECT id FROM threads WHERE imported_from=?", (key,))
    if row:
        return brain.thread_view(brain.thread(row["id"]))
    cand = cand or next((c for c in discover(brain.settings)
                         if c["provider"] == provider and c["session_id"] == session_id), None)
    if cand is None:
        raise KeyError(f"no importable {provider} session {session_id}")
    _learn_secrets(brain)
    raw, end = _read(brain, cand)
    items, _ = brain.redactor.redact_obj(raw)  # terminal transcripts can hold echoed tokens
    run_as = provider_override or provider
    shared = provider == run_as and provider in SHARED
    t = brain.create_thread(_ensure_repo(brain, cand["repo_path"]), _title(brain, cand["title"]), "readonly", run_as)
    tid = t["id"]
    brain._append_transcript(tid, provider, None, items)
    _events(brain, tid, provider, items)
    cut = _cut(items)
    if shared:  # the terminal's own session already holds all of it
        _point_at_terminal(brain, tid, provider, session_id, sum(1 for i in raw if i["k"] != "user"))
    for name in {run_as, *brain.providers}:  # a later provider switch must not hydrate the whole history either
        brain.db.x("INSERT OR IGNORE INTO provider_sessions(thread_id, provider, native_session_id, sync_seq, updated_at,"
                   " native_seen, canon_synced) VALUES (?,?,NULL,0,?,0,?)", (tid, name, time.time(), cut))
    brain.events.append(tid, "system.notice", {"text": (
        f"Imported {len(items)} items from your terminal session {session_id} ({cand['path']}). This thread and the "
        f"terminal share that session: a message from here goes to the terminal while it has the session open, "
        f"or runs here in the same session when it is closed." if shared else
        f"Imported {len(items)} items from your terminal session {session_id} ({cand['path']}). Later terminal turns "
        f"keep arriving here; your next message continues it here in a new session, and the terminal's session is "
        f"untouched." + (f" The model is given the last {len(items) - cut} items; the full history stays in "
                         f"this thread." if cut else ""))})
    # last: a failed import is never "done". updated_at is the session's own last activity, not the import time, so an
    # old session ranks below the threads actually being worked on.
    brain.db.x("UPDATE threads SET imported_from=?, updated_at=?, import_items=?, import_mtime=?, import_path=?,"
               " import_offset=? WHERE id=?",
               (key, cand["last_ts"], len(items), cand["last_ts"], cand["path"], end, tid))
    _arrive_read(brain, tid)
    brain.ir.log("import", thread=tid, source=key, items=len(items))
    return brain.thread_view(brain.thread(tid))


def follow(brain) -> None:
    """One pass: import sessions not seen before, and copy into each imported thread what its terminal session added.

    Sources are append-only JSONL. Claude sessions are read from the byte offset already copied; Codex ones are parsed
    whole and sliced past the item count. A half-written last line counts as nothing until it is complete. A thread
    with a Brain turn running waits for the next pass."""
    with _LOCK:
        cands = discover(brain.settings)
        if not cands:
            return
        _learn_secrets(brain)
        have = {r["imported_from"]: dict(r) for r in
                brain.db.q("SELECT * FROM threads WHERE imported_from IS NOT NULL")}
        for c in cands:
            key = f"{c['provider']}:{c['session_id']}"
            try:
                if key not in have:
                    _import(brain, c["provider"], c["session_id"], None, c)
                else:
                    _catch_up(brain, have[key], c)
            except Exception:  # one bad session must not stop the others
                log.exception("follow %s failed", key)


def watch(brain) -> None:
    """Each second: copy in what each followed session added (a stat per session when nothing changed)."""
    if not _LOCK.acquire(blocking=False):  # follow is mid-pass; it covers these too
        return
    try:
        for r in brain.db.q("SELECT * FROM threads WHERE import_path IS NOT NULL"):
            try:
                _catch_up(brain, dict(r), None)
            except Exception:
                log.exception("watch %s failed", r["id"])
    finally:
        _LOCK.release()


def _point_at_terminal(brain, tid: str, provider: str, sid: str, native_seen: int) -> None:
    """The thread's session is the terminal's own: nothing in the transcript is missing from it."""
    n = brain.db.one("SELECT COUNT(*) AS n FROM transcript_items WHERE thread_id=?", (tid,))["n"]
    brain.db.x("INSERT INTO provider_sessions(thread_id, provider, native_session_id, sync_seq, updated_at, native_seen,"
               " canon_synced) VALUES (?,?,?,?,?,?,?) ON CONFLICT(thread_id, provider) DO UPDATE SET"
               " native_session_id=excluded.native_session_id, native_seen=excluded.native_seen,"
               " canon_synced=excluded.canon_synced, updated_at=excluded.updated_at",
               (tid, provider, sid, brain.events.last_seq(tid), time.time(), native_seen, n))


def _src(t: dict) -> str:
    return t["imported_from"].split(":", 1)[0]


def _sid(t: dict) -> str:
    return t["imported_from"].split(":", 1)[1]


def _done(brain, t: dict) -> int:
    """Items copied in from the session file so far (counted the old way for an import from before follow)."""
    if t["import_items"] is not None:
        return t["import_items"]
    return brain.db.one("SELECT COUNT(*) AS n FROM transcript_items WHERE thread_id=? AND turn_id IS NULL"
                        " AND provider!='user'", (t["id"],))["n"]  # the import wrote the only items with no turn


def _link_older(brain, t: dict, path: Path) -> dict:
    """An import from before session files were followed or shared: find where it stopped and, if it runs with its
    own CLI, share the terminal's session. Its earlier phone turns stay in the thread only."""
    src, done = _src(t), _done(brain, t)
    if src == "claude":
        off = transcript.claude_offset_after(path, done)
        with path.open("rb") as f:
            head = transcript._claude_items(f.read(off).decode(errors="replace").splitlines())[0]
    else:
        off, head = None, _items(src, path)[:done]
    brain.db.x("UPDATE threads SET import_path=?, import_offset=?, import_items=? WHERE id=?",
               (str(path), off, done, t["id"]))
    if t["provider"] == src and src in SHARED:
        _point_at_terminal(brain, t["id"], src, _sid(t), sum(1 for i in head if i["k"] != "user"))
        brain.events.append(t["id"], "system.notice", {"text": (
            "This thread now shares your terminal session: a message from here goes to the terminal while it has "
            "the session open, or runs here in the same session when it is closed. "
            "Earlier phone turns stay in this thread only.")})
    brain.ir.log("import.link", thread=t["id"], offset=off)
    return dict(brain.thread(t["id"]))


def _items(src: str, path: Path) -> list[dict]:
    return transcript.read_codex(path)[0] if src == "codex" else transcript.read_agy(path)[0]


def _catch_up(brain, t: dict, cand: dict | None, during_turn: bool = False) -> None:
    """Copy in what the session file gained. Claude files are read from the byte offset already copied; Codex and
    agy ones (their readers pair calls across lines) are read whole and sliced past the item count, only when
    their mtime moved. A terminal turn stays open across passes until the session is no longer busy (_settle)."""
    tid, src = t["id"], _src(t)
    if not t["import_path"]:
        t = _link_older(brain, t, Path(cand["path"]))
    path = Path(t["import_path"])
    mtime = path.stat().st_mtime
    if t["import_mtime"] is not None and mtime <= t["import_mtime"]:
        _settle(brain, t)
        return
    task = brain._tasks.get(tid)
    if not during_turn and task is not None and not task.done():
        return
    if src == "claude":
        raw, end = transcript.read_claude_from(path, t["import_offset"] or 0)
    else:
        done = _done(brain, t)
        raw, end = _items(src, path)[done:], None
    if raw:
        _learn_secrets(brain)
        new, _ = brain.redactor.redact_obj(raw)
        before = brain.db.one("SELECT COUNT(*) AS n FROM transcript_items WHERE thread_id=?", (tid,))["n"]
        unread = brain.thread_view(t)["unseen"]
        brain._append_transcript(tid, src, None, new)
        _events(brain, tid, src, new, brain._relayed.get(tid), keep_open=True)
        brain.db.x("UPDATE threads SET updated_at=?, archived=0 WHERE id=?", (mtime, tid))
        if not unread:  # the owner lived these turns in the terminal; an unread phone reply stays unread
            _arrive_read(brain, tid)
        if linked(t):  # the shared session holds these natively: never hand them to it again
            brain.db.x("UPDATE provider_sessions SET native_seen=native_seen+?, canon_synced=CASE WHEN canon_synced=?"
                       " THEN ? ELSE canon_synced END WHERE thread_id=? AND provider=?",
                       (sum(1 for i in raw if i["k"] != "user"), before, before + len(new), tid, src))
        brain.ir.log("import.follow", thread=tid, items=len(new))
    if src == "claude":
        brain.db.x("UPDATE threads SET import_offset=?, import_mtime=? WHERE id=?", (end, mtime, tid))
    else:
        brain.db.x("UPDATE threads SET import_items=?, import_mtime=? WHERE id=?", (done + len(raw), mtime, tid))


# ---- one writer: where a message for a linked thread goes ---------------------------------------
# "shell" is idle with a background shell running; "waiting" is a permission prompt, which would take typed text
# as its answer (Claude Code 2.1.302). Shell mode (`!`) reports "idle": the input box check below catches it.
RELAY_STATUS = {"idle", "busy", "shell"}
CONFIRM_S = 60  # a typed message not in the session file this long after the terminal went quiet did not arrive


SHARED = {"claude", "codex"}  # CLIs whose terminal session a thread can share
HOME_ENV = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}


def linked(t: dict) -> bool:
    """The thread and the terminal share one session, in the owner's own CLI home: it runs with the CLI it came from."""
    src = (t.get("imported_from") or "").split(":", 1)[0]
    return bool(t.get("import_path")) and src in SHARED and t["provider"] == src


def profile_of(t: dict) -> Path:
    """The owner's CLI home the session file lives in."""
    p = Path(t["import_path"])
    return p.parents[2] if _src(t) == "claude" else p.parents[4]  # <profile>/projects/<dir>/<sid>.jsonl, <home>/sessions/Y/M/D/…


def _open(t: dict) -> dict | None:
    """Claude: the live record of the terminal that has the session open."""
    return next((d for d in _live_records(profile_of(t)) if d.get("sessionId") == _sid(t)), None)


def _held(t: dict) -> int | None:
    """Codex: the pid of the terminal holding the thread's writer lock."""
    return _holder(_codex_lock(Path(t["import_path"]), _sid(t)))


def _codex_busy(path: Path) -> bool:
    """Codex is mid-turn: the last turn marker in its rollout is task_started."""
    with path.open("rb") as f:
        f.seek(max(0, path.stat().st_size - 262144))
        tail = f.read().decode(errors="replace")
    last = None
    for line in tail.splitlines():
        if '"task_started"' in line or '"task_complete"' in line or '"turn_aborted"' in line:
            try:
                last = (json.loads(line).get("payload") or {}).get("type") or last
            except json.JSONDecodeError:
                continue
    return last == "task_started"


def _ancestor(pid: int, root: int) -> bool:
    """root is pid itself or one of its parents."""
    for _ in range(64):
        if pid == root:
            return True
        if pid <= 1:
            return False
        try:
            pid = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return False
    return False


def _pane(rec: dict) -> tuple[str, str] | None:
    """(tmux socket, pane) the session runs in, only when that pane's shell is an ancestor of its process."""
    pane = str(rec.get("tmux") or "").rpartition(".")[2]
    if not pane.startswith("%"):
        return None
    try:
        env = Path(f"/proc/{int(rec['pid'])}/environ").read_bytes().split(b"\0")
        sock = next((e[5:].decode().split(",")[0] for e in env if e.startswith(b"TMUX=")), "")
        if not sock:
            return None
        out = subprocess.run(["tmux", "-S", sock, "display", "-p", "-t", pane, "#{pane_pid}"],
                             capture_output=True, text=True, timeout=5)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    shell = out.stdout.strip()
    return (sock, pane) if out.returncode == 0 and shell.isdigit() and _ancestor(int(rec["pid"]), int(shell)) else None


def _screen(sock: str, pane: str) -> str:
    out = subprocess.run(["tmux", "-S", sock, "capture-pane", "-p", "-t", pane], capture_output=True, text=True,
                         timeout=5)
    return out.stdout if out.returncode == 0 else ""


def _box_empty(screen: str, glyph: str) -> bool:
    """The TUI's input box (the lines between the last two rules) is one line: its prompt glyph and nothing typed.
    A draft, shell mode (`!` instead of the glyph) or a dialog in its place means typing now would go wrong."""
    lines = screen.rstrip("\n").splitlines()
    rules = [i for i, l in enumerate(lines) if l.strip() and set(l.strip()) == {"─"}]
    if len(rules) < 2 or rules[-1] - rules[-2] != 2:
        return False
    box = lines[rules[-2] + 1].replace("\xa0", " ").strip()
    if not box.startswith(glyph):
        return False
    rest = box[len(glyph):].strip()
    return not rest or (rest.startswith('Try "') and rest.endswith('"'))  # Claude's placeholder in a fresh session


def _literal(provider: str, text: str) -> str:
    """Typed text that starts with `!` runs as a shell command and `/` as a slash command. Claude keeps it a message
    after a leading newline, agy after a leading zero-width space (each ignores the other's; both observed)."""
    if not text.startswith(("!", "/")):
        return text
    return ("\n" if provider == "claude" else "\u200b") + text


def _paste(sock: str, pane: str, text: str) -> None:
    """Type a message into the pane as one bracketed paste, then submit it."""
    buf = f"brain-{uuid.uuid4().hex[:8]}"
    subprocess.run(["tmux", "-S", sock, "load-buffer", "-b", buf, "-"], input=text, text=True, check=True, timeout=10)
    subprocess.run(["tmux", "-S", sock, "paste-buffer", "-p", "-d", "-b", buf, "-t", pane], check=True, timeout=10)
    time.sleep(0.3)  # let the TUI take the paste before Enter
    subprocess.run(["tmux", "-S", sock, "send-keys", "-t", pane, "Enter"], check=True, timeout=10)


GLYPH = {"claude": "❯", "agy": ">"}  # the input box prompt of each TUI


def route(t: dict) -> tuple[str, tuple | None]:
    """'run' (closed, or open outside tmux and idle: Brain takes it over), 'deliver' (hand it to the open session
    the way its CLI queues input) or 'hold' (open and not ready for it; try again later)."""
    if _src(t) == "codex":  # `codex queue` takes it whatever the terminal is doing, and keeps it if it closes
        return ("deliver", ("queue", profile_of(t), _sid(t))) if _held(t) else ("run", None)
    rec = _open(t)
    if rec is None:
        return "run", None
    ready = "deliver" if rec.get("status") in RELAY_STATUS else "hold"
    pane = _pane(rec)
    if pane:
        return ready, ("pane", *pane)
    if rec.get("kind") == "bg" and rec.get("jobId"):  # no terminal of its own: open one to type into
        return ready, ("attach", profile_of(t), rec["jobId"])
    return ("run" if rec.get("status") == "idle" else "hold"), None


def deliver(brain, t: dict, target: tuple, text: str) -> bool:
    """Hand the message to the open session. False: it was not ready after all (a draft, a dialog); hold it."""
    entry = (text, time.time())
    sent = brain._relayed.setdefault(t["id"], [])
    sent.append(entry)  # first: the watcher may read it back from the file at once; it is on the phone already
    try:
        kind, provider = target[0], t["provider"]
        if kind == "pane":
            _, sock, pane = target
            ok = _box_empty(_screen(sock, pane), GLYPH[provider])
            if ok:
                _paste(sock, pane, _literal(provider, text))
        elif kind == "attach":
            ok = _attach_paste(target[1], target[2], text)
        elif kind == "queue":
            _codex_queue(target[1], target[2], text)
            ok = True
        else:
            raise ValueError(f"unknown delivery {kind}")
    except Exception:
        sent.remove(entry)
        raise
    if not ok:
        sent.remove(entry)
    else:
        brain.ir.log("import.deliver", thread=t["id"], via=kind)
    return ok


def _codex_queue(home: Path, sid: str, text: str) -> None:
    """Codex's own queue: a user message, run after the current turn, kept on disk while the terminal is closed."""
    subprocess.run([shutil.which("codex") or "codex", "queue", "--thread", sid, "--message", text],
                   env={**os.environ, "CODEX_HOME": str(home)}, capture_output=True, check=True, timeout=60)


def _busy(t: dict) -> bool:
    """The session is open and in the middle of something (a turn, a permission prompt)."""
    if _src(t) == "codex":
        return _held(t) is not None and _codex_busy(Path(t["import_path"]))
    rec = _open(t)
    return rec is not None and rec.get("status") not in ("idle", "shell")


def unconfirmed(brain) -> None:
    """A delivered message the session file never showed, though the terminal has been quiet for a while: tell the
    phone instead of leaving it looking sent."""
    now = time.time()
    for tid, sent in list(brain._relayed.items()):
        late = [x for x in sent if now - x[1] > CONFIRM_S]
        t = brain.thread(tid) if late else None
        if not late or (t and linked(t) and _busy(t)):
            continue
        for x in late:
            sent.remove(x)
            brain.events.append(tid, "system.notice", {"text": (
                f"Codex has this message queued: \u201c{x[0][:80]}\u201d. It runs the next time you open the "
                f"session in your terminal." if t and _src(t) == "codex" else
                f"Your terminal did not take this message: \u201c{x[0][:80]}\u201d. Send it again, or type it there.")})
        if not sent:
            brain._relayed.pop(tid, None)


ATTACH_SOCK = Path(os.environ.get("TMUX_TMPDIR", "/tmp")) / f"tmux-{os.getuid()}" / "brain-attach"
ATTACH_WAIT_S = 10.0


def _attach_paste(profile: Path, job: str, text: str) -> bool:
    """Type into a background session: open it with `claude attach` in a hidden tmux window, type once its input
    box is empty, close the window. The session keeps running (observed with 2.1.302)."""
    sock, name = str(ATTACH_SOCK), f"a-{job}"
    ATTACH_SOCK.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tm = ["tmux", "-S", sock]
    subprocess.run([*tm, "kill-session", "-t", name], capture_output=True, timeout=10)  # one left from a crash
    subprocess.run([*tm, "new-session", "-d", "-s", name, "-x", "200", "-y", "50", "env",
                    f"CLAUDE_CONFIG_DIR={profile}", shutil.which("claude") or "claude", "attach", job],
                   check=True, capture_output=True, timeout=10)
    try:
        end = time.time() + ATTACH_WAIT_S
        while time.time() < end:
            if _box_empty(_screen(sock, name), GLYPH["claude"]):
                _paste(sock, name, _literal("claude", text))
                time.sleep(1.5)  # submitted before the window goes
                return True
            time.sleep(0.25)
        return False
    finally:
        subprocess.run([*tm, "kill-session", "-t", name], capture_output=True, timeout=10)


def take_over(t: dict) -> str | None:
    """Close the idle terminal that holds the session so Brain can write it. The conversation is on disk."""
    if _src(t) == "codex":
        if _held(t):  # it opened since the message was routed: it gets the message instead
            raise RuntimeError("your terminal has this Codex session open; send again to queue it there")
        return None
    rec = _open(t)
    if rec is None:
        return None
    if rec.get("status") != "idle":
        raise RuntimeError("your terminal is working in this session; send again when it is idle")
    if rec.get("kind") == "bg":  # messages reach it through `claude attach`; never stopped to run here
        raise RuntimeError("your background session has this open; the message goes to it instead")
    sid = t["imported_from"].split(":", 1)[1]
    os.kill(int(rec["pid"]), signal.SIGTERM)
    note = f"Closed your idle terminal session (pid {rec['pid']}) to run this here. Pick it up with: claude --resume {sid}"
    for _ in range(100):
        if _open(t) is None:
            return note
        time.sleep(0.1)
    raise RuntimeError("could not close the terminal that has this session open")


def prepare(brain, t: dict) -> str | None:
    """Before Brain writes a linked session: close an idle terminal holding it, then copy in what it added."""
    note = take_over(t)
    with _LOCK:
        _catch_up(brain, dict(brain.thread(t["id"])), None, during_turn=True)
        _settle(brain, dict(brain.thread(t["id"])), force=True)  # Brain's own turn starts now
    return note


def synced(brain, t: dict) -> None:
    """After a Brain turn on a linked session: what it wrote is in the transcript already; skip past it."""
    path = Path(t["import_path"])
    if _src(t) != "claude":
        brain.db.x("UPDATE threads SET import_items=?, import_mtime=? WHERE id=?",
                   (len(_items(_src(t), path)), path.stat().st_mtime, t["id"]))
        return
    with path.open("rb") as f:
        data = f.read()
    brain.db.x("UPDATE threads SET import_offset=?, import_mtime=? WHERE id=?",
               (data.rfind(b"\n") + 1, path.stat().st_mtime, t["id"]))
