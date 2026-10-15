"""Pick up the owner's own terminal agent sessions (Claude Code, Codex) as Brain threads.

Only sessions whose working directory is inside the configured root and inside a git repo are listed. The cluster
account may be shared, so the root is what keeps teammates' sessions out. Import copies the history into Brain: the
first phone message continues it in a new native session in Brain's own config home, never in the terminal's file.
See docs/superpowers/specs/2026-10-15-session-import-design.md.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import repos, transcript
from .config import RepoConfig


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
                cwd = r.get("cwd")
                content = (r.get("message") or {}).get("content")
                if r.get("type") == "user" and isinstance(content, str):
                    first_user = content
    return None if not cwd else {"cwd": cwd, "title": title or (first_user or "Imported session")[:60]}


def _claude(profile: Path, cutoff: float) -> list[dict]:
    out, live = [], _claude_live(profile)
    for f in (profile / "projects").glob("*/*.jsonl"):
        mtime = f.stat().st_mtime
        if mtime < cutoff:
            continue
        meta = _claude_meta(f)
        if meta:
            out.append({"provider": "claude", "session_id": f.stem, "path": str(f), "last_ts": mtime,
                        "live": f.stem in live, **meta})
    return out


LIVE_WINDOW_S = 600  # Codex keeps no session registry: a rollout written in the last 10 minutes counts as open


def _codex(home: Path, cutoff: float, now: float) -> list[dict]:
    out = []
    for f in (home / "sessions").glob("*/*/*/rollout-*.jsonl"):
        mtime = f.stat().st_mtime
        if mtime < cutoff:
            continue
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
                        title = text[:60]
                        break
        if not meta or not meta.get("cwd") or meta.get("originator") == "codex_exec":
            continue
        out.append({"provider": "codex", "session_id": meta.get("id") or f.stem, "path": str(f), "cwd": meta["cwd"],
                    "title": title or "Imported Codex session", "last_ts": mtime, "live": now - mtime < LIVE_WINDOW_S})
    return out


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


def _ensure_repo(brain, path: str) -> str:
    for r in brain.settings.repos:
        if Path(r.path).resolve() == Path(path).resolve():
            return r.id
    base = Path(path).name
    rid, n = base, 2
    while brain.settings.repo(rid):
        rid, n = f"{base}-{n}", n + 1
    brain.settings.repos.append(RepoConfig(id=rid, path=path, name=base,
                                           worktree_root=str(Path(path).parent / ".brain-worktrees"), vault_root=""))
    brain.settings.save(brain.p)
    brain.ir.log("import.repo", repo=rid, path=path)
    return rid


def _events(brain, thread_id: str, items: list[dict]) -> None:
    names: dict[str, str] = {}
    actor = {"kind": "import"}
    for it in items:
        if it["k"] == "user":
            brain.events.append(thread_id, "user.message", {"text": it["text"]}, actor=actor)
        elif it["k"] == "assistant":
            brain.events.append(thread_id, "assistant.text", {"text": it["text"]}, actor=actor)
        elif it["k"] == "call":
            names[it["id"]] = it.get("name") or "?"
            brain.events.append(thread_id, "tool.started", {
                "tool_use_id": it["id"], "tool": it.get("name"), "summary": it.get("shell") or it.get("name"),
                "input_preview": json.dumps(it.get("input"))[:1500]}, actor=actor)
        elif it["k"] == "result":
            brain.events.append(thread_id, "tool.completed", {
                "tool_use_id": it["id"], "tool": names.get(it["id"], "?"), "ok": not it.get("error"),
                "output_preview": str(it.get("output", ""))[-4000:]}, actor=actor)


def candidates(brain) -> list[dict]:
    have = {r["imported_from"]: r["id"] for r in
            brain.db.q("SELECT id, imported_from FROM threads WHERE imported_from IS NOT NULL")}
    return [{**c, "thread_id": have.get(f"{c['provider']}:{c['session_id']}")} for c in discover(brain.settings)]


def import_session(brain, provider: str, session_id: str, provider_override: str | None = None) -> dict:
    key = f"{provider}:{session_id}"
    row = brain.db.one("SELECT id FROM threads WHERE imported_from=?", (key,))
    if row:
        return brain.thread_view(brain.thread(row["id"]))
    cand = next((c for c in discover(brain.settings) if c["provider"] == provider and c["session_id"] == session_id), None)
    if cand is None:
        raise KeyError(f"no importable {provider} session {session_id}")
    read = transcript.read_claude if provider == "claude" else transcript.read_codex
    items, _ = read(Path(cand["path"]))
    items, _ = brain.redactor.redact_obj(items)  # terminal transcripts can hold echoed tokens
    run_as = provider_override or provider
    t = brain.create_thread(_ensure_repo(brain, cand["repo_path"]), cand["title"], "readonly", run_as)
    tid = t["id"]
    brain.db.x("UPDATE threads SET imported_from=? WHERE id=?", (key, tid))
    brain._append_transcript(tid, provider, None, items)
    _events(brain, tid, items)
    cut = _cut(items)
    brain.db.x("INSERT INTO provider_sessions(thread_id, provider, native_session_id, sync_seq, updated_at, native_seen,"
               " canon_synced) VALUES (?,?,NULL,0,?,0,?)", (tid, run_as, time.time(), cut))
    brain.events.append(tid, "system.notice", {"text": (
        f"Imported {len(items)} items from your terminal session {session_id} ({cand['path']}). Your next message "
        f"continues it here in a new session; the terminal's session is untouched."
        + (f" The model is given the last {len(items) - cut} items; the full history stays in this thread." if cut else ""))})
    brain.ir.log("import", thread=tid, source=key, items=len(items))
    return brain.thread_view(brain.thread(tid))
