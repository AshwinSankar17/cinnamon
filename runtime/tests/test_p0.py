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


def test_expiry_spares_pending_listening_tests_and_recent_jobs(brain):
    brain.settings.raw["thread_ttl_days"] = 3
    listen = brain.create_thread("hi-tts-v3", "listen", "worktree")
    job = brain.create_thread("hi-tts-v3", "job", "worktree")
    plain = brain.create_thread("hi-tts-v3", "plain", "worktree")
    brain.db.x("INSERT INTO listening_tests(id, thread_id, path, title, n_items, n_systems, created_at)"
               " VALUES ('lt1', ?, '/x', 't', 5, 2, 0)", (listen["id"],))
    _job(brain, job["id"], job_id="9003")  # finished just now
    old = time.time() - 5 * 86400
    for x in (listen, job, plain):
        brain.db.x("UPDATE threads SET updated_at=? WHERE id=?", (old, x["id"]))
    assert brain.expire_threads() == [plain["id"]]


def _st(hh, mm):
    return time.struct_time((2026, 10, 15, hh, mm, 0, 3, 288, -1))


def test_quiet_window_wraps_midnight(brain):
    brain.settings.quiet_hours = ("23:00", "07:00")
    brain.settings.raw["quiet_hours_enabled"] = True
    assert brain._quiet_now(_st(3, 40)) and brain._quiet_now(_st(23, 0))
    assert not brain._quiet_now(_st(7, 0)) and not brain._quiet_now(_st(22, 59))
    brain.settings.raw["quiet_hours_enabled"] = False
    assert not brain._quiet_now(_st(3, 40))


def test_quiet_hours_hold_everything_but_approvals_then_send_one_digest(brain, monkeypatch):
    sent = []
    monkeypatch.setattr(brain, "_push_all", lambda kind, title, body, data: sent.append((kind, title)))
    t = brain.create_thread("hi-tts-v3", "EDIT TTS", "worktree")
    muted = brain.create_thread("hi-tts-v3", "muted", "worktree")
    brain.patch_thread(muted["id"], muted=True)
    monkeypatch.setattr(brain, "_quiet_now", lambda at=None: True)
    brain._notify(t["id"], "needs_you", "Ready to listen: V3 vs V1", "5 items", {"thread_id": t["id"]})
    brain._notify(t["id"], "approval", "Push to main", "git push", {"thread_id": t["id"]})
    brain._notify(muted["id"], "done", "muted thing", "x", {"thread_id": muted["id"]})
    assert sent == [("approval", "Push to main")]
    monkeypatch.setattr(brain, "_quiet_now", lambda at=None: False)
    assert brain._flush_digest() == 1
    assert sent[-1][0] == "digest" and "1 update" in sent[-1][1]
    assert brain._flush_digest() == 0  # sent once


def test_pending_uses_the_clients_left_at_and_clamps_it(brain):
    t = brain.create_thread("hi-tts-v3", "EDIT TTS", "worktree")
    _job(brain, t["id"], job_id="9004", state="COMPLETED")
    _job(brain, t["id"], job_id="9005", state="FAILED")
    left = time.time() - 60
    s = brain.pending(left)["since"]
    assert abs(s["ts"] - left) < 1 and s["jobs_finished"] == 1 and s["jobs_failed"] == 1
    assert brain.pending(time.time() + 3600)["since"]["ts"] <= time.time()  # a phone clock in the future
    assert brain.pending(0)["since"]["ts"] >= time.time() - 7 * 86400 - 1  # at most a week back
    with TestClient(create_app(brain, dev_no_auth=True)) as c:
        assert c.get(f"/v1/pending?since={left}").json()["since"]["jobs_finished"] == 1
        assert c.get("/v1/pending?since=abc").status_code == 422


from brain.policy import PolicyContext, _out_of_scope_writes

CTX = PolicyContext(worktree="/w/t1", vault_roots=("/vaults",), scratch_roots=("/tmp",))
REAL_FALSE_POSITIVES = [
    'ls ~/.local/bin | grep -i -E "brain|cb"; env | grep -i brain | sed \'s/=.*TOKEN.*/=<redacted>/\' | head; ps -ef | grep -i brain | grep -v grep | head -5',
    "sed -i 's/x0_max_ratio: float = 1.0      # random x0 length ~ U{0..ratio\\*L1}/x0_max_ratio: float = 1.5      # random x0 length ~ U{0..ratio*L1}; >1 gives deletions/' edit_tts/edits.py && sed -i 's/x0_max_ratio: 1.0/x0_max_ratio: 1.5/' configs/base.yaml",
    "W=$PWD && cat > slurm/eval_sweep.sh <<'EOF'\n#!/bin/bash\n# Needs RUN (run dir) and STEP; writes $RUN/eval_<step>/<setting>/wer.json.\nset -eo pipefail\nEOF\necho done",
]


@pytest.mark.parametrize("cmd", REAL_FALSE_POSITIVES)
def test_real_false_positives_are_in_scope(cmd):
    assert _out_of_scope_writes(cmd, CTX) == []


def test_real_out_of_scope_writes_are_still_caught():
    assert _out_of_scope_writes("echo hi > /etc/motd", CTX) == ["/etc/motd"]
    assert _out_of_scope_writes("cat > /home/other/x.sh <<'EOF'\nhi\nEOF", CTX) == ["/home/other/x.sh"]
    assert _out_of_scope_writes("rm -rf ~/.config/foo", CTX) == ["~/.config/foo"]
