"""A message sent while a turn runs follows the model's own behaviour: Claude Code reads it at its next step
(steering); providers without steering get it as the very next turn. Nothing is dropped."""
import asyncio
import json
import time
from pathlib import Path

import pytest

from brain.providers.claude import ClaudeProvider
from brain.providers.codex import CodexProvider
from test_flows import _settle

# `claude -p --input-format stream-json`: first stdin line is the prompt; while its "tool" runs (~1.5 s) it reads
# more lines and answers them in the same turn, like Claude Code does at its next step. One result per process.
FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, os, select, sys, time, uuid
d = os.path.dirname(os.path.abspath(__file__))
sid = str(uuid.uuid4())
def out(o): print(json.dumps(o), flush=True)
first = json.loads(sys.stdin.readline())["message"]["content"]
open(os.path.join(d, "started"), "w").write("1")
out({"type": "system", "subtype": "init", "session_id": sid})
out({"type": "assistant", "session_id": sid, "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "sleep"}}]}})
seen, end = [first], time.time() + 1.5
while time.time() < end:
    r, _, _ = select.select([sys.stdin], [], [], 0.05)
    if r:
        line = sys.stdin.readline()
        if line:
            seen.append(json.loads(line)["message"]["content"])
open(os.path.join(d, "seen.json"), "w").write(json.dumps(seen))
out({"type": "user", "session_id": sid, "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "", "is_error": False}]}})
text = "answered: " + " | ".join(seen)
out({"type": "assistant", "session_id": sid, "message": {"content": [{"type": "text", "text": text}]}})
out({"type": "result", "subtype": "success", "session_id": sid, "stop_reason": "end_turn", "result": text, "usage": {}})
sys.stdin.read()  # like the real CLI: waits for more input until stdin closes
'''

# `codex exec`: no mid-turn input. Each run records its prompt and takes ~1.5 s.
FAKE_CODEX = r'''#!/bin/bash
d="$(dirname "$0")"
echo "${@: -1}" >> "$d/prompts"
touch "$d/started"
echo '{"type":"thread.started","thread_id":"s1"}'
sleep 1.5
echo '{"type":"item.completed","item":{"id":"i1","type":"agent_message","text":"ok"}}'
echo '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}'
'''


def _bin(tmp_path: Path, name: str, body: str) -> Path:
    d = tmp_path / f"fake{name}"
    d.mkdir()
    f = d / name
    f.write_text(body)
    f.chmod(0o755)
    return f


async def _started(f: Path, timeout=10.0):
    end = time.time() + timeout
    while not (f.parent / "started").exists():
        assert time.time() < end, "turn did not start"
        await asyncio.sleep(0.02)


def _notices(brain, tid):
    return [e["payload"]["text"] for e in brain.events.replay(tid, 0, 500) if e["type"] == "system.notice"]


async def test_claude_reads_a_mid_turn_message_in_the_same_turn(brain, tmp_path):
    claude = _bin(tmp_path, "claude", FAKE_CLAUDE)
    brain.providers["claude"] = ClaudeProvider(binary=str(claude))
    t = brain.create_thread("hi-tts-v3", "steer", "worktree", provider="claude")
    await brain.send_message(t["id"], "train it")
    await _started(claude)
    await brain.send_message(t["id"], "use lr 3e-4 instead")
    await _settle(brain, t["id"])
    assert json.loads((claude.parent / "seen.json").read_text()) == ["train it", "use lr 3e-4 instead"]
    assert brain.db.one("SELECT COUNT(*) AS n FROM turns WHERE thread_id=?", (t["id"],))["n"] == 1
    assert "Sent into the running turn." in _notices(brain, t["id"])
    users = [i["text"] for i in brain.transcript(t["id"]) if i["k"] == "user"]
    assert users == ["train it", "use lr 3e-4 instead"]  # the next model sees it too


async def test_without_steering_the_message_runs_next_even_if_the_thread_now_waits(brain, tmp_path):
    codex = _bin(tmp_path, "codex", FAKE_CODEX)
    brain.providers["codex"] = CodexProvider(binary=str(codex))
    t = brain.create_thread("hi-tts-v3", "queue", "worktree", provider="codex")
    await brain.send_message(t["id"], "submit the job")
    await _started(codex)
    # the turn leaves the thread waiting on a SLURM job: this used to drop the queued message
    brain.db.x("INSERT INTO waits(id, thread_id, kind, predicate, created_at) VALUES ('w1', ?, 'slurm_job_completed', '{}', 0)",
               (t["id"],))
    await brain.send_message(t["id"], "also log the loss every 10 steps")
    assert "Queued: runs as soon as this turn ends." in _notices(brain, t["id"])
    for _ in range(200):
        if (codex.parent / "prompts").read_text().count("\n") >= 2 and not brain._tasks.get(t["id"]):
            break
        await asyncio.sleep(0.05)
    assert (codex.parent / "prompts").read_text().splitlines() == ["submit the job", "also log the loss every 10 steps"]


async def test_stop_hands_queued_messages_back_and_does_not_run_them(brain, tmp_path, monkeypatch):
    from brain.providers import base
    monkeypatch.setattr(base, "STOP_GRACE", 0.5)
    codex = _bin(tmp_path, "codex", FAKE_CODEX)
    brain.providers["codex"] = CodexProvider(binary=str(codex))
    t = brain.create_thread("hi-tts-v3", "stop", "worktree", provider="codex")
    await brain.send_message(t["id"], "train it")
    await _started(codex)
    await brain.send_message(t["id"], "actually wait")
    brain._queued[t["id"]].insert(0, ("wake", "[Brain] SLURM job 1 finished"))
    assert await brain.stop(t["id"]) == ["actually wait"]
    await _settle(brain, t["id"])
    assert (codex.parent / "prompts").read_text().splitlines() == ["train it"]
    assert any("back in your composer" in n for n in _notices(brain, t["id"]))
    # the job wake-up is not lost: it goes with the owner's next message
    (codex.parent / "started").unlink()
    await brain.send_message(t["id"], "go on")
    await _settle(brain, t["id"])
    assert (codex.parent / "prompts").read_text().split("\n", 1)[1].startswith("[Brain] SLURM job 1 finished")


async def test_messages_sent_while_paused_run_on_resume(brain):
    await brain.set_paused(True)
    t = brain.create_thread("hi-tts-v3", "paused", "worktree")
    await brain.send_message(t["id"], "hello")
    assert not brain.providers["fake"].prompts
    await brain.set_paused(False)
    await _settle(brain, t["id"])
    assert brain.providers["fake"].prompts == ["hello"]
