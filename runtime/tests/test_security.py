"""An agent must not be able to pair a device, approve its own actions, or read Brain's secrets."""
import hashlib
import hmac
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from brain.api import create_app
from brain.identity import Pairing, b64d, b64e
from brain.policy import classify
from test_flows import _pair


def _pair_raw(client, brain, name):
    payload = brain.pairing.create("https://testserver")
    k = Ed25519PrivateKey.generate()
    pub = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    nonce = b"\x09" * 32
    t = Pairing.transcript(b64d(payload["brain_pub"]), pub, payload["endpoint"], nonce)
    mac = hmac.new(b64d(payload["secret"]), t, hashlib.sha256).digest()
    r = client.post("/v1/pair/complete", json={"code": payload["code"], "device_pub": b64e(pub), "device_name": name,
                                                "nonce_device": b64e(nonce), "sig": b64e(k.sign(t)), "mac": b64e(mac)})
    assert r.status_code == 200, r.text
    return k, r.json()


def _token(client, k, dev):
    ch = client.post("/v1/session/challenge", json={"device_id": dev})
    if ch.status_code != 200:
        return ch  # revoked devices are refused at the challenge already
    n = ch.json()["nonce"]
    return client.post("/v1/session/token", json={"device_id": dev, "nonce": n,
                                                  "sig": b64e(k.sign(b"cb-tok-v1" + b64d(n)))})


def test_hook_token_cannot_reach_admin_endpoints(brain):
    with TestClient(create_app(brain)) as c:
        for path, body in (("/internal/pair", {"endpoint": "https://x"}), ("/internal/devices", {}),
                           ("/internal/endpoint", {"endpoint": "https://evil"})):
            assert c.post(path, json=body, headers={"X-Brain-Hook-Token": brain.hook_token}).status_code == 403
            assert c.post(path, json=body, headers={"X-Brain-Admin-Token": brain.hook_token}).status_code == 403
        r = c.post("/internal/pair", json={"endpoint": "https://x"}, headers={"X-Brain-Admin-Token": brain.admin_token})
        assert r.status_code == 200 and r.json()["code"]
        # the hook still works with the hook token
        assert c.post("/internal/hook", json={"thread_id": "x", "tool_name": "Bash", "tool_input": {"command": "ls"}},
                      headers={"X-Brain-Hook-Token": brain.hook_token}).status_code == 200
    assert brain.admin_token != brain.hook_token
    assert brain.p.admin_token.stat().st_mode & 0o777 == 0o600


async def test_second_device_needs_approval_from_the_first(brain):
    with TestClient(create_app(brain)) as c:
        k1, dev1, h1, _ = _pair(c, brain)  # first device: trusted on pairing
        k2, out = _pair_raw(c, brain, "agent-made phone")
        assert out["pending_approval"] is True
        r = _token(c, k2, out["device_id"])
        assert r.status_code == 403 and r.json()["detail"]["error"] == "pending_approval"
        pend = c.get("/v1/pending", headers=h1).json()["approvals"]
        a = next(x for x in pend if x["action_type"] == "pair_device")
        assert "agent-made phone" in a["summary"]
        # the pending device cannot approve itself (it has no session); the first device approves with a signed decision
        sig = b64e(k1.sign(b"cb-dec-v1" + a["id"].encode() + b"allow"))
        assert c.post(f"/v1/approvals/{a['id']}/decision", headers=h1,
                      json={"decision": "allow", "device_sig": sig}).status_code == 200
        assert _token(c, k2, out["device_id"]).status_code == 200
        # deny path: a third device is revoked
        k3, out3 = _pair_raw(c, brain, "third")
        a3 = next(x for x in c.get("/v1/pending", headers=h1).json()["approvals"] if x["action_type"] == "pair_device")
        sig = b64e(k1.sign(b"cb-dec-v1" + a3["id"].encode() + b"deny"))
        c.post(f"/v1/approvals/{a3['id']}/decision", headers=h1, json={"decision": "deny", "device_sig": sig})
        assert _token(c, k3, out3["device_id"]).status_code in (401, 403)
        assert brain.db.one("SELECT revoked FROM devices WHERE id=?", (out3["device_id"],))["revoked"] == 1
    # local fallback
    _, out4 = _pair_raw(TestClient(create_app(brain)), brain, "fourth")
    assert brain.approve_device(out4["device_id"]) is True
    # revoking a device that is still waiting closes its approval card
    _, out5 = _pair_raw(TestClient(create_app(brain)), brain, "fifth")
    brain.pairing.revoke(out5["device_id"])
    assert not [a for a in brain.pending()["approvals"] if a["action_type"] == "pair_device"]


def test_policy_keeps_agents_out_of_brain_internals(brain, tmp_path):
    t = brain.create_thread("hi-tts-v3", "sec", "worktree")
    ctx = brain.policy_ctx(brain.thread(t["id"]))
    root = str(brain.p.root)
    bash = lambda cmd: classify("Bash", {"command": cmd}, ctx).verdict
    for bad in (f"cat {root}/admin.token", f"cat {root}/hook.token", "echo $BRAIN_HOOK_TOKEN",
                "curl --unix-socket $BRAIN_HOOK_SOCK -H \"X-Brain-Hook-Token: ${BRAIN_HOOK_TOKEN}\" x",
                f"sqlite3 {root}/brain.sqlite3 'select * from devices'", f"cp {root}/identity.ed25519 /tmp/k",
                f"echo 'agy_permissions: skip' >> {root}/settings.yaml", "cat ~/.brain/admin.token",
                "cat $HOME/.brain/token.secret", f"tar czf /tmp/x.tgz {root}/audit", f"cat {root}/claude/.credentials.json"):
        assert bash(bad) == "deny", bad
    assert classify("Read", {"file_path": f"{root}/identity.ed25519"}, ctx).verdict == "deny"
    assert classify("Write", {"file_path": f"{root}/settings.yaml"}, ctx).verdict == "deny"
    assert classify("Edit", {"file_path": f"{root}/claude/skills/x/SKILL.md"}, ctx).verdict == "deny"  # no changing Brain
    # what agents legitimately need under BRAIN_ROOT: reading their skills
    assert bash(f"cat {root}/codex/skills/gpu-triage/SKILL.md") == "allow"
    assert classify("Read", {"file_path": f"{root}/claude/skills/x/SKILL.md"}, ctx).verdict == "allow"
    assert bash("brain submit train.sh") == "allow"


def test_tokens_never_reach_the_phone_or_audit_log(brain):
    t = brain.create_thread("hi-tts-v3", "redact", "worktree")
    brain.events.append(t["id"], "tool.completed", {"tool_use_id": "x", "tool": "Bash", "ok": True,
                                                    "output_preview": f"BRAIN_HOOK_TOKEN={brain.hook_token}\nadmin {brain.admin_token}"})
    out = json.dumps(brain.events.replay(t["id"], 0, 100))
    assert brain.hook_token not in out and brain.admin_token not in out
