"""Pick up the owner's own terminal agent sessions (Claude Code, Codex) as Brain threads.

Only sessions whose working directory is inside the configured root and inside a git repo are listed. The cluster
account may be shared, so the root is what keeps teammates' sessions out. Import copies the history into Brain: the
first phone message continues it in a new native session in Brain's own config home, never in the terminal's file.
`follow` (each scheduler minute) imports new sessions and copies in what the terminal added since; `watch` (each
second) does the second part for Claude sessions. A Claude session run as Claude is *linked*: the thread and the
terminal share that one session, with one writer at a time (see `route`).
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


LIVE_WINDOW_S = 600  # Codex keeps no session registry: a rollout written in the last 10 minutes counts as open


def _codex(home: Path, cutoff: float, now: float) -> list[dict]:
    out = []
    for f in (home / "sessions").glob("*/*/*/rollout-*.jsonl"):
        try:
            c = _codex_one(f, cutoff, now)
        except (OSError, UnicodeDecodeError, AttributeError):
            continue
        if c:
            out.append(c)
    return out


def _codex_one(f: Path, cutoff: float, now: float) -> dict | None:
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
    return {"provider": "codex", "session_id": meta.get("id") or f.stem, "path": str(f), "cwd": meta["cwd"],
            "title": title or "Imported Codex session", "last_ts": mtime, "live": now - mtime < LIVE_WINDOW_S}


def discover(settings, now: float | None = None) -> list[dict]:
    c = config(settings)
    if not c["root"]:
        return []
    cutoff = (now or time.time()) - c["days"] * 86400
    found = []
    for d in c["claude_dirs"]:
        found += _claude(Path(d).expanduser(), cutoff)
    for d in c["codex_dirs"]:
        found += _codex(Path(d).expanduser(), cutoff, now or time.time())
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


def _events(brain, thread_id: str, provider: str, items: list[dict], shown: list[str] | None = None,
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
            match = next((x for x in shown or [] if x.strip() == it["text"].strip()), None)
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
    if not force:
        rec = _open(t)
        if rec is not None and rec.get("status") != "idle":
            return
    unread = brain.thread_view(t)["unseen"]
    _events(brain, t["id"], t["imported_from"].split(":", 1)[0], [])  # closes it
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
    shared = provider == "claude" and run_as == "claude"
    t = brain.create_thread(_ensure_repo(brain, cand["repo_path"]), _title(brain, cand["title"]), "readonly", run_as)
    tid = t["id"]
    brain._append_transcript(tid, provider, None, items)
    _events(brain, tid, provider, items)
    cut = _cut(items)
    if shared:  # the terminal's own session already holds all of it
        _point_at_terminal(brain, tid, session_id, sum(1 for i in raw if i["k"] != "user"))
    for name in {run_as, *brain.providers}:  # a later provider switch must not hydrate the whole history either
        brain.db.x("INSERT OR IGNORE INTO provider_sessions(thread_id, provider, native_session_id, sync_seq, updated_at,"
                   " native_seen, canon_synced) VALUES (?,?,NULL,0,?,0,?)", (tid, name, time.time(), cut))
    brain.events.append(tid, "system.notice", {"text": (
        f"Imported {len(items)} items from your terminal session {session_id} ({cand['path']}). This thread and the "
        f"terminal share that session: a message from here goes to the terminal while it has the session open "
        f"(typed into its tmux pane), or runs here in the same session when it is closed." if shared else
        f"Imported {len(items)} items from your terminal session {session_id} ({cand['path']}). Later terminal turns "
        f"keep arriving here; your next message continues it here in a new session, and the terminal's session is "
        f"untouched." + (f" The model is given the last {len(items) - cut} items; the full history stays in "
                         f"this thread." if cut else ""))})
    # last: a failed import is never "done". updated_at is the session's own last activity, not the import time, so an
    # old session ranks below the threads actually being worked on.
    brain.db.x("UPDATE threads SET imported_from=?, updated_at=?, import_items=?, import_mtime=?, import_path=?,"
               " import_offset=? WHERE id=?",
               (key, cand["last_ts"], len(items), cand["last_ts"], cand["path"] if end is not None else None, end, tid))
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
    """Each second: copy in what each Claude session added (a stat per session when nothing changed)."""
    if not _LOCK.acquire(blocking=False):  # follow is mid-pass; it covers these too
        return
    try:
        for r in brain.db.q("SELECT * FROM threads WHERE import_path IS NOT NULL AND imported_from LIKE 'claude:%'"):
            try:
                _catch_up(brain, dict(r), None)
            except Exception:
                log.exception("watch %s failed", r["id"])
    finally:
        _LOCK.release()


def _point_at_terminal(brain, tid: str, sid: str, native_seen: int) -> None:
    """The thread's Claude session is the terminal's own: nothing in the transcript is missing from it."""
    n = brain.db.one("SELECT COUNT(*) AS n FROM transcript_items WHERE thread_id=?", (tid,))["n"]
    brain.db.x("INSERT INTO provider_sessions(thread_id, provider, native_session_id, sync_seq, updated_at, native_seen,"
               " canon_synced) VALUES (?,'claude',?,?,?,?,?) ON CONFLICT(thread_id, provider) DO UPDATE SET"
               " native_session_id=excluded.native_session_id, native_seen=excluded.native_seen,"
               " canon_synced=excluded.canon_synced, updated_at=excluded.updated_at",
               (tid, sid, brain.events.last_seq(tid), time.time(), native_seen, n))


def _link_older(brain, t: dict, path: Path) -> dict:
    """A Claude import from before sessions were shared: find its byte offset and, if it runs as Claude, share the
    terminal's session. Its earlier phone turns stay in the thread only."""
    done = t["import_items"]
    if done is None:  # imported before follow existed: the import wrote the only items with no turn and a provider
        done = brain.db.one("SELECT COUNT(*) AS n FROM transcript_items WHERE thread_id=? AND turn_id IS NULL"
                            " AND provider!='user'", (t["id"],))["n"]
    off = transcript.claude_offset_after(path, done)
    brain.db.x("UPDATE threads SET import_path=?, import_offset=? WHERE id=?", (str(path), off, t["id"]))
    if t["provider"] == "claude":
        with path.open("rb") as f:
            head = f.read(off).decode(errors="replace").splitlines()
        seen = sum(1 for i in transcript._claude_items(head)[0] if i["k"] != "user")
        _point_at_terminal(brain, t["id"], t["imported_from"].split(":", 1)[1], seen)
        brain.events.append(t["id"], "system.notice", {"text": (
            "This thread now shares your terminal session: a message from here goes to the terminal while it has "
            "the session open (typed into its tmux pane), or runs here in the same session when it is closed. "
            "Earlier phone turns stay in this thread only.")})
    brain.ir.log("import.link", thread=t["id"], offset=off)
    return dict(brain.thread(t["id"]))


def _catch_up(brain, t: dict, cand: dict | None, during_turn: bool = False) -> None:
    tid = t["id"]
    claude = t["imported_from"].startswith("claude:")
    if claude and not t["import_path"]:
        t = _link_older(brain, t, Path(cand["path"]))
    path = Path(t["import_path"] or cand["path"])
    mtime = path.stat().st_mtime
    if t["import_mtime"] is not None and mtime <= t["import_mtime"]:
        if claude:
            _settle(brain, t)
        return
    task = brain._tasks.get(tid)
    if not during_turn and task is not None and not task.done():
        return
    if claude:
        raw, end = transcript.read_claude_from(path, t["import_offset"] or 0)
    else:
        done = t["import_items"]
        if done is None:  # imported before follow existed: see _link_older
            done = brain.db.one("SELECT COUNT(*) AS n FROM transcript_items WHERE thread_id=? AND turn_id IS NULL"
                                " AND provider!='user'", (tid,))["n"]
        raw, end = _read(brain, cand)[0][done:], None
    if raw:
        _learn_secrets(brain)
        new, _ = brain.redactor.redact_obj(raw)
        before = brain.db.one("SELECT COUNT(*) AS n FROM transcript_items WHERE thread_id=?", (tid,))["n"]
        unread = brain.thread_view(t)["unseen"]
        brain._append_transcript(tid, t["imported_from"].split(":", 1)[0], None, new)
        _events(brain, tid, t["imported_from"].split(":", 1)[0], new, brain._relayed.get(tid), keep_open=claude)
        brain.db.x("UPDATE threads SET updated_at=?, archived=0 WHERE id=?", (mtime, tid))
        if not unread:  # the owner lived these turns in the terminal; an unread phone reply stays unread
            _arrive_read(brain, tid)
        if linked(t):  # the shared session holds these natively: never hand them to it again
            brain.db.x("UPDATE provider_sessions SET native_seen=native_seen+?, canon_synced=CASE WHEN canon_synced=?"
                       " THEN ? ELSE canon_synced END WHERE thread_id=? AND provider='claude'",
                       (sum(1 for i in raw if i["k"] != "user"), before, before + len(new), tid))
        brain.ir.log("import.follow", thread=tid, items=len(new))
    if claude:
        brain.db.x("UPDATE threads SET import_offset=?, import_mtime=? WHERE id=?", (end, mtime, tid))
    else:
        brain.db.x("UPDATE threads SET import_items=?, import_mtime=? WHERE id=?", (done + len(raw), mtime, tid))


# ---- one writer: where a message for a linked thread goes ---------------------------------------
RELAY_STATUS = {"idle", "busy"}  # a pane at a permission prompt or in shell mode would take typed text as an answer


def linked(t: dict) -> bool:
    """The thread and the terminal share one Claude session (in the owner's profile)."""
    return bool(t.get("import_path")) and (t.get("imported_from") or "").startswith("claude:") and t["provider"] == "claude"


def profile_of(t: dict) -> Path:
    return Path(t["import_path"]).parents[2]  # <profile>/projects/<dir>/<session>.jsonl


def _open(t: dict) -> dict | None:
    sid = t["imported_from"].split(":", 1)[1]
    return next((d for d in _live_records(profile_of(t)) if d.get("sessionId") == sid), None)


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


def _paste(sock: str, pane: str, text: str) -> None:
    """Type a message into the pane as one bracketed paste, then submit it."""
    buf = f"brain-{uuid.uuid4().hex[:8]}"
    subprocess.run(["tmux", "-S", sock, "load-buffer", "-b", buf, "-"], input=text, text=True, check=True, timeout=10)
    subprocess.run(["tmux", "-S", sock, "paste-buffer", "-p", "-d", "-b", buf, "-t", pane], check=True, timeout=10)
    time.sleep(0.3)  # let the TUI take the paste before Enter
    subprocess.run(["tmux", "-S", sock, "send-keys", "-t", pane, "Enter"], check=True, timeout=10)


def route(t: dict) -> tuple[str, tuple[str, str] | None]:
    """'run' (closed, or open outside tmux and idle: Brain takes it over), 'relay' (type it into the open tmux pane)
    or 'hold' (open and not ready for typed input; try again later)."""
    rec = _open(t)
    if rec is None:
        return "run", None
    pane = _pane(rec)
    if pane:
        return ("relay" if rec.get("status") in RELAY_STATUS else "hold"), pane
    return ("run" if rec.get("status") == "idle" else "hold"), None


def relay(brain, t: dict, pane: tuple[str, str], text: str) -> None:
    brain._relayed.setdefault(t["id"], []).append(text)  # it comes back from the file; its message is shown already
    _paste(*pane, text)
    brain.ir.log("import.relay", thread=t["id"], pane=pane[1])


def _claude_stop(profile: Path, job: str) -> None:
    subprocess.run([shutil.which("claude") or "claude", "stop", job], env={**os.environ, "CLAUDE_CONFIG_DIR": str(profile)},
                   capture_output=True, check=True, timeout=60)


def take_over(t: dict) -> str | None:
    """Close the idle terminal that holds the session so Brain can write it. The conversation is on disk."""
    rec = _open(t)
    if rec is None:
        return None
    if rec.get("status") != "idle":
        raise RuntimeError("your terminal is working in this session; send again when it is idle")
    sid = t["imported_from"].split(":", 1)[1]
    if rec.get("kind") == "bg" and rec.get("jobId"):
        _claude_stop(profile_of(t), rec["jobId"])
        note = f"Stopped your background session {rec['jobId']} to run this here. Reopen it with: claude attach {rec['jobId']}"
    else:
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
    with path.open("rb") as f:
        data = f.read()
    brain.db.x("UPDATE threads SET import_offset=?, import_mtime=? WHERE id=?",
               (data.rfind(b"\n") + 1, path.stat().st_mtime, t["id"]))
