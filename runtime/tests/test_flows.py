"""End-to-end flows with the fake provider: worktree turns, deferred approval, SLURM wake, pause, API + WS."""
import asyncio
import hashlib
import hmac
import json
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from brain.api import create_app
from brain.identity import Pairing, b64d, b64e


async def _settle(brain, thread_id, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        task = brain._tasks.get(thread_id)
        if task is None or task.done():
            await asyncio.sleep(0.05)
            if brain._tasks.get(thread_id) is None:
                return
        await asyncio.sleep(0.05)
    raise TimeoutError("turn did not finish")


def types(brain, tid):
    return [e["type"] for e in brain.events.replay(tid, 0, 1000)]


async def test_worktree_turn_records_diff_and_turn(brain):
    t = brain.create_thread("hi-tts-v3", "prosody loss ablation", "worktree")
    assert t["branch"].startswith("brain/") and "prosody" in t["branch"]
    await brain.send_message(t["id"], "please edit the notes")
    await _settle(brain, t["id"])
    evs = brain.events.replay(t["id"], 0, 100)
    tys = [e["type"] for e in evs]
    assert "turn.started" in tys and "turn.ended" in tys and "file.diff" in tys
    diff = next(e for e in evs if e["type"] == "file.diff")
    assert diff["payload"]["path"] == "notes.md" and diff["payload"]["added"] >= 1
    ended = next(e for e in evs if e["type"] == "turn.ended")
    assert ended["payload"]["counts"]["edits"] == 1 and ended["payload"]["diffstat"]["files"] == 1
    assert brain.thread(t["id"])["state"] == "idle"


async def test_readonly_thread_denies_edits(brain):
    t = brain.create_thread("hi-tts-v3", "ro", "readonly")
    await brain.send_message(t["id"], "edit something")
    await _settle(brain, t["id"])
    done = [e for e in brain.events.replay(t["id"], 0, 100) if e["type"] == "tool.completed"]
    assert done and done[0]["payload"]["ok"] is False and "read-only" in done[0]["payload"]["output_preview"]


async def test_always_ask_defers_until_decided_then_resumes(brain, tmp_path):
    t = brain.create_thread("hi-tts-v3", "cleanup", "worktree")
    target = f"{tmp_path}/vaults/hi-tts-v2"
    await brain.send_message(t["id"], f"clean up with rm -rf {target}")
    await _settle(brain, t["id"])
    assert brain.thread(t["id"])["state"] == "needs_you"
    pend = brain.pending()
    assert len(pend["approvals"]) == 1
    a = pend["approvals"][0]
    assert a["action_type"] == "delete_vault_or_checkpoints" and a["state"] == "pending"
    assert "approval.requested" in types(brain, t["id"])
    # push went to the outbox (no FCM key in tests); payload carries no token
    # (no devices registered yet, so nothing sent — that is fine)
    # still pending after a long time: never auto-denied
    assert brain.approval(a["id"])["state"] == "pending"
    await brain.decide(a["id"], "allow", None, "dev_x")
    await _settle(brain, t["id"])
    tys = types(brain, t["id"])
    assert "approval.decided" in tys
    completed = [e for e in brain.events.replay(t["id"], 0, 200) if e["type"] == "tool.completed"]
    assert completed[-1]["payload"]["ok"] is True
    assert brain.thread(t["id"])["state"] == "idle"


async def test_deny_with_note_reaches_the_agent(brain, tmp_path):
    t = brain.create_thread("hi-tts-v3", "cleanup2", "worktree")
    await brain.send_message(t["id"], f"rm -rf {tmp_path}/vaults/x")
    await _settle(brain, t["id"])
    a = brain.pending()["approvals"][0]
    await brain.decide(a["id"], "deny", "keep the old runs", "dev_x")
    await _settle(brain, t["id"])
    completed = [e for e in brain.events.replay(t["id"], 0, 200) if e["type"] == "tool.completed"]
    assert completed[-1]["payload"]["ok"] is False
    assert "keep the old runs" in completed[-1]["payload"]["output_preview"]


async def test_slurm_submit_sentinel_wake(brain):
    t = brain.create_thread("hi-tts-v3", "sanity run", "worktree")
    await brain.send_message(t["id"], "submit a sanity job")
    await _settle(brain, t["id"])
    jobs = brain.jobs()
    assert len(jobs) == 1 and jobs[0]["name"].startswith("brain.")
    assert brain.thread(t["id"])["state"] == "waiting"
    turns_before = types(brain, t["id"]).count("turn.started")
    for _ in range(60):
        await brain.tick(force=True)
        if brain.jobs()[0]["state"] == "COMPLETED":
            break
        await asyncio.sleep(0.2)
    assert brain.jobs()[0]["state"] == "COMPLETED"
    await _settle(brain, t["id"])
    tys = types(brain, t["id"])
    assert "job.state" in tys and "wait.fired" in tys
    assert tys.count("turn.started") == turns_before + 1  # the agent was woken
    woke = [e for e in brain.events.replay(t["id"], 0, 500) if e["type"] == "turn.started"][-1]
    assert woke["payload"]["wake_reason"] == "wait fired"


async def test_pause_blocks_tools_and_queues_messages(brain):
    t = brain.create_thread("hi-tts-v3", "p", "worktree")
    await brain.set_paused(True)
    await brain.send_message(t["id"], "edit now")
    assert brain._tasks.get(t["id"]) is None
    d = await brain.pretool(t["id"], "Bash", {"command": "ls"}, "x")
    assert d["decision"] == "deny"
    await brain.set_paused(False)


def _pair(client, brain):
    payload = brain.pairing.create("https://testserver")
    k = Ed25519PrivateKey.generate()
    pub = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    nonce = b"\x07" * 32
    t = Pairing.transcript(b64d(payload["brain_pub"]), pub, payload["endpoint"], nonce)
    mac = hmac.new(b64d(payload["secret"]), t, hashlib.sha256).digest()
    r = client.post("/v1/pair/complete", json={"code": payload["code"], "device_pub": b64e(pub), "device_name": "test",
                                                "nonce_device": b64e(nonce), "sig": b64e(k.sign(t)), "mac": b64e(mac)})
    assert r.status_code == 200, r.text
    dev = r.json()["device_id"]
    ch = client.post("/v1/session/challenge", json={"device_id": dev}).json()
    tok = client.post("/v1/session/token", json={"device_id": dev, "nonce": ch["nonce"],
                                                 "sig": b64e(k.sign(b"cb-tok-v1" + b64d(ch["nonce"])))}).json()
    return k, dev, {"Authorization": f"Bearer {tok['access_token']}"}, tok["access_token"]


def test_api_end_to_end_with_ws(brain, tmp_path):
    app = create_app(brain)
    with TestClient(app) as client:
        assert client.get("/v1/threads").status_code == 401
        k, dev, h, token = _pair(client, brain)
        assert client.get("/v1/health").json()["brain_pub"] == brain.ident.pub_b64
        repos = client.get("/v1/repos", headers=h).json()
        assert repos[0]["id"] == "hi-tts-v3"
        th = client.post("/v1/threads", headers={**h, "Idempotency-Key": "k1"},
                         json={"repo_id": "hi-tts-v3", "title": "api", "mode": "worktree"}).json()
        again = client.post("/v1/threads", headers={**h, "Idempotency-Key": "k1"},
                            json={"repo_id": "hi-tts-v3", "title": "api", "mode": "worktree"}).json()
        assert again["id"] == th["id"]
        assert client.post("/v1/threads", headers={**h, "Idempotency-Key": "k1"},
                           json={"repo_id": "hi-tts-v3", "title": "other", "mode": "worktree"}).status_code == 422
        r = client.post(f"/v1/threads/{th['id']}/messages", headers=h,
                        json={"text": f"rm -rf {tmp_path}/vaults/old"})
        assert r.status_code == 200
        for _ in range(100):
            pend = client.get("/v1/pending", headers=h).json()
            if pend["approvals"]:
                break
            time.sleep(0.05)
        a = pend["approvals"][0]
        bad = client.post(f"/v1/approvals/{a['id']}/decision", headers=h,
                          json={"decision": "allow", "device_sig": b64e(k.sign(b"cb-dec-v1" + a["id"].encode() + b"deny"))})
        assert bad.status_code == 403
        ok = client.post(f"/v1/approvals/{a['id']}/decision", headers=h,
                         json={"decision": "allow",
                               "device_sig": b64e(k.sign(b"cb-dec-v1" + a["id"].encode() + b"allow"))})
        assert ok.status_code == 200 and ok.json()["state"] == "allowed"
        with client.websocket_connect(f"/v1/ws?token={token}") as ws:
            ws.send_json({"op": "subscribe", "thread_id": th["id"], "after_seq": 0})
            seen = []
            while True:
                m = ws.receive_json()
                if m["op"] == "event":
                    seen.append(m["event"]["seq"])
                if m["op"] == "replay_complete":
                    break
            assert seen == sorted(seen) and seen[0] == 1
        evs = client.get(f"/v1/threads/{th['id']}/events?after_seq=0", headers=h).json()
        assert evs["last_seq"] >= len(seen)
        assert client.patch(f"/v1/threads/{th['id']}", headers=h, json={"pinned": True}).json()["pinned"] is True
        assert client.post("/v1/pause", headers=h).json()["paused"] is True
        assert client.post("/v1/resume", headers=h).json()["paused"] is False


def test_internal_hook_requires_token(brain):
    app = create_app(brain)
    with TestClient(app) as client:
        r = client.post("/internal/hook", json={"thread_id": "x", "tool_name": "Bash", "tool_input": {}})
        assert r.status_code == 403
        t = brain.create_thread("hi-tts-v3", "h", "readonly")
        r = client.post("/internal/hook", headers={"X-Brain-Hook-Token": brain.hook_token},
                        json={"thread_id": t["id"], "tool_name": "Bash", "tool_input": {"command": "nohup x &"}})
        assert r.json()["decision"] == "deny"


def test_hook_fallback_when_brain_is_down(tmp_path, monkeypatch):
    import subprocess
    import sys
    monkeypatch.setenv("BRAIN_HOOK_SOCK", str(tmp_path / "nope.sock"))
    inp = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                      "tool_input": {"command": "git push --force origin main"}, "cwd": str(tmp_path)})
    out = subprocess.run([sys.executable, "-m", "brain.hook"], input=inp, capture_output=True, text=True).stdout
    d = json.loads(out)["hookSpecificOutput"]
    assert d["permissionDecision"] == "deny" and "unreachable" in d["permissionDecisionReason"]
    inp2 = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}})
    assert subprocess.run([sys.executable, "-m", "brain.hook"], input=inp2, capture_output=True, text=True).stdout == ""
