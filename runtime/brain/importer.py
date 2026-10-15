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

from . import repos


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
