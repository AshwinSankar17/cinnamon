"""Listening tests: listen.json validation, `brain listen` registration, audio serving, blind ratings → wake-up."""
import json
import struct
import wave
from pathlib import Path

from fastapi.testclient import TestClient

from brain.api import create_app
from brain.listening import ListenError, load, summarize
from test_flows import _settle


def _wav(p: Path, n=800):
    p.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack(f"<{n}h", *([1000, -1000] * (n // 2))))


def _test_dir(root: Path) -> Path:
    d = root / "evals" / "step10k"
    for s in ("base", "kappa"):
        for u in ("u1", "u2"):
            _wav(d / s / f"{u}.wav")
    _wav(d / "refs" / "u1.wav")
    (d / "listen.json").write_text(json.dumps({
        "version": 1, "title": "Sampler sweep", "systems": [{"id": "base", "label": "current grid"}, {"id": "kappa", "label": "kappa grid"}],
        "items": [{"id": "u1", "text": "Hello there.", "samples": {"base": "base/u1.wav", "kappa": "kappa/u1.wav"},
                   "reference": "refs/u1.wav", "metrics": {"base": {"wer": 0.1}, "kappa": {"wer": 0.0}}},
                  {"id": "u2", "text": "Second line.", "samples": {"base": "base/u2.wav", "kappa": "kappa/u2.wav"}}],
        "metrics": {"base": {"wer": 0.05}, "kappa": {"wer": 0.02}}}))
    return d


def test_validation_names_every_problem(tmp_path):
    d = _test_dir(tmp_path)
    assert load(d / "listen.json", [tmp_path])["items"][0]["reference"] == "refs/u1.wav"
    bad = json.loads((d / "listen.json").read_text())
    bad["items"][1]["samples"] = {"base": "base/missing.wav", "nope": "kappa/u2.wav"}
    bad["items"][0]["samples"]["kappa"] = "../../../../etc/passwd"
    (d / "bad.json").write_text(json.dumps(bad))
    try:
        load(d / "bad.json", [tmp_path])
        raise AssertionError("should have failed")
    except ListenError as e:
        msg = str(e)
        assert "does not exist" in msg and "unknown systems" in msg and "outside" in msg


def test_summary_mos_ci_and_preferences():
    test = {"systems": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
            "items": [{"id": "1"}, {"id": "2"}, {"id": "3"}]}
    s = summarize(test, {"1": {"scores": {"a": 4, "b": 2}, "best": "a"}, "2": {"scores": {"a": 5, "b": 3}, "best": "a", "note": "b clips"},
                         "3": {"scores": {"a": 3, "b": 3}, "best": "tie"}})
    assert s["systems"]["a"]["mos"] == 4.0 and s["systems"]["b"]["mos"] == 2.667 and s["systems"]["a"]["best"] == 2
    assert s["ties"] == 1 and s["ranking"] == ["a", "b"] and s["rated_items"] == 3 and s["notes"] == ["2: b clips"]
    assert s["systems"]["a"]["mos_ci95"] > 0


async def test_listen_end_to_end(brain, tmp_path):
    t = brain.create_thread("hi-tts-v3", "tts eval", run=True)
    wt = Path(brain.thread(t["id"])["worktree"])
    d = _test_dir(Path(t["vault_dir"]))  # samples in the run vault, as a real eval would write them
    brain.db.x("INSERT INTO devices(id, name, public_key, fcm_token, created_at) VALUES ('dev1','phone','k','tok',0)")
    with TestClient(create_app(brain)) as c:
        bad = c.post("/internal/listen", json={"thread_id": t["id"], "path": str(tmp_path / "nowhere.json")},
                     headers={"X-Brain-Hook-Token": brain.hook_token})
        assert bad.status_code == 400 and "outside" in bad.json()["error"]
        r = c.post("/internal/listen", json={"thread_id": t["id"], "path": str(d / "listen.json")},
                   headers={"X-Brain-Hook-Token": brain.hook_token})
        assert r.status_code == 200 and r.json()["items"] == 2
    lid = r.json()["listen_id"]
    pushes = [json.loads(l)["message"]["data"] for l in brain.p.push_outbox.read_text().splitlines()]
    assert any(p.get("listen_id") == lid for p in pushes)  # the owner is told
    assert brain.pending()["listening"][0]["id"] == lid
    with TestClient(create_app(brain, dev_no_auth=True)) as c:
        test = c.get(f"/v1/listening/{lid}").json()
        assert test["title"] == "Sampler sweep" and test["state"] == "pending" and test["items"][0]["reference"]
        a = c.get(f"/v1/listening/{lid}/audio", params={"path": "kappa/u1.wav"})
        assert a.status_code == 200 and a.content[:4] == b"RIFF" and a.headers["content-type"] == "audio/wav"
        assert c.get(f"/v1/listening/{lid}/audio", params={"path": "../../../../etc/passwd"}).status_code == 404
        assert c.get(f"/v1/listening/{lid}/audio", params={"path": "../listen.json"}).status_code == 404
        assert c.post("/v1/listening/nope/ratings", json={"ratings": {}}).status_code == 404
    # rate on the main loop (as the server does), so the wake-up turn runs to completion here
    summary = await brain.rate_listening(lid, {
        "u1": {"scores": {"base": 3, "kappa": 5}, "best": "kappa", "note": "kappa is crisper"},
        "u2": {"scores": {"base": 4, "kappa": 4}, "best": "tie"}}, "dev1")
    assert summary["ranking"][0] == "kappa"
    with TestClient(create_app(brain, dev_no_auth=True)) as c:
        # file browser over the same scope
        ls = c.get(f"/v1/threads/{t['id']}/files", params={"root": "run", "path": "evals/step10k"}).json()
        assert {"base", "kappa", "listen.json", "listen_results.json"} <= {e["name"] for e in ls["entries"]}
        assert c.get(f"/v1/threads/{t['id']}/file", params={"root": "run", "path": "evals/step10k/base/u1.wav"}).status_code == 200
        assert c.get(f"/v1/threads/{t['id']}/file", params={"root": "run", "path": "../../../etc/passwd"}).status_code == 404
    res = json.loads((d / "listen_results.json").read_text())
    assert res["blind"] and res["ratings"]["u1"]["best"] == "kappa"
    await _settle(brain, t["id"])
    prompt = brain.providers["fake"].prompts[-1]
    assert "blind listening test" in prompt and "kappa is crisper" in prompt and "kappa grid: MOS 4.5" in prompt
    assert "listen.rated" in [e["type"] for e in brain.events.replay(t["id"], 0, 500)]
    assert brain.listening_row(lid)["state"] == "rated" and not brain.pending()["listening"]
