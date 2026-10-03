"""Event spine, redaction, policy, identity."""
import hashlib
import hmac

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from brain.identity import AuthError, Pairing, b64d, b64e
from brain.policy import PolicyContext, classify
from brain.redact import SecretRegistry


def test_events_seq_chain_replay_and_redaction(brain):
    t = brain.create_thread("hi-tts-v3", "spine", "readonly")
    for i in range(5):
        brain.events.append(t["id"], "assistant.text", {"text": f"msg {i} key=wandb-secret-0123456789"})
    evs = brain.events.replay(t["id"], after_seq=2)
    assert [e["seq"] for e in evs] == [3, 4, 5, 6]
    assert all("wandb-secret" not in e["payload"]["text"] for e in evs)
    assert all(e["payload"].get("redactions") == 1 for e in evs)
    ok, n = brain.events.verify_chain(t["id"])
    assert ok and n == 6
    # tamper → chain breaks at that seq
    brain.db.x("UPDATE events SET payload='{\"text\":\"x\"}' WHERE thread_id=? AND seq=4", (t["id"],))
    ok, at = brain.events.verify_chain(t["id"])
    assert not ok and at == 4


def test_large_payload_goes_to_blob(brain):
    t = brain.create_thread("hi-tts-v3", "big", "readonly")
    ev = brain.events.append(t["id"], "tool.completed", {"tool": "Bash", "ok": True, "output_preview": "x" * 50000})
    assert ev["payload_ref"] and ev["payload"]["truncated"]
    assert len(ev["payload"]["output_preview"]) < 3000
    assert brain.events.blob_path(ev["payload_ref"]).exists()


def test_secret_registry_longest_first():
    r = SecretRegistry(["abcdefgh", "abcdefgh-longer-secret"])
    out, n = r.redact("token abcdefgh-longer-secret and abcdefgh")
    assert "abcdefgh" not in out and n == 2


def test_policy_auto_profile():
    ctx = PolicyContext(profile="auto", worktree="/w/t1", vault_roots=("/vaults",), brain_job_ids=frozenset({"100"}))
    assert classify("Bash", {"command": "pytest -q"}, ctx).verdict == "allow"
    assert classify("Bash", {"command": "git push origin brain/x"}, ctx).verdict == "allow"
    assert classify("Bash", {"command": "git push -f origin main"}, ctx).verdict == "ask"
    assert classify("Bash", {"command": "rm -rf /vaults/hi-tts-v2/runs/old"}, ctx).verdict == "ask"
    assert classify("Bash", {"command": "rm -rf checkpoints/"}, ctx).verdict == "ask"
    assert classify("Bash", {"command": "rm -rf /home/ash/other"}, ctx).verdict == "ask"
    assert classify("Bash", {"command": "rm -rf build/"}, ctx).verdict == "allow"
    assert classify("Bash", {"command": "scancel 100"}, ctx).verdict == "allow"
    assert classify("Bash", {"command": "scancel 999"}, ctx).verdict == "ask"
    assert classify("Bash", {"command": "nohup python train.py &"}, ctx).verdict == "deny"
    assert classify("Bash", {"command": "sleep 600 && squeue -u ash"}, ctx).verdict == "deny"
    assert classify("Bash", {"command": "python synth_samples.py --ckpt 20k"}, ctx).route == "dev_alloc"
    assert classify("Write", {"file_path": "/etc/hosts"}, ctx).verdict == "ask"
    assert classify("Write", {"file_path": "/w/t1/a.py"}, ctx).verdict == "allow"


def test_policy_student_profile_asks_for_push_and_submit():
    ctx = PolicyContext(profile="student", worktree="/w")
    assert classify("Bash", {"command": "git push origin brain/x"}, ctx).verdict == "ask"
    assert classify("Bash", {"command": "sbatch job.sh"}, ctx).verdict == "ask"


def _device():
    k = Ed25519PrivateKey.generate()
    pub = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return k, pub


def test_pairing_session_and_decision_signatures(brain):
    pairing: Pairing = brain.pairing
    payload = pairing.create("https://brain-aqua.example.net")
    k, pub = _device()
    nonce = b"\x01" * 32
    t = Pairing.transcript(b64d(payload["brain_pub"]), pub, payload["endpoint"], nonce)
    mac = hmac.new(b64d(payload["secret"]), t, hashlib.sha256).digest()
    res = pairing.complete(payload["code"], b64e(pub), "Pixel 9", b64e(nonce), b64e(k.sign(t)), b64e(mac))
    # brain_sig verifies with the pinned key
    from brain.identity import verify_ed25519
    assert verify_ed25519(b64d(payload["brain_pub"]), b64d(res["brain_sig"]), t)
    # code is single-use
    try:
        pairing.complete(payload["code"], b64e(pub), "x", b64e(nonce), b64e(k.sign(t)), b64e(mac))
        raise AssertionError("reuse allowed")
    except AuthError:
        pass
    ch = pairing.challenge(res["device_id"], "brain-aqua.example.net")
    assert verify_ed25519(b64d(payload["brain_pub"]), b64d(ch["brain_sig"]),
                          b"cb-chal-v1" + b64d(ch["nonce"]) + b"brain-aqua.example.net")
    tok = pairing.token(res["device_id"], ch["nonce"], b64e(k.sign(b"cb-tok-v1" + b64d(ch["nonce"]))))
    assert pairing.authorize(tok["access_token"]) == res["device_id"]
    tok2 = pairing.refresh(res["device_id"], tok["refresh_token"])
    try:
        pairing.refresh(res["device_id"], tok["refresh_token"])  # rotated
        raise AssertionError("refresh token reused")
    except AuthError:
        pass
    assert pairing.verify_decision(res["device_id"], "apr_1", "allow", b64e(k.sign(b"cb-dec-v1apr_1allow")))
    assert not pairing.verify_decision(res["device_id"], "apr_1", "deny", b64e(k.sign(b"cb-dec-v1apr_1allow")))
    pairing.revoke(res["device_id"])
    try:
        pairing.authorize(tok2["access_token"])
        raise AssertionError("revoked device still authorized")
    except AuthError:
        pass


def test_bad_mac_rejected(brain):
    payload = brain.pairing.create("https://e")
    k, pub = _device()
    nonce = b"\x02" * 32
    t = Pairing.transcript(b64d(payload["brain_pub"]), pub, payload["endpoint"], nonce)
    try:
        brain.pairing.complete(payload["code"], b64e(pub), "x", b64e(nonce), b64e(k.sign(t)), b64e(b"\x00" * 32))
        raise AssertionError("bad mac accepted")
    except AuthError:
        pass
