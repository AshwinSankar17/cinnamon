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
