"""Cinnamon P0: pushes, quiet hours, expiry, since-you-left, policy false positives, health, settings."""
import time

import pytest
from fastapi.testclient import TestClient

from brain.api import create_app
from test_flows import _settle


@pytest.fixture
def pushes(brain, monkeypatch):
    sent = []
    monkeypatch.setattr(brain, "_notify", lambda tid, kind, title, body, data: sent.append((kind, title, body)))
    return sent


def _job(brain, tid, job_id="9001", label="v3_eval_fullb200k", state="COMPLETED"):
    brain.db.x("INSERT INTO jobs(job_id, thread_id, run_id, name, sentinel, state, submitted_at, updated_at, label)"
               " VALUES (?,?,?,?,?,?,?,?,?)", (job_id, tid, "run_x", "brain.x.y", "/tmp/s", state, time.time(), time.time(), label))


async def test_a_job_woken_turn_pushes_its_first_line(brain, pushes):
    t = brain.create_thread("hi-tts-v3", "EDIT TTS", "worktree")
    _job(brain, t["id"])
    await brain.wake(t["id"], "[Brain] SLURM job 9001 finished: COMPLETED")
    await _settle(brain, t["id"])
    done = [p for p in pushes if p[0] == "done"]
    assert len(done) == 1
    assert done[0][1] == "EDIT TTS: v3_eval_fullb200k finished"
    assert done[0][2]  # the agent's first line, not empty


async def test_an_owner_message_turn_does_not_push_done(brain, pushes):
    t = brain.create_thread("hi-tts-v3", "EDIT TTS", "worktree")
    await brain.send_message(t["id"], "hello")
    await _settle(brain, t["id"])
    assert [p for p in pushes if p[0] == "done"] == []


async def test_a_failed_turn_pushes_once_and_never_done(brain, pushes):
    t = brain.create_thread("hi-tts-v3", "EDIT TTS", "worktree")
    _job(brain, t["id"])
    await brain.wake(t["id"], "please simulate failure")
    await _settle(brain, t["id"])
    kinds = [p[0] for p in pushes]
    assert kinds.count("failed") == 1 and "done" not in kinds
    assert pushes[kinds.index("failed")][1] == "EDIT TTS: turn failed"


async def test_failed_job_push_uses_the_label(brain, pushes, monkeypatch):
    from brain import slurm
    t = brain.create_thread("hi-tts-v3", "EDIT TTS", "worktree")
    _job(brain, t["id"], job_id="9002", label="v3_gate", state="RUNNING")
    monkeypatch.setattr(brain.watcher, "poll", lambda jobs, force: [slurm.JobUpdate("9002", "FAILED", 1, "")])
    await brain.tick()
    assert ("failed", "v3_gate failed") in [(k, ti) for k, ti, _ in pushes]
