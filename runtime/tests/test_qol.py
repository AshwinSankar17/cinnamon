"""Quality of life: idle threads expire into Archive; SLURM jobs read as their script and thread."""
import time
from pathlib import Path

from fastapi.testclient import TestClient

from brain.api import create_app
from test_flows import _settle

DAY = 86400


def _age(brain, tid, days):
    brain.db.x("UPDATE threads SET updated_at=? WHERE id=?", (time.time() - days * DAY, tid))


def test_idle_threads_expire_but_protected_ones_never_do(brain):
    brain.settings.raw["thread_ttl_days"] = 3
    old = brain.create_thread("hi-tts-v3", "old idle", "worktree")
    fresh = brain.create_thread("hi-tts-v3", "fresh", "worktree")
    pinned = brain.create_thread("hi-tts-v3", "pinned", "worktree")
    needs = brain.create_thread("hi-tts-v3", "needs you", "worktree")
    waiting = brain.create_thread("hi-tts-v3", "waiting on a job", "worktree")
    brain.patch_thread(pinned["id"], pinned=True)
    brain.db.x("UPDATE threads SET state='needs_you' WHERE id=?", (needs["id"],))
    brain.db.x("INSERT INTO waits(id, thread_id, kind, predicate, created_at) VALUES ('w1', ?, 'slurm_job_completed', '{}', 0)",
               (waiting["id"],))
    for t in (old, pinned, needs, waiting):
        _age(brain, t["id"], 5)
    _age(brain, fresh["id"], 1)
    archived = brain.expire_threads()
    assert archived == [old["id"]]
    assert brain.thread(old["id"])["archived"] == 1
    assert all(brain.thread(t["id"])["archived"] == 0 for t in (fresh, pinned, needs, waiting))
    notes = [e["payload"].get("text", "") for e in brain.events.replay(old["id"], 0, 100) if e["type"] == "system.notice"]
    assert any("Archived after 3 idle days" in n for n in notes)
    assert brain.expire_threads() == []  # idempotent


def test_expiry_can_be_turned_off(brain):
    brain.settings.raw["thread_ttl_days"] = 0
    t = brain.create_thread("hi-tts-v3", "keep", "worktree")
    _age(brain, t["id"], 30)
    assert brain.expire_threads() == []


async def test_messaging_an_archived_thread_brings_it_back(brain):
    t = brain.create_thread("hi-tts-v3", "back", "worktree")
    brain.patch_thread(t["id"], archived=True)
    await brain.send_message(t["id"], "continue please")
    await _settle(brain, t["id"])
    assert brain.thread(t["id"])["archived"] == 0


async def test_jobs_read_as_script_and_thread(brain):
    t = brain.create_thread("hi-tts-v3", "EDIT TTS", "worktree")
    wt = Path(brain.thread(t["id"])["worktree"])
    (wt / "slurm").mkdir()
    script = wt / "slurm" / "eval_base_v0_20k.sh"
    script.write_text("#!/bin/bash\necho hi\n")
    brain.submit_job(t["id"], script, [])
    j = brain.jobs()[0]
    assert j["label"] == "eval_base_v0_20k" and j["thread_title"] == "EDIT TTS" and j["name"].startswith("brain.")
    with TestClient(create_app(brain, dev_no_auth=True)) as c:
        assert c.get("/v1/jobs").json()[0]["label"] == "eval_base_v0_20k"
