"""Pick up the owner's own terminal agent sessions (Claude Code, Codex) as Brain threads.

Only sessions whose working directory is inside the configured root and inside a git repo are listed. The cluster
account may be shared, so the root is what keeps teammates' sessions out. Import copies the history into Brain: the
first phone message continues it in a new native session in Brain's own config home, never in the terminal's file.
See docs/superpowers/specs/2026-10-15-session-import-design.md.
"""
from __future__ import annotations

import json
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


def _claude_live(profile: Path) -> set[str]:
    """Sessions a running `claude` holds: <profile>/sessions/<pid>.json whose pid is alive and was started then."""
    live = set()
    for f in (profile / "sessions").glob("*.json"):
        try:
            d = json.loads(f.read_text())
            stat = Path(f"/proc/{int(d['pid'])}/stat").read_text()
            if stat.rsplit(")", 1)[1].split()[19] == str(d.get("procStart")):
                live.add(d["sessionId"])
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            continue
    return live


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


def _events(brain, thread_id: str, provider: str, items: list[dict]) -> None:
    """Replay the history as phone events, grouped like live turns: each owner message opens a turn."""
    names: dict[str, str] = {}
    actor = {"kind": "import"}
    turn, last_text = None, ""

    def close() -> None:
        if turn:
            brain.events.append(thread_id, "turn.ended", {
                "turn_id": turn, "text": last_text[:2000], "counts": {}, "diffstat": {"files": 0, "added": 0, "removed": 0},
                "duration_ms": 0, "usage": {}, "stop_reason": "end_turn"}, turn_id=turn, actor=actor)

    def open_turn() -> str:
        t = f"turn_imp_{uuid.uuid4().hex[:12]}"
        brain.events.append(thread_id, "turn.started", {"turn_id": t, "provider": provider, "wake_reason": None,
                                                        "retry_of": None}, turn_id=t, actor=actor)
        return t

    for it in items:
        if it["k"] == "user":
            close()
            brain.events.append(thread_id, "user.message", {"text": it["text"]}, actor=actor)
            turn, last_text = open_turn(), ""
            continue
        if turn is None:  # history that starts mid-turn
            turn = open_turn()
        if it["k"] == "assistant":
            last_text = it["text"]
            brain.events.append(thread_id, "assistant.text", {"text": it["text"]}, turn_id=turn, actor=actor)
        elif it["k"] == "call":
            names[it["id"]] = it.get("name") or "?"
            brain.events.append(thread_id, "tool.started", {
                "tool_use_id": it["id"], "tool": it.get("name"), "summary": it.get("shell") or it.get("name"),
                "input_preview": json.dumps(it.get("input"))[:1500]}, turn_id=turn, actor=actor)
        elif it["k"] == "result":
            brain.events.append(thread_id, "tool.completed", {
                "tool_use_id": it["id"], "tool": names.get(it["id"], "?"), "ok": not it.get("error"),
                "output_preview": str(it.get("output", ""))[-4000:]}, turn_id=turn, actor=actor)
    close()


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


def _import(brain, provider: str, session_id: str, provider_override: str | None) -> dict:
    key = f"{provider}:{session_id}"
    row = brain.db.one("SELECT id FROM threads WHERE imported_from=?", (key,))
    if row:
        return brain.thread_view(brain.thread(row["id"]))
    cand = next((c for c in discover(brain.settings) if c["provider"] == provider and c["session_id"] == session_id), None)
    if cand is None:
        raise KeyError(f"no importable {provider} session {session_id}")
    _learn_secrets(brain)
    read = transcript.read_claude if provider == "claude" else transcript.read_codex
    items, _ = read(Path(cand["path"]))
    items, _ = brain.redactor.redact_obj(items)  # terminal transcripts can hold echoed tokens
    run_as = provider_override or provider
    t = brain.create_thread(_ensure_repo(brain, cand["repo_path"]), _title(brain, cand["title"]), "readonly", run_as)
    tid = t["id"]
    brain._append_transcript(tid, provider, None, items)
    _events(brain, tid, provider, items)
    cut = _cut(items)
    for name in {run_as, *brain.providers}:  # a later provider switch must not hydrate the whole history either
        brain.db.x("INSERT OR IGNORE INTO provider_sessions(thread_id, provider, native_session_id, sync_seq, updated_at,"
                   " native_seen, canon_synced) VALUES (?,?,NULL,0,?,0,?)", (tid, name, time.time(), cut))
    brain.events.append(tid, "system.notice", {"text": (
        f"Imported {len(items)} items from your terminal session {session_id} ({cand['path']}), as of now. Your next "
        f"message continues it here in a new session; the terminal's session is untouched and later terminal turns "
        f"do not appear here." + (f" The model is given the last {len(items) - cut} items; the full history stays in "
                                  f"this thread." if cut else ""))})
    # last: a failed import is never "done". updated_at is the session's own last activity, not the import time, so an
    # old session ranks below the threads actually being worked on.
    brain.db.x("UPDATE threads SET imported_from=?, updated_at=? WHERE id=?", (key, cand["last_ts"], tid))
    _arrive_read(brain, tid)
    brain.ir.log("import", thread=tid, source=key, items=len(items))
    return brain.thread_view(brain.thread(tid))
