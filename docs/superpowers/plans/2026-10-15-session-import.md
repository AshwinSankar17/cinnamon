# Session import — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Brain lists the owner's own Claude Code and Codex terminal sessions (under the owner's folder, in git repos) and imports any of them as a read-only thread with its full history. The owner can continue it from the phone in a fork, never touching the terminal's session file.

**Architecture:** A new `runtime/brain/importer.py` does discovery and import. Discovery is a cheap scan of session files, with string filters before any JSON parse. Import does four things:
- parses with the existing `transcript.read_claude` / `read_codex`;
- stores the redacted transcript and phone events;
- registers the repo;
- seeds `provider_sessions.canon_synced`, so the existing `_catch_up` hydrates only the recent part on the first phone message.

A thin API, admin route, CLI and one app screen sit on top.

**Tech Stack:** Python 3.10, FastAPI, SQLite (one additive column), pytest; Expo / React Native (TypeScript).

**Spec:** `docs/superpowers/specs/2026-10-15-session-import-design.md`

## Global Constraints

- **Tests:** `runtime/.venv/bin/pytest -q runtime` from the repo root. It starts at 93 passed and every task leaves it green.
- **Typecheck:** `node mobile/node_modules/.bin/tsc --noEmit -p mobile`. `npm` is not on PATH.
- **Never read or write the owner's real profiles or `~/.brain` in tests.** Fixtures live under `tmp_path`, and the `brain` fixture's root is temporary.
- **Never modify a source session file.** A test checks its sha256.
- **Scope rules:**
  - only sessions with `cwd` under `import.root`;
  - only inside a git repo;
  - active within `import.days` (by file mtime);
  - Claude sessions with a `continuedInSessionId` record are skipped;
  - Codex sessions with `originator == "codex_exec"` are skipped.
- **Transcript items are redacted** with `brain.redactor.redact_obj` before storage. Events are redacted by `events.append`.
- **Additive schema only:** `("threads", "imported_from", "TEXT")` in `db.MIGRATIONS`.
- **Commits:** end each message with
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_0137qnrn1B2jvqAHCTFRm4c4`.

## Review Focus

1. **A teammate's session whose cwd shares the owner's prefix** (e.g. `/projects/data/ttsteam/ashwin2/...`). It must not match `/projects/data/ttsteam/ashwin`. Path containment must be by path component. → Task 1 test.
2. **A 50 MB session file.** Listing must not JSON-parse every line. Import may parse it fully, but the model budget must cap what is hydrated. → Task 1 test (unparsed lines are skipped) and Task 3 test (budget).
3. **A session whose cwd was deleted, or is not a git repo.** It is skipped silently, with no crash. → Task 1 test.
4. **Importing the same session twice, or two repos with the same folder name.** The same thread comes back, and the second repo gets a suffixed id. → Task 3 tests.
5. **A secret in the terminal transcript** (e.g. a token echoed by a command). It must not land in `transcript_items` or events. → Task 3 test.

---

### Task 1: Discover Claude sessions

**Files:**
- Create: `runtime/brain/importer.py`
- Test: `runtime/tests/test_import.py`

**Interfaces:**
- Produces:
  - `importer.config(settings) -> dict`, with keys `root: str|None`, `claude_dirs: list[str]`, `codex_dirs: list[str]` and `days: float`.
  - `importer.discover(settings, now: float | None = None) -> list[dict]`. Each dict has `provider, session_id, path, cwd, repo_path, title, last_ts, live`. The list is sorted by `last_ts` descending.

- [ ] **Step 1: Write the failing tests** in `runtime/tests/test_import.py`:

```python
"""Import the owner's terminal agent sessions as threads (docs/superpowers/specs/2026-10-15-session-import-design.md)."""
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from brain import importer


def _claude_session(profile: Path, cwd: Path, sid: str, records: list[dict] | None = None, title: str | None = None,
                    continued: str | None = None) -> Path:
    d = profile / "projects" / str(cwd).replace("/", "-")
    d.mkdir(parents=True, exist_ok=True)
    recs = records or [
        {"type": "user", "cwd": str(cwd), "sessionId": sid, "message": {"role": "user", "content": "train the model"}},
        {"type": "assistant", "cwd": str(cwd), "sessionId": sid,
         "message": {"role": "assistant", "content": [{"type": "text", "text": "Submitted job 42."}]}},
    ]
    if title:
        recs.append({"type": "ai-title", "aiTitle": title, "sessionId": sid})
    if continued:
        recs.append({"type": "continued-in", "continuedInSessionId": continued, "sessionId": sid})
    f = d / f"{sid}.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    return f


@pytest.fixture
def owner(tmp_path, git_repo, brain):
    """An owner root holding the fixture repo, a Claude profile, and import settings pointing at both."""
    profile = tmp_path / "profile"
    brain.settings.raw["import"] = {"root": str(tmp_path), "claude_dirs": [str(profile)], "codex_dirs": [], "days": 30}
    return {"root": tmp_path, "profile": profile, "repo": git_repo}


def test_discover_lists_the_owners_git_sessions_newest_first(owner, brain):
    _claude_session(owner["profile"], owner["repo"], "s-old", title="Old work")
    time.sleep(0.01)
    _claude_session(owner["profile"], owner["repo"], "s-new", title="Eval the 200k checkpoint")
    found = importer.discover(brain.settings)
    assert [c["session_id"] for c in found] == ["s-new", "s-old"]
    c = found[0]
    assert c["provider"] == "claude" and c["title"] == "Eval the 200k checkpoint"
    assert c["repo_path"] == str(owner["repo"]) and c["live"] is False


def test_discover_skips_what_is_not_the_owners_or_not_importable(owner, brain, tmp_path):
    other = tmp_path.parent / (tmp_path.name + "2") / "repo"  # shares the owner's root as a string prefix
    other.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    plain = owner["root"] / "notes"  # not a git repo
    plain.mkdir()
    _claude_session(owner["profile"], other, "s-teammate")
    _claude_session(owner["profile"], plain, "s-plain")
    _claude_session(owner["profile"], owner["root"] / "deleted", "s-gone")
    _claude_session(owner["profile"], owner["repo"], "s-moved-on", continued="s-next")
    old = _claude_session(owner["profile"], owner["repo"], "s-ancient")
    os.utime(old, (time.time() - 60 * 86400,) * 2)
    assert importer.discover(brain.settings) == []


def test_discover_needs_a_root(brain):
    brain.settings.raw.pop("import", None)
    assert importer.discover(brain.settings) == []


def test_discover_does_not_parse_every_line(owner, brain):
    big = [{"type": "user", "cwd": str(owner["repo"]), "sessionId": "s-big", "message": {"role": "user", "content": "go"}}]
    f = _claude_session(owner["profile"], owner["repo"], "s-big", records=big)
    with f.open("a") as fh:
        fh.write("not json at all\n" * 1000)  # lines with no key the scan looks for are never parsed
    assert [c["session_id"] for c in importer.discover(brain.settings)] == ["s-big"]


def test_a_session_open_in_a_terminal_is_marked_live(owner, brain):
    _claude_session(owner["profile"], owner["repo"], "s-live")
    (owner["profile"] / "sessions").mkdir()
    stat = Path(f"/proc/{os.getpid()}/stat").read_text()
    start = stat.rsplit(")", 1)[1].split()[19]
    (owner["profile"] / "sessions" / f"{os.getpid()}.json").write_text(
        json.dumps({"pid": os.getpid(), "sessionId": "s-live", "procStart": start}))
    assert importer.discover(brain.settings)[0]["live"] is True
```

- [ ] **Step 2: Run them to verify they fail**

Run: `runtime/.venv/bin/pytest -q runtime/tests/test_import.py`
Expected: FAIL with `ImportError: cannot import name 'importer'`.

- [ ] **Step 3: Implement** `runtime/brain/importer.py`:

```python
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


def discover(settings, now: float | None = None) -> list[dict]:
    c = config(settings)
    if not c["root"]:
        return []
    cutoff = (now or time.time()) - c["days"] * 86400
    found = []
    for d in c["claude_dirs"]:
        found += _claude(Path(d).expanduser(), cutoff)
    out = []
    for s in found:
        if not _inside(s["cwd"], c["root"]):
            continue
        top = _toplevel(s["cwd"])
        if top and _inside(top, c["root"]):
            out.append({**s, "repo_path": top})
    return sorted(out, key=lambda s: s["last_ts"], reverse=True)
```

- [ ] **Step 4: Run the tests**

Run: `runtime/.venv/bin/pytest -q runtime/tests/test_import.py && runtime/.venv/bin/pytest -q runtime`
Expected: 5 passed, and the suite is green.

`git_repo` resolves through `tmp_path`, so `repo_path` must equal `str(owner["repo"])`. If the platform's tmp is a symlink, `git rev-parse --show-toplevel` returns the resolved path. In that case compare `Path(...).resolve()` on both sides in the first test and ledger a ruling.

- [ ] **Step 5: Commit**

```bash
git add runtime/brain/importer.py runtime/tests/test_import.py
git commit -m "Import: discover the owner's Claude sessions in git repos under the import root"
```

---

### Task 2: Discover Codex sessions

**Files:**
- Modify: `runtime/brain/importer.py`
- Test: `runtime/tests/test_import.py`

**Interfaces:**
- Produces: Codex candidates with `provider="codex"`, `session_id` = `session_meta.payload.id`, and `live` = file modified within the last 10 minutes.

- [ ] **Step 1: Write the failing test**

```python
def _codex_session(home: Path, cwd: Path, sid: str, originator: str = "codex_cli_rs") -> Path:
    d = home / "sessions" / "2026" / "10" / "15"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"rollout-2026-10-15T10-00-00-{sid}.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in [
        {"type": "session_meta", "payload": {"id": sid, "cwd": str(cwd), "originator": originator}},
        {"type": "response_item", "payload": {"type": "message", "role": "user",
                                              "content": [{"type": "input_text", "text": "profile the loader"}]}},
    ]) + "\n")
    return f


def test_discover_lists_interactive_codex_sessions_only(owner, brain, tmp_path):
    home = tmp_path / "codexhome"
    brain.settings.raw["import"]["codex_dirs"] = [str(home)]
    _codex_session(home, owner["repo"], "c-1")
    _codex_session(home, owner["repo"], "c-auto", originator="codex_exec")
    found = importer.discover(brain.settings)
    assert [(c["provider"], c["session_id"]) for c in found] == [("codex", "c-1")]
    assert found[0]["title"] == "profile the loader" and found[0]["live"] is True  # written just now
```

- [ ] **Step 2: Run it.** Expected: FAIL, because `found == []`.

- [ ] **Step 3: Implement.** Add to `importer.py`:

```python
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
```

In `discover`, after the Claude loop, add:

```python
    for d in c["codex_dirs"]:
        found += _codex(Path(d).expanduser(), cutoff, now or time.time())
```

- [ ] **Step 4: Run the tests**

Run: `runtime/.venv/bin/pytest -q runtime/tests/test_import.py && runtime/.venv/bin/pytest -q runtime`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/brain/importer.py runtime/tests/test_import.py
git commit -m "Import: discover interactive Codex sessions"
```

---

### Task 3: Import a session as a thread

**Files:**
- Modify: `runtime/brain/db.py` (MIGRATIONS), `runtime/brain/importer.py`
- Test: `runtime/tests/test_import.py`

**Interfaces:**
- Consumes: `discover`; `Brain.create_thread(repo_id, title, mode, provider)`; `Brain._append_transcript(thread_id, provider, turn_id, items)`; `brain.events.append(thread_id, type_, payload, actor=...)`; `transcript.read_claude` / `read_codex`.
- Produces:
  - `importer.import_session(brain, provider: str, session_id: str, provider_override: str | None = None) -> dict` (a thread view), which raises `KeyError` if the session is not importable.
  - `importer.candidates(brain) -> list[dict]`: `discover` plus `thread_id` (or `None`) per candidate.
  - `importer.BUDGET_BYTES = 400_000`.

- [ ] **Step 1: Write the failing tests**

```python
SECRET = "wandb-secret-0123456789"  # the brain fixture's redactor knows this value


def _long_session(owner, sid: str, n: int) -> Path:
    recs = []
    for i in range(n):
        recs.append({"type": "user", "cwd": str(owner["repo"]), "sessionId": sid,
                     "message": {"role": "user", "content": f"step {i} " + "x" * 2000}})
        recs.append({"type": "assistant", "cwd": str(owner["repo"]), "sessionId": sid,
                     "message": {"role": "assistant", "content": [{"type": "text", "text": f"done {i}"}]}})
    return _claude_session(owner["profile"], owner["repo"], sid, records=recs, title="Long one")


def test_import_creates_a_readonly_thread_with_history_and_events(owner, brain):
    recs = [
        {"type": "user", "cwd": str(owner["repo"]), "sessionId": "s1", "message": {"role": "user", "content": "check auth"}},
        {"type": "assistant", "cwd": str(owner["repo"]), "sessionId": "s1", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "echo $WANDB_API_KEY"}}]}},
        {"type": "user", "cwd": str(owner["repo"]), "sessionId": "s1", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": SECRET}]}},
        {"type": "assistant", "cwd": str(owner["repo"]), "sessionId": "s1",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "Key is set."}]}},
    ]
    src = _claude_session(owner["profile"], owner["repo"], "s1", records=recs, title="Auth check")
    before = hashlib.sha256(src.read_bytes()).hexdigest()
    t = importer.import_session(brain, "claude", "s1", provider_override="fake")
    assert t["title"] == "Auth check" and t["mode"] == "readonly" and t["provider"] == "fake"
    assert [i["k"] for i in brain.transcript(t["id"])] == ["user", "call", "result", "assistant"]
    assert SECRET not in json.dumps(brain.transcript(t["id"]))
    types = [e["type"] for e in brain.events.replay(t["id"], 0, 100)]
    assert ["user.message", "tool.started", "tool.completed", "assistant.text"] == [x for x in types if x in
            ("user.message", "tool.started", "tool.completed", "assistant.text")]
    assert SECRET not in json.dumps(brain.events.replay(t["id"], 0, 100))
    assert hashlib.sha256(src.read_bytes()).hexdigest() == before  # the terminal's file is never touched


def test_import_is_idempotent_and_lists_the_thread(owner, brain):
    _claude_session(owner["profile"], owner["repo"], "s2")
    a = importer.import_session(brain, "claude", "s2", provider_override="fake")
    b = importer.import_session(brain, "claude", "s2", provider_override="fake")
    assert a["id"] == b["id"]
    assert [c["thread_id"] for c in importer.candidates(brain)] == [a["id"]]


def test_import_registers_a_new_repo_with_a_unique_id(owner, brain, tmp_path):
    other = tmp_path / "x" / "hi-tts-v3"  # same folder name as the fixture repo, different path
    other.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    _claude_session(owner["profile"], other, "s3")
    t = importer.import_session(brain, "claude", "s3", provider_override="fake")
    repo = brain.settings.repo(t["repo_id"])
    assert t["repo_id"] == "hi-tts-v3-2" and repo.path == str(other) and repo.vault_root == ""


def test_import_hands_the_model_only_the_recent_part(owner, brain):
    _long_session(owner, "s4", 400)  # ~800 KB of items
    t = importer.import_session(brain, "claude", "s4", provider_override="fake")
    row = brain.db.one("SELECT * FROM provider_sessions WHERE thread_id=?", (t["id"],))
    items = brain.transcript(t["id"])
    tail = items[row["canon_synced"]:]
    assert 0 < row["canon_synced"] < len(items) and tail[0]["k"] == "user"
    assert sum(len(json.dumps(i)) for i in tail) <= importer.BUDGET_BYTES


def test_unknown_sessions_are_not_importable(owner, brain):
    with pytest.raises(KeyError):
        importer.import_session(brain, "claude", "nope", provider_override="fake")
```

- [ ] **Step 2: Run them.** Expected: FAIL with `AttributeError: module 'brain.importer' has no attribute 'import_session'`.

- [ ] **Step 3: Add the column.** In `runtime/brain/db.py` `MIGRATIONS`, append:

```python
    ("threads", "imported_from", "TEXT"),                                 # "<provider>:<session id>" of an imported terminal session
```

- [ ] **Step 4: Implement.** Add to `importer.py`. Extend the top imports to `from . import repos, transcript`, and add `from .config import RepoConfig`.

```python
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
    have = {r["imported_from"]: r["id"] for r in brain.db.q("SELECT id, imported_from FROM threads WHERE imported_from IS NOT NULL")}
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
```

- [ ] **Step 5: Run the tests**

Run: `runtime/.venv/bin/pytest -q runtime/tests/test_import.py && runtime/.venv/bin/pytest -q runtime`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add runtime/brain/db.py runtime/brain/importer.py runtime/tests/test_import.py
git commit -m "Import: a terminal session becomes a read-only thread with redacted history, events and a model budget

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0137qnrn1B2jvqAHCTFRm4c4"
```

---

### Task 4: Continuing from the phone hydrates only the recent part

This checks that the existing `_catch_up` machinery does the right thing with an imported thread.

**Files:**
- Test: `runtime/tests/test_import.py`
- Modify: only if the test fails. The likely spot is `core._run_turn`'s `missing` computation; ledger a ruling if you change it.

**Interfaces:**
- Consumes: `FakeProvider.hydrated` (a list of item lists passed to `hydrate`) and `FakeProvider.prompts`.

- [ ] **Step 1: Write the test**

```python
async def test_the_first_phone_message_hydrates_the_recent_part_then_runs(owner, brain):
    from test_flows import _settle
    src = _long_session(owner, "s5", 400)
    before = hashlib.sha256(src.read_bytes()).hexdigest()
    t = importer.import_session(brain, "claude", "s5", provider_override="fake")
    cut = brain.db.one("SELECT canon_synced FROM provider_sessions WHERE thread_id=?", (t["id"],))["canon_synced"]
    items = brain.transcript(t["id"])
    await brain.send_message(t["id"], "what was the last step?")
    await _settle(brain, t["id"])
    fake = brain.providers["fake"]
    assert fake.hydrated == [items[cut:]]
    assert fake.prompts[-1] == "what was the last step?"
    assert hashlib.sha256(src.read_bytes()).hexdigest() == before
```

- [ ] **Step 2: Run it**

Run: `runtime/.venv/bin/pytest -q runtime/tests/test_import.py -k hydrates`
Expected: PASS. This exercises existing code. If it passes on the first run, run it once more with `canon_synced` forced to 0 in a scratch edit of the test to see it fail, which proves it can fail; revert the scratch edit; ledger "characterization test of existing catch-up". If it FAILS, use superpowers:systematic-debugging and fix the cause in `core._run_turn`.

- [ ] **Step 3: Commit**

```bash
git add runtime/tests/test_import.py
git commit -m "Import: the first phone message hydrates only the recent part, in a fork"
```

---

### Task 5: API, admin route and `brain import`

**Files:**
- Modify: `runtime/brain/api.py`, `runtime/brain/cli.py`
- Test: `runtime/tests/test_import.py`

**Interfaces:**
- Produces:
  - `GET /v1/imports` → `candidates(brain)`.
  - `POST /v1/imports {provider, session_id}` → thread view, or 404.
  - `POST /internal/imports` (admin), with a body that is one of:
    - `{"configure": {...}}`, which merges into `settings.raw["import"]` and saves;
    - `{"list": true}`;
    - `{"all": true}`;
    - `{"provider", "session_id"}`.
  - CLI: `brain import [--root DIR] [--claude-dir DIR]... [--codex-dir DIR]... [--days N] [--list | --all | SESSION_ID]`.

- [ ] **Step 1: Write the failing test**

```python
def test_api_lists_and_imports(owner, brain):
    from fastapi.testclient import TestClient
    from brain.api import create_app
    _claude_session(owner["profile"], owner["repo"], "s6", title="From the phone")
    with TestClient(create_app(brain, dev_no_auth=True)) as c:
        lst = c.get("/v1/imports").json()
        assert [(x["session_id"], x["thread_id"]) for x in lst] == [("s6", None)]
        brain.providers["claude"] = brain.providers["fake"]  # the API imports as the session's own provider
        t = c.post("/v1/imports", json={"provider": "claude", "session_id": "s6"}).json()
        assert t["title"] == "From the phone"
        assert c.post("/v1/imports", json={"provider": "claude", "session_id": "nope"}).status_code == 404
        r = c.post("/internal/imports", json={"configure": {"days": 7}}, headers={"X-Brain-Admin-Token": brain.admin_token})
        assert r.status_code == 200 and brain.settings.raw["import"]["days"] == 7
```

- [ ] **Step 2: Run it.** Expected: FAIL with 404 on `/v1/imports`.

- [ ] **Step 3: Implement the API.** In `api.py`:
  - Import `importer` next to the other `brain` imports.
  - Define the request model next to the other models:

    ```python
    class ImportReq(BaseModel):
        provider: str
        session_id: str
    ```

  - Add, next to `/v1/pending`:

```python
    @app.get("/v1/imports")
    def imports(device: str = Depends(auth)) -> list[dict]:
        return importer.candidates(brain)

    @app.post("/v1/imports")
    def do_import(b: ImportReq, device: str = Depends(auth)) -> dict:
        try:
            return importer.import_session(brain, b.provider, b.session_id)
        except KeyError as e:
            raise HTTPException(404, str(e))
```

  - Add next to `/internal/endpoint`:

```python
    @app.post("/internal/imports")
    def internal_imports(body: dict, _: None = Depends(admin_auth)) -> object:
        if body.get("configure"):
            brain.settings.raw["import"] = {**(brain.settings.raw.get("import") or {}), **body["configure"]}
            brain.settings.save(brain.p)
            return importer.config(brain.settings)
        if body.get("list"):
            return importer.candidates(brain)
        if body.get("all"):
            return [importer.import_session(brain, c["provider"], c["session_id"]) for c in importer.candidates(brain)
                    if not c["thread_id"]]
        try:
            return importer.import_session(brain, body["provider"], body["session_id"])
        except KeyError as e:
            raise HTTPException(404, str(e))
```

- [ ] **Step 4: Implement the CLI.** In `cli.py`, add:

```python
def cmd_import(a: argparse.Namespace) -> int:
    if not _brain_running():
        print("Start Brain first (`brain serve`); import runs inside it.")
        return 1
    conf = {k: v for k, v in {"root": a.root and str(Path(a.root).expanduser().resolve()),
                              "claude_dirs": a.claude_dir, "codex_dirs": a.codex_dir, "days": a.days}.items() if v}
    if conf:
        code, res = _internal("/internal/imports", {"configure": conf})
        if code != 200:
            print(f"failed: {res}")
            return 1
    if a.session:
        code, res = _internal("/internal/imports", {"provider": a.provider, "session_id": a.session})
        print(f"imported as thread {res['id']}: {res['title']}" if code == 200 else f"failed: {res}")
        return 0 if code == 200 else 1
    code, res = _internal("/internal/imports", {"all": True} if a.all else {"list": True})
    if code != 200:
        print(f"failed: {res}")
        return 1
    for c in res:
        if a.all:
            print(f"imported {c['id']}: {c['title']}")
        else:
            state = f"thread {c['thread_id']}" if c["thread_id"] else ("open in a terminal" if c["live"] else "not imported")
            print(f"{c['provider']:6} {c['session_id']}  {Path(c['repo_path']).name:20} {c['title'][:50]:50} {state}")
    if not res:
        print("No importable sessions. Set the folder with --root and the profiles with --claude-dir / --codex-dir.")
    return 0
```

Register it next to `endpoint` in the parser setup:

```python
    im = sub.add_parser("import", help="list or import your terminal Claude/Codex sessions as threads")
    im.add_argument("session", nargs="?", help="session id to import (see --list)")
    im.add_argument("--provider", default="claude", choices=["claude", "codex"])
    im.add_argument("--list", action="store_true")
    im.add_argument("--all", action="store_true", help="import every listed session not imported yet")
    im.add_argument("--root", help="only sessions whose folder is under this (yours)")
    im.add_argument("--claude-dir", action="append", help="a Claude profile folder, e.g. ~/.claude-profiles/you")
    im.add_argument("--codex-dir", action="append", help="a Codex home, e.g. ~/.codex")
    im.add_argument("--days", type=float)
    im.set_defaults(fn=cmd_import)
```

- [ ] **Step 5: Run the tests**

Run: `runtime/.venv/bin/pytest -q runtime/tests/test_import.py && runtime/.venv/bin/pytest -q runtime && runtime/.venv/bin/brain import --help`
Expected: PASS, and the help text lists the flags.

- [ ] **Step 6: Commit**

```bash
git add runtime/brain/api.py runtime/brain/cli.py runtime/tests/test_import.py
git commit -m "Import: GET/POST /v1/imports, admin route, and \`brain import\`"
```

---

### Task 6: The Import screen in the app

**Files:**
- Create: `mobile/app/import.tsx`
- Modify: `mobile/src/types.ts`, `mobile/src/api.ts`, `mobile/app/(tabs)/threads.tsx` (header button)

**Interfaces:**
- Consumes: `GET /v1/imports`, `POST /v1/imports`.
- Produces:
  - `api.imports(): Promise<ImportCandidate[]>`;
  - `api.importSession(provider, session_id): Promise<Thread>`;
  - the route `/import`.

- [ ] **Step 1: Types and client.**

In `mobile/src/types.ts`, add:

```ts
/** GET /v1/imports: one of the owner's terminal sessions Brain can pick up (runtime/brain/importer.py). */
export type ImportCandidate = {
  provider: 'claude' | 'codex';
  session_id: string;
  path: string;
  cwd: string;
  repo_path: string;
  title: string;
  last_ts: number;
  live: boolean;
  thread_id: string | null;
};
```

In `mobile/src/api.ts`:
- Add `ImportCandidate` to the type import from `./types`.
- Add next to `pending`:

```ts
  imports: () => authed<ImportCandidate[]>('GET', '/v1/imports'),
  importSession: (provider: string, session_id: string) => authed<Thread>('POST', '/v1/imports', { body: { provider, session_id } }),
```

- [ ] **Step 2: The screen.** Create `mobile/app/import.tsx`:

```tsx
import { useCallback, useEffect, useState } from 'react';
import { ScrollView, Text, View } from 'react-native';
import { router } from 'expo-router';
import { BackHeader, Button, EmptyNote, ListCard, Mono, SectionLabel } from '@/components/ui';
import { api, describeError } from '@/src/api';
import { ago } from '@/src/format';
import { colors, type } from '@/src/theme';
import type { ImportCandidate } from '@/src/types';

/** Your terminal Claude/Codex sessions, by repo: import one to read and continue it here (a fork; the terminal's is untouched). */
export default function ImportScreen() {
  const [list, setList] = useState<ImportCandidate[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    api.imports().then(setList).catch((e) => setErr(describeError(e)));
  }, []);
  useEffect(load, [load]);

  const open = async (c: ImportCandidate) => {
    if (c.thread_id) return router.push(`/thread/${encodeURIComponent(c.thread_id)}`);
    setBusy(c.session_id);
    try {
      const t = await api.importSession(c.provider, c.session_id);
      router.push(`/thread/${encodeURIComponent(t.id)}`);
    } catch (e) {
      setErr(describeError(e));
    } finally {
      setBusy(null);
    }
  };

  const byRepo = new Map<string, ImportCandidate[]>();
  for (const c of list ?? []) byRepo.set(c.repo_path, [...(byRepo.get(c.repo_path) ?? []), c]);

  return (
    <View style={{ flex: 1, backgroundColor: colors.ground }}>
      <BackHeader title="Import sessions" subtitle="From your terminal" surface={colors.ground} />
      <ScrollView contentContainerStyle={{ padding: 16, gap: 14 }}>
        {err ? <Text style={[type.small, { color: colors.failed }]}>{err}</Text> : null}
        {list && !list.length ? (
          <EmptyNote>No sessions to import. On the cluster run: brain import --root ~/your-folder --claude-dir ~/.claude</EmptyNote>
        ) : null}
        {[...byRepo.entries()].map(([repo, items]) => (
          <View key={repo} style={{ gap: 6 }}>
            <SectionLabel>{repo.split('/').pop() ?? repo}</SectionLabel>
            <ListCard>
              {items.map((c) => (
                <View key={c.session_id} style={{ flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 10 }}>
                  <View style={{ flex: 1, gap: 2 }}>
                    <Text style={type.body} numberOfLines={2}>{c.title}</Text>
                    <Mono>{`${c.provider} · ${ago(c.last_ts)}${c.live ? ' · open in a terminal' : ''}`}</Mono>
                  </View>
                  <Button compact label={c.thread_id ? 'Open' : 'Import'} kind={c.thread_id ? 'secondary' : 'primary'}
                    busy={busy === c.session_id} onPress={() => open(c)} accessibilityLabel={`${c.thread_id ? 'Open' : 'Import'} ${c.title}`} />
                </View>
              ))}
            </ListCard>
          </View>
        ))}
      </ScrollView>
    </View>
  );
}
```

- [ ] **Step 3: Header entry point.** In `mobile/app/(tabs)/threads.tsx`, change the `TabHeader` `right` to hold two buttons. Add `View` to the `react-native` import if it is missing.

```tsx
        right={
          <View style={{ flexDirection: 'row', gap: 8 }}>
            <IconButton name="refresh" label="Import terminal sessions" bordered onPress={() => router.push('/import')} />
            <IconButton name="plus" label="Start a thread, run or idea" bordered onPress={() => router.push('/new')} />
          </View>
        }
```

- [ ] **Step 4: Typecheck**

Run: `node mobile/node_modules/.bin/tsc --noEmit -p mobile`
Expected: exit 0.

- [ ] **Step 5: Commit**

```bash
git add mobile/app/import.tsx mobile/src/types.ts mobile/src/api.ts "mobile/app/(tabs)/threads.tsx"
git commit -m "App: Import screen lists terminal sessions by repo; Import or Open"
```

---

## Done when

- `runtime/.venv/bin/pytest -q runtime` is green, with `test_import.py` added, and the typecheck exits 0.
- **On a throwaway Brain only** (`BRAIN_ROOT=$(mktemp -d)`), `brain import --root /projects/data/ttsteam/ashwin --claude-dir ~/.claude-profiles/ash --list` lists the owner's git-repo sessions (about 9 today) and nothing from other users' folders. This reads the owner's session files and does not write to them.
- **The owner's real Brain** is configured by the owner, after merge and a restart, with the same command.
