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


from brain import quota

# The shape a real get_usage control_response carries (claude 2.x, 2026-10-15): windows nest under rate_limits.
CLAUDE_USAGE = {"subscription_type": "team", "rate_limits_available": True,
                "rate_limits": {"five_hour": {"utilization": 4, "resets_at": "2026-10-15T07:09:00Z"},
                                "seven_day": {"utilization": 21, "resets_at": "2026-10-17T18:59:00Z"},
                                "seven_day_opus": None}}
CODEX_LIMITS = {"primary": {"used_percent": 85.0, "window_minutes": 300, "resets_at": 1792040000},
                "secondary": {"used_percent": 44.0, "window_minutes": 10080, "resets_at": 1792400000},
                "plan_type": "plus", "credits": {"has_credits": False}}
AGY_USAGE = {"groups": [{"name": "Gemini Models", "buckets": [{"window": "weekly", "remaining_fraction": 0.75,
                                                               "reset_time": "2026-10-20T00:00:00Z"}]}]}


def test_parsers_read_only_what_providers_report():
    import datetime as dt
    c = quota.parse_claude(CLAUDE_USAGE)
    assert [(w["name"], w["used_pct"]) for w in c] == [("5 h", 4), ("week", 21)] and c[0]["resets_at"] > 0
    x = quota.parse_codex(CODEX_LIMITS)
    assert [(w["name"], w["used_pct"], w["resets_at"]) for w in x] == [("5 h", 85.0, 1792040000), ("week", 44.0, 1792400000)]
    reset = dt.datetime(2026, 10, 20, tzinfo=dt.timezone.utc).timestamp()
    assert quota.parse_agy(AGY_USAGE) == [{"name": "Gemini Models · week", "used_pct": 25.0, "resets_at": reset}]
    assert quota.parse_claude({"rate_limits_available": False}) == []


def test_agy_probe_passes_a_relative_app_data_dir(brain, monkeypatch):
    seen = []
    monkeypatch.setattr(quota, "_run", lambda args, env, stdin=None: seen.append(args) or "")
    quota._probe(brain, "agy")
    flag = next(a for a in seen[0] if a.startswith("--app_data_dir="))
    assert not flag.split("=", 1)[1].startswith("/")  # agy refuses absolute paths ("must not be absolute")


def test_agy_probe_reads_the_command_result_event(brain, monkeypatch):
    out = json.dumps({"event": "command_result", "command": {"name": "usage", "data": AGY_USAGE}}) + "\n" + \
        json.dumps({"event": "result", "result": "ok"}) + "\n"
    monkeypatch.setattr(quota, "_run", lambda args, env, stdin=None: out)
    assert [w["name"] for w in quota._probe(brain, "agy")["windows"]] == ["Gemini Models · week"]


def test_snapshot_caches_and_survives_a_hanging_probe(brain, monkeypatch):
    calls = []

    def slow(brain_, name):
        calls.append(name)
        raise TimeoutError("probe timed out after 15 s")
    monkeypatch.setattr(quota, "_probe", slow)
    monkeypatch.setattr(quota, "_cache", {})
    s = quota.snapshot(brain, force=True)
    assert s["providers"] and all(p["error"] and p["windows"] == [] for p in s["providers"])
    n = len(calls)
    quota.snapshot(brain)  # cached
    assert len(calls) == n
    with TestClient(create_app(brain, dev_no_auth=True)) as cl:
        assert "providers" in cl.get("/v1/quota").json()


def test_rules_and_quiet_hours_switch(brain):
    with TestClient(create_app(brain, dev_no_auth=True)) as c:
        r = c.get("/v1/rules").json()
        assert [g["head"] for g in r["groups"]] == ["Brain asks you first", "Never on the login node"]
        assert r["quiet_hours"]["from"] == "23:00"
        assert c.patch("/v1/settings", json={"quiet_hours_enabled": True}).json()["enabled"] is True
        assert brain.settings.raw["quiet_hours_enabled"] is True
        assert c.patch("/v1/settings", json={"default_provider": "x"}).status_code == 422


def test_self_device_and_sign_out(brain):
    from test_flows import _pair
    with TestClient(create_app(brain)) as c:
        k, dev, h, _ = _pair(c, brain)
        me = c.get("/v1/devices/self", headers=h).json()
        assert me["id"] == dev and me["web_push"] is False
        assert c.post("/v1/devices/self/revoke", headers=h).json() == {"revoked": True}
        assert c.get("/v1/devices/self", headers=h).status_code in (401, 403)
