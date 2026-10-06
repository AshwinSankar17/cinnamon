"""PWA: Web Push (RFC 8291 encryption + VAPID), subscription registration, notification fan-out, static serving."""
import json
import struct
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient

from brain import webpush
from brain.api import create_app
from brain.identity import b64d, b64e
from brain.webpush import WebPush, _hkdf, _pub_bytes


def _browser():
    """A fake browser subscription: its ECDH key pair and auth secret."""
    k = ec.generate_private_key(ec.SECP256R1())
    auth = b"\x01" * 16
    sub = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": b64e(_pub_bytes(k.public_key())),
                                                                          "auth": b64e(auth)}}
    return k, auth, sub


def _decrypt(body: bytes, ua_key, auth: bytes) -> bytes:
    """What the browser does (RFC 8291 §3.4 / RFC 8188), written independently of encrypt()."""
    salt, rs, idlen = body[:16], *struct.unpack(">IB", body[16:21])
    as_pub, record = body[21:21 + idlen], body[21 + idlen:]
    assert rs == 4096
    shared = ua_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
    ua_pub = _pub_bytes(ua_key.public_key())
    ikm = _hkdf(auth, shared, b"WebPush: info\x00" + ua_pub + as_pub, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    plain = AESGCM(cek).decrypt(nonce, record, None)
    assert plain.endswith(b"\x02")
    return plain[:-1]


def test_encrypt_round_trips_like_a_browser():
    k, auth, sub = _browser()
    msg = json.dumps({"title": "Approve?", "data": {"approval_id": "apr_1"}}).encode()
    body = webpush.encrypt(msg, sub["keys"]["p256dh"], sub["keys"]["auth"])
    assert _decrypt(body, k, auth) == msg


def test_vapid_header_is_a_valid_es256_jwt(tmp_path):
    wp = WebPush(tmp_path / "vapid.pem")
    assert (tmp_path / "vapid.pem").stat().st_mode & 0o777 == 0o600
    assert WebPush(tmp_path / "vapid.pem").public_key == wp.public_key  # persisted, not regenerated
    hdr = wp._vapid("https://web.push.apple.com/QGuQyavXut")
    t, k = hdr.removeprefix("vapid t=").split(", k=")
    h, c, sig = t.split(".")
    claims = json.loads(b64d(c))
    assert claims["aud"] == "https://web.push.apple.com" and claims["exp"] > time.time() and claims["sub"].startswith("mailto:")
    raw = b64d(sig)
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b64d(k))
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    pub.verify(der, f"{h}.{c}".encode(), ec.ECDSA(hashes.SHA256()))  # raises if invalid


def test_web_push_subscription_and_fanout(brain, monkeypatch):
    k, auth, sub = _browser()
    sent = []

    def fake_post(url, content, headers, timeout):
        sent.append((url, content, headers))
        return type("R", (), {"status_code": 201, "text": ""})()

    monkeypatch.setattr(webpush.httpx, "post", fake_post)
    brain.db.x("INSERT INTO devices(id, name, public_key, created_at) VALUES ('dev', 'pwa', 'k', 0)")
    with TestClient(create_app(brain, dev_no_auth=True)) as c:
        assert c.get("/v1/push/vapid").json()["public_key"] == brain.webpush.public_key
        bad = c.post("/v1/devices/web-push", json={"subscription": {"endpoint": "http://x", "keys": {}}})
        assert bad.status_code == 422
    brain.db.x("UPDATE devices SET web_push=? WHERE id='dev'", (json.dumps(sub),))
    t = brain.create_thread("hi-tts-v3", "pwa", "worktree")
    brain._notify(t["id"], "approval", "Delete old vault?", "hi-tts-v3 · pending", {"approval_id": "apr_9", "thread_id": t["id"]})
    assert len(sent) == 1
    url, body, headers = sent[0]
    assert url == sub["endpoint"] and headers["Content-Encoding"] == "aes128gcm" and headers["Urgency"] == "high"
    msg = json.loads(_decrypt(body, k, auth))
    assert msg["title"] == "Delete old vault?" and msg["data"] == {"v": "1", "kind": "approval", "approval_id": "apr_9",
                                                                     "thread_id": t["id"]}
    # a gone subscription (410) is dropped so Brain stops sending to it
    monkeypatch.setattr(webpush.httpx, "post", lambda *a, **kw: type("R", (), {"status_code": 410, "text": ""})())
    brain._notify(t["id"], "approval", "x", "y", {})
    assert brain.db.one("SELECT web_push FROM devices WHERE id='dev'")["web_push"] is None


def test_pwa_static_serving(brain, tmp_path):
    app_dir = tmp_path / "dist"
    (app_dir / "_expo/static/js").mkdir(parents=True)
    (app_dir / "index.html").write_text("<html>shell</html>")
    (app_dir / "sw.js").write_text("self.x=1")
    (app_dir / "manifest.webmanifest").write_text("{}")
    (app_dir / "_expo/static/js/entry-abc.js").write_text("app")
    (tmp_path / "secret.txt").write_text("no")
    brain.settings.raw["web_app_dir"] = str(app_dir)
    with TestClient(create_app(brain)) as c:
        assert c.get("/").text == "<html>shell</html>"
        assert c.get("/thread/thr_123").text == "<html>shell</html>"  # client-side routes get the shell
        sw = c.get("/sw.js")
        assert sw.text == "self.x=1" and sw.headers["cache-control"] == "no-cache" and sw.headers["service-worker-allowed"] == "/"
        assert c.get("/manifest.webmanifest").headers["content-type"].startswith("application/manifest+json")
        assert "immutable" in c.get("/_expo/static/js/entry-abc.js").headers["cache-control"]
        assert "no" not in c.get("/../secret.txt").text  # no escaping the app dir
        assert c.get("/v1/threads").status_code == 401  # API routes still win and still need auth
        assert c.get("/v1/nonexistent").status_code == 404
