"""Stop uses the model's own interrupt (SIGINT) and only kills a CLI that ignores it; Retry re-runs the failed turn."""
import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from brain.providers import base
from brain.providers.codex import CodexProvider
from test_flows import _settle

# A stand-in `codex exec --json`: records each prompt, starts a tool child, then either hangs (ignoring SIGINT unless
# HONOUR_INT is set) or, once the "hung" flag exists, answers and exits like a normal turn.
FAKE_CODEX = r"""#!/bin/bash
d="$(dirname "$0")"
prompt="${@: -1}"
echo "$prompt" >> "$d/prompts"
echo '{"type":"thread.started","thread_id":"sess-1"}'
if [ -e "$d/hung" ]; then
  echo '{"type":"item.completed","item":{"id":"i1","type":"agent_message","text":"done"}}'
  echo '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}'
  exit 0
fi
touch "$d/hung"
if [ -n "$HONOUR_INT" ]; then trap 'echo native > "$d/interrupted"; kill $child; exit 130' INT; else trap '' INT; fi
sleep 300 & child=$!
echo $child > "$d/child"
wait $child
"""


@pytest.fixture
def fake_codex(tmp_path: Path) -> Path:
    d = tmp_path / "fakecodex"
    d.mkdir()
    f = d / "codex"
    f.write_text(FAKE_CODEX)
    f.chmod(0o755)
    return f


async def _wait_for(path: Path, timeout=10.0):
    end = time.time() + timeout
    while not path.exists():
        if time.time() > end:
            raise TimeoutError(path)
        await asyncio.sleep(0.05)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    st = Path(f"/proc/{pid}/stat")
    return not (st.exists() and st.read_text().split()[2] == "Z")


def _types(brain, tid):
    return [e for e in brain.events.replay(tid, 0, 500)]


async def test_stop_uses_native_interrupt_first(brain, fake_codex, monkeypatch):
    monkeypatch.setenv("HONOUR_INT", "1")
    monkeypatch.setattr(base, "STOP_GRACE", 5.0)
    brain.providers["codex"] = CodexProvider(binary=str(fake_codex))
    t = brain.create_thread("hi-tts-v3", "stop me", "worktree", provider="codex")
    await brain.send_message(t["id"], "train it")
    await _wait_for(fake_codex.parent / "child")
    started = time.time()
    await brain.stop(t["id"])
    await _settle(brain, t["id"])
    assert (fake_codex.parent / "interrupted").read_text().strip() == "native"  # its own SIGINT handler ran
    assert time.time() - started < 4  # no escalation needed
    turn = brain.db.one("SELECT * FROM turns WHERE thread_id=?", (t["id"],))
    assert turn["stop_reason"] == "cancelled"
    assert not any(e["type"] == "provider.status" for e in _types(brain, t["id"]))  # a stop is not an error


async def test_stop_kills_a_cli_that_ignores_sigint_and_its_tools(brain, fake_codex, monkeypatch):
    monkeypatch.setattr(base, "STOP_GRACE", 0.5)
    brain.providers["codex"] = CodexProvider(binary=str(fake_codex))
    t = brain.create_thread("hi-tts-v3", "hung", "worktree", provider="codex")
    await brain.send_message(t["id"], "train it")
    await _wait_for(fake_codex.parent / "child")
    child = int((fake_codex.parent / "child").read_text())
    await brain.stop(t["id"])
    await _settle(brain, t["id"])
    await asyncio.sleep(0.2)
    assert not _alive(child)
    assert brain.db.one("SELECT stop_reason FROM turns WHERE thread_id=?", (t["id"],))["stop_reason"] == "cancelled"


async def test_retry_reruns_the_stopped_turn_with_the_same_prompt(brain, fake_codex, monkeypatch):
    monkeypatch.setattr(base, "STOP_GRACE", 0.5)
    brain.providers["codex"] = CodexProvider(binary=str(fake_codex))
    t = brain.create_thread("hi-tts-v3", "retry", "worktree", provider="codex")
    await brain.send_message(t["id"], "train it")
    await _wait_for(fake_codex.parent / "child")
    await brain.stop(t["id"])
    await _settle(brain, t["id"])
    first = brain.db.one("SELECT turn_id FROM turns WHERE thread_id=?", (t["id"],))["turn_id"]

    brain.retry_turn(t["id"])
    await _settle(brain, t["id"])
    assert (fake_codex.parent / "prompts").read_text().split("\n")[:2] == ["train it", "train it"]
    evs = _types(brain, t["id"])
    assert sum(e["type"] == "user.message" for e in evs) == 1  # no duplicate message in the chat
    started = [e["payload"] for e in evs if e["type"] == "turn.started"]
    assert started[-1]["retry_of"] == first
    users = [i for i in brain.transcript(t["id"]) if i["k"] == "user"]
    assert users == [{"k": "user", "text": "train it"}]  # nor in the history other models receive
    last = brain.db.one("SELECT * FROM turns WHERE thread_id=? ORDER BY started_seq DESC LIMIT 1", (t["id"],))
    assert last["stop_reason"] == "end_turn"


async def test_retry_after_an_error_and_only_after_a_failure(brain):
    t = brain.create_thread("hi-tts-v3", "err", "worktree")
    await brain.send_message(t["id"], "please simulate failure")
    await _settle(brain, t["id"])
    assert brain.db.one("SELECT stop_reason FROM turns WHERE thread_id=?", (t["id"],))["stop_reason"] == "error"
    brain.retry_turn(t["id"])
    await _settle(brain, t["id"])
    assert brain.providers["fake"].prompts[-2:] == ["please simulate failure"] * 2

    ok = brain.create_thread("hi-tts-v3", "fine", "worktree")
    await brain.send_message(ok["id"], "hello")
    await _settle(brain, ok["id"])
    with pytest.raises(RuntimeError, match="did not fail"):
        brain.retry_turn(ok["id"])


def test_retry_over_http(brain):
    from fastapi.testclient import TestClient
    from brain.api import create_app
    with TestClient(create_app(brain, dev_no_auth=True)) as c:
        t = c.post("/v1/threads", json={"repo_id": "hi-tts-v3", "title": "http", "mode": "worktree",
                                         "message": "please simulate failure"}).json()
        for _ in range(100):
            if c.get(f"/v1/threads/{t['id']}").json().get("state") != "running":
                break
            time.sleep(0.05)
        r = c.post(f"/v1/threads/{t['id']}/retry")
        assert r.status_code == 200, r.text
        assert c.post(f"/v1/threads/{t['id']}/retry").status_code in (200, 409)  # never a 500
