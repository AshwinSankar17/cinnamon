"""Cinnamon P3 backend: activity for the roll, quota snapshots, rules + quiet hours switch, self sign-out."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from brain.api import create_app


def test_activity_has_turns_and_jobs(brain):
    t = brain.create_thread("hi-tts-v3", "EDIT TTS", "worktree")
    now = time.time()
    brain.db.x("INSERT INTO turns(turn_id, thread_id, provider, started_seq, started_at, ended_at, output_tokens)"
               " VALUES ('tu1', ?, 'fake', 1, ?, ?, 4000)", (t["id"], now - 3600, now - 3500))
    brain.db.x("INSERT INTO jobs(job_id, thread_id, run_id, name, sentinel, state, submitted_at, updated_at, label)"
               " VALUES ('j1', ?, 'r', 'n', '/s', 'FAILED', ?, ?, 'v3_gate')", (t["id"], now - 3000, now - 2000))
    a = brain.activity(t["id"])
    assert a["turns"] == [[pytest.approx(now - 3600), 4.0]]
    assert a["jobs"] == [[pytest.approx(now - 3000), pytest.approx(now - 2000), 1]]
    assert a["start"] <= now - 3600 and a["now"] >= now
    with TestClient(create_app(brain, dev_no_auth=True)) as c:
        assert c.get(f"/v1/threads/{t['id']}/activity?left_at={now - 100}").json()["left_at"] == pytest.approx(now - 100)
        assert c.get("/v1/threads/nope/activity").status_code == 404
