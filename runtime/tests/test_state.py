"""Thread state is derived from facts, one ranked attention list for every client, read markers, skip, live changes."""
import json
import time

from fastapi.testclient import TestClient

from brain.api import create_app
from test_flows import _settle


def _approval(brain, tid, aid="apr_1", ago=60):
    brain.db.x("INSERT INTO approvals(id, thread_id, action_type, summary, decision_summary, tool_input, state, created_at)"
               " VALUES (?,?,?,?,?,?,?,?)", (aid, tid, "shell", f"do {aid}", json.dumps({"verb": f"Allow {aid}", "target": "x"}),
                                             aid, "pending", time.time() - ago))


def _listen(brain, tid, lid="lst_1", ago=60):
    brain.db.x("INSERT INTO listening_tests(id, thread_id, path, title, n_items, n_systems, state, created_at) VALUES (?,?,?,?,?,?,?,?)",
               (lid, tid, "/tmp/x", f"test {lid}", 12, 2, "pending", time.time() - ago))


def _job(brain, tid, jid, state, ago=60):
    brain.db.x("INSERT INTO jobs(job_id, thread_id, name, label, state, exit_code, submitted_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
               (jid, tid, f"brain.{jid}", f"train_{jid}.sh", state, 1, time.time() - ago - 600, time.time() - ago))


def _turn(brain, tid, stop, ago=60):
    brain.db.x("INSERT INTO turns(turn_id, thread_id, provider, started_seq, started_at, ended_at, stop_reason) VALUES (?,?,?,?,?,?,?)",
               (f"turn_{tid}_{stop}_{ago}", tid, "fake", 1, time.time() - ago - 5, time.time() - ago, stop))


def _state(brain, tid):
    return next(t for t in brain.list_threads() if t["id"] == tid)["state"]


def _kinds(brain):
    return [(i["kind"], i["thread_id"]) for i in brain.pending()["attention"]]


# ---- derived state ------------------------------------------------------------------------------

def test_state_comes_from_facts_not_the_stored_column(brain):
    a, b, c, d = (brain.create_thread("hi-tts-v3", n, "worktree")["id"] for n in "abcd")
    brain.db.x("UPDATE threads SET state='paused'")  # e.g. left over from a pause that has since been lifted
    _approval(brain, a)
    _listen(brain, b)
    brain.db.x("INSERT INTO waits(id, thread_id, kind, predicate, created_at) VALUES ('w1', ?, 'slurm_job_completed', '{}', 0)", (c,))
    assert [_state(brain, x) for x in (a, b, c, d)] == ["needs_you", "needs_you", "waiting", "idle"]
    assert {t["id"] for t in brain.list_threads(state="needs_you")} == {a, b}
    assert {t["id"] for t in brain.pending()["threads_needing_you"]} == {a, b}


async def test_paused_brain_reads_paused_and_resuming_clears_it(brain):
    t = brain.create_thread("hi-tts-v3", "p", "worktree")["id"]
    await brain.set_paused(True)
    assert _state(brain, t) == "paused"
    await brain.set_paused(False)
    assert _state(brain, t) == "idle"


def test_a_failed_turn_needs_you_until_the_next_turn(brain):
    t = brain.create_thread("hi-tts-v3", "f", "worktree")["id"]
    brain.db.x("UPDATE threads SET seen_at=?", (time.time() - 3600,))  # the failure came after the owner last looked
    _turn(brain, t, "error", ago=60)
    assert _state(brain, t) == "needs_you"
    _turn(brain, t, "end_turn", ago=10)
    assert _state(brain, t) == "idle"


def test_a_usage_limit_stop_is_waiting_not_needs_you(brain):
    t = brain.create_thread("hi-tts-v3", "q", "worktree")["id"]
    _turn(brain, t, "error")
    brain.db.x("INSERT INTO waits(id, thread_id, kind, predicate, created_at) VALUES ('wq', ?, 'provider_quota_reset', '{}', 0)", (t,))
    assert _state(brain, t) == "waiting"


async def test_a_running_turn_reads_running(brain):
    t = brain.create_thread("hi-tts-v3", "r", "worktree")["id"]
    await brain.send_message(t, "hello")
    assert _state(brain, t) == "running"
    await _settle(brain, t)
    assert _state(brain, t) == "idle"


# ---- one ranked list ----------------------------------------------------------------------------

def test_attention_ranks_what_blocks_agents_first(brain):
    a, b, c, d, e = (brain.create_thread("hi-tts-v3", n, "worktree")["id"] for n in "abcde")
    brain.db.x("UPDATE threads SET seen_at=?", (time.time() - 3600,))
    _listen(brain, d)
    _job(brain, c, "J1", "FAILED", ago=60)
    _turn(brain, b, "error")
    _approval(brain, a)
    brain.events.append(e, "turn.ended", {"text": "WER is 4.1% on the clean set.\nMore below."}, actor={"kind": "agent"})
    items = brain.pending()["attention"]
    assert [(i["tier"], i["kind"], i["thread_id"]) for i in items] == [
        (1, "approval", a), (2, "needs", b), (3, "failed", c), (4, "listen", d), (5, "news", e)]
    assert items[0]["title"] == "Allow apr_1" and items[0]["target"] == "apr_1"
    assert items[4]["detail"] == "WER is 4.1% on the clean set."


def test_failures_group_per_thread_and_name_the_reason(brain):
    t = brain.create_thread("hi-tts-v3", "sweep", "worktree")["id"]
    brain.db.x("UPDATE threads SET seen_at=?", (time.time() - 3600,))
    _job(brain, t, "J1", "FAILED", ago=120)
    _job(brain, t, "J2", "OUT_OF_MEMORY", ago=60)
    _job(brain, t, "J0", "FAILED", ago=7200)  # before you last looked
    _job(brain, t, "J3", "COMPLETED", ago=30)
    [i] = brain.pending()["attention"]
    assert (i["kind"], i["key"]) == ("failed", f"failed:{t}:J2")
    assert i["title"] == "2 jobs failed in sweep" and "out of memory" in i["detail"]


def test_every_pending_listening_test_is_listed_newest_first_and_skip_removes_one(brain):
    t = brain.create_thread("hi-tts-v3", "tts", "worktree")["id"]
    _listen(brain, t, "old", ago=9 * 86400)
    _listen(brain, t, "new", ago=60)
    assert [i["target"] for i in brain.pending()["attention"]] == ["new", "old"]
    brain.skip_listening("old")
    assert [i["target"] for i in brain.pending()["attention"]] == ["new"]
    assert [e["type"] for e in brain.events.replay(t, 0, 100)][-1] == "listen.skipped"


def test_the_owners_own_message_is_not_news(brain):
    t = brain.create_thread("hi-tts-v3", "mine", "worktree")["id"]
    brain.db.x("UPDATE threads SET seen_at=?", (time.time() - 3600,))
    brain.events.append(t, "user.message", {"text": "go"}, actor={"kind": "user"})
    assert _kinds(brain) == []


def test_muted_threads_bring_no_news_or_failures(brain):
    t = brain.create_thread("hi-tts-v3", "quiet", "worktree")["id"]
    brain.db.x("UPDATE threads SET seen_at=?, muted=1", (time.time() - 3600,))
    _job(brain, t, "J1", "FAILED")
    brain.events.append(t, "turn.ended", {"text": "done"}, actor={"kind": "agent"})
    assert _kinds(brain) == []


# ---- read markers -------------------------------------------------------------------------------

def test_news_and_failures_stay_until_the_thread_is_seen(brain):
    t = brain.create_thread("hi-tts-v3", "run", "worktree")["id"]
    assert _kinds(brain) == [], "a thread you just made is not news"
    time.sleep(0.01)
    brain.events.append(t, "turn.ended", {"text": "done"}, actor={"kind": "agent"})
    _job(brain, t, "J1", "FAILED", ago=0)
    view = next(x for x in brain.list_threads() if x["id"] == t)
    assert view["unseen"] is True
    assert _kinds(brain) == [("failed", t)], "a thread is listed once: its failure, not also as news"
    brain.mark_seen(t)
    assert _kinds(brain) == []
    assert next(x for x in brain.list_threads() if x["id"] == t)["unseen"] is False


def test_threads_from_before_read_markers_start_seen(brain):
    t = brain.create_thread("hi-tts-v3", "legacy", "worktree")["id"]
    brain.events.append(t, "turn.ended", {"text": "old news"}, actor={"kind": "agent"})
    brain.db.x("UPDATE threads SET seen_at=NULL")
    brain.backfill_seen()
    assert _kinds(brain) == []


# ---- API ----------------------------------------------------------------------------------------

def test_seen_and_skip_endpoints(brain):
    t = brain.create_thread("hi-tts-v3", "api", "worktree")["id"]
    brain.db.x("UPDATE threads SET seen_at=?", (time.time() - 3600,))
    brain.events.append(t, "turn.ended", {"text": "hi"}, actor={"kind": "agent"})
    _listen(brain, t, "lst_api")
    with TestClient(create_app(brain, dev_no_auth=True)) as c:
        assert [i["kind"] for i in c.get("/v1/pending").json()["attention"]] == ["listen"]
        assert c.post(f"/v1/listening/lst_api/skip").status_code == 200
        assert [i["kind"] for i in c.get("/v1/pending").json()["attention"]] == ["news"]
        assert c.post(f"/v1/threads/{t}/seen").status_code == 200
        assert c.get("/v1/pending").json()["attention"] == []
        assert c.post("/v1/threads/nope/seen").status_code == 404
        assert c.post("/v1/listening/nope/skip").status_code == 404


def test_subscribe_all_announces_changes_on_any_thread(brain):
    t = brain.create_thread("hi-tts-v3", "ws", "worktree")["id"]
    with TestClient(create_app(brain, dev_no_auth=True)) as c, c.websocket_connect("/v1/ws") as ws:
        ws.send_json({"op": "subscribe_all"})
        ws.send_json({"op": "ping"})
        assert ws.receive_json()["op"] == "pong"  # the subscription is registered before the ping is answered
        brain.events.append(t, "job.state", {"job_id": "1", "state": "FAILED"})
        msg = ws.receive_json()
        assert msg["op"] == "changed" and msg["thread_id"] == t and msg["type"] == "job.state" and "payload" not in msg


# ---- review fixes -------------------------------------------------------------------------------

def test_opening_a_thread_acknowledges_its_failed_turn(brain):
    t = brain.create_thread("hi-tts-v3", "old failure", "worktree")["id"]
    _turn(brain, t, "error", ago=60)
    brain.db.x("UPDATE threads SET seen_at=?", (time.time() - 3600,))
    assert _state(brain, t) == "needs_you" and _kinds(brain) == [("needs", t)]
    brain.mark_seen(t)
    assert _state(brain, t) == "idle" and _kinds(brain) == []


def test_a_dangling_turn_from_a_crash_does_not_hide_the_failed_one(brain):
    t = brain.create_thread("hi-tts-v3", "crash", "worktree")["id"]
    brain.db.x("UPDATE threads SET seen_at=?", (time.time() - 3600,))
    _turn(brain, t, "error", ago=60)
    brain.db.x("INSERT INTO turns(turn_id, thread_id, provider, started_seq, started_at) VALUES ('dangling', ?, 'fake', 9, ?)",
               (t, time.time()))
    [i] = brain.pending()["attention"]
    assert i["kind"] == "needs" and i["at"] is not None and "dangling" not in i["key"]


def test_expiry_uses_the_derived_state(brain):
    brain.settings.raw["thread_ttl_days"] = 3
    failed = brain.create_thread("hi-tts-v3", "failed long ago", "worktree")["id"]
    lifted = brain.create_thread("hi-tts-v3", "paused then resumed", "worktree")["id"]
    brain.db.x("UPDATE threads SET seen_at=0")
    _turn(brain, failed, "error", ago=5 * 86400)
    brain.db.x("UPDATE threads SET state='paused' WHERE id=?", (lifted,))
    brain.db.x("UPDATE threads SET updated_at=?", (time.time() - 5 * 86400,))
    assert brain.expire_threads() == [lifted]


def test_later_on_news_holds_until_a_new_reply(brain):
    t = brain.create_thread("hi-tts-v3", "n", "worktree")["id"]
    brain.db.x("UPDATE threads SET seen_at=?", (time.time() - 3600,))
    brain.events.append(t, "turn.ended", {"text": "one"}, actor={"kind": "agent"})
    key = brain.pending()["attention"][0]["key"]
    brain.events.append(t, "system.notice", {"text": "unrelated"})
    brain.events.append(t, "job.state", {"job_id": "1", "state": "RUNNING"})
    assert brain.pending()["attention"][0]["key"] == key
    brain.events.append(t, "turn.ended", {"text": "two"}, actor={"kind": "agent"})
    assert brain.pending()["attention"][0]["key"] != key


def test_seen_through_a_seq_leaves_later_replies_unread(brain):
    t = brain.create_thread("hi-tts-v3", "s", "worktree")["id"]
    brain.db.x("UPDATE threads SET seen_at=?", (time.time() - 3600,))
    shown = brain.events.append(t, "turn.ended", {"text": "shown"}, actor={"kind": "agent"})
    time.sleep(0.01)
    brain.events.append(t, "turn.ended", {"text": "never on screen"}, actor={"kind": "agent"})
    brain.mark_seen(t, through_seq=shown["seq"])
    [i] = brain.pending()["attention"]
    assert i["detail"] == "never on screen"
    brain.mark_seen(t, through_seq=1)  # a stale call never moves the marker back
    assert brain.pending()["attention"][0]["detail"] == "never on screen"
    brain.mark_seen(t)
    assert brain.pending()["attention"] == []


def test_imported_sessions_arrive_read(brain, tmp_path, monkeypatch):
    from brain import importer
    t = brain.create_thread("hi-tts-v3", "imported", "worktree")["id"]
    monkeypatch.setattr(brain, "create_thread", lambda *a, **k: brain.thread_view(brain.thread(t)))
    brain.events.append(t, "turn.ended", {"text": "history"}, actor={"kind": "agent"})
    importer._arrive_read(brain, t)
    assert _kinds(brain) == []


def test_subscribe_all_only_announces_what_lists_show(brain):
    t = brain.create_thread("hi-tts-v3", "ws2", "worktree")["id"]
    with TestClient(create_app(brain, dev_no_auth=True)) as c, c.websocket_connect("/v1/ws") as ws:
        ws.send_json({"op": "subscribe", "thread_id": "*"})  # a thread literally named "*" must not block subscribe_all
        ws.send_json({"op": "subscribe_all"})
        ws.send_json({"op": "unsubscribe", "thread_id": "*"})
        ws.send_json({"op": "ping"})
        msgs = []
        while (m := ws.receive_json())["op"] != "pong":
            msgs.append(m)
        brain.events.append(t, "tool.started", {"tool": "Bash"})
        brain.events.append(t, "assistant.text", {"text": "thinking"})
        brain.events.append(t, "turn.ended", {"text": "done"})
        m = ws.receive_json()
        assert (m["op"], m["type"]) == ("changed", "turn.ended")
    assert not brain.events._global_subs, "the global queue is released when the socket closes"
