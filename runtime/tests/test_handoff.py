"""Provider handoff: transcript ingest, native hydration, verbatim replay, no-defer approvals, hook formats,
and the native session readers/writers (formats verified live by spikes/handoff_spikes.py)."""
import io
import json
import sys

import pytest
from fastapi.testclient import TestClient

from brain import hook, transcript
from brain.api import create_app
from brain.providers.fake import FakeProvider, FakeReplayProvider
from test_flows import _settle


@pytest.fixture
def multi(brain):
    a, b, r = FakeProvider("fake"), FakeProvider("fake2"), FakeReplayProvider("replay")
    nd = FakeProvider("nodefer", defer=False)
    brain.providers = {"fake": a, "fake2": b, "replay": r, "nodefer": nd}
    return brain, a, b, r, nd


async def _turn(brain, tid, text):
    await brain.send_message(tid, text)
    await _settle(brain, tid)


async def test_switch_hydrates_target_with_unseen_history_only(multi):
    brain, a, b, _, _ = multi
    t = brain.create_thread("hi-tts-v3", "handoff", "worktree")
    await _turn(brain, t["id"], "please edit the notes")
    canon = brain.transcript(t["id"])
    assert [i["k"] for i in canon] == ["user", "call", "result", "assistant"]
    assert canon[2]["output"] == "wrote notes.md"

    brain.switch_provider(t["id"], "fake2")
    assert brain.thread(t["id"])["provider"] == "fake2"
    await _turn(brain, t["id"], "what did you change?")
    assert b.hydrated == [canon]  # everything fake did, verbatim, before fake2's first turn
    assert b.prompts == ["what did you change?"]  # native handoff: the prompt itself is untouched
    canon2 = brain.transcript(t["id"])
    assert [i["k"] for i in canon2] == ["user", "call", "result", "assistant", "user", "assistant"]
    assert canon2[4]["text"] == "what did you change?"  # hydrated items were not re-ingested from fake2

    brain.switch_provider(t["id"], "fake")
    await _turn(brain, t["id"], "thanks")
    assert a.hydrated == [canon2[4:]]  # only what fake has not seen
    assert "provider.switched" in [e["type"] for e in brain.events.replay(t["id"], 0, 500)]


async def test_switch_to_replay_provider_puts_history_in_prompt(multi):
    brain, _, _, r, _ = multi
    t = brain.create_thread("hi-tts-v3", "to-agy", "worktree")
    await _turn(brain, t["id"], "please edit the notes")
    brain.switch_provider(t["id"], "replay")
    await _turn(brain, t["id"], "continue")
    sent = r.prompts[0]
    assert sent.startswith(transcript.REPLAY_PREAMBLE) and "wrote notes.md" in sent and sent.endswith("continue")
    assert brain.transcript(t["id"])[4] == {"k": "user", "text": "continue"}  # canonical keeps the real prompt
    await _turn(brain, t["id"], "and now?")
    assert r.prompts[1] == "and now?"  # caught up: no second replay


async def test_no_defer_provider_resumes_with_prompt_after_approval(multi, tmp_path):
    brain, _, _, _, nd = multi
    t = brain.create_thread("hi-tts-v3", "nd", "worktree", provider="nodefer")
    target = f"{tmp_path}/vaults/old"
    await _turn(brain, t["id"], f"rm -rf {target}")
    assert brain.thread(t["id"])["state"] == "needs_you"
    with pytest.raises(RuntimeError):
        brain.switch_provider(t["id"], "fake")  # the pending approval belongs to this provider
    a = brain.pending()["approvals"][0]
    await brain.decide(a["id"], "allow", None, "dev_x")
    await _settle(brain, t["id"])
    assert nd.prompts[-1].startswith("[Brain] The owner approved") and target in nd.prompts[-1]
    done = [e for e in brain.events.replay(t["id"], 0, 500) if e["type"] == "tool.completed"]
    assert done[-1]["payload"]["ok"] is True and brain.thread(t["id"])["state"] == "idle"


def test_api_switch_and_providers(multi):
    brain = multi[0]
    t = brain.create_thread("hi-tts-v3", "api", "worktree")
    with TestClient(create_app(brain, dev_no_auth=True)) as c:
        provs = {p["name"]: p for p in c.get("/v1/providers").json()}
        assert provs["fake"]["native_handoff"] and not provs["replay"]["native_handoff"] and not provs["nodefer"]["defer"]
        assert c.post(f"/v1/threads/{t['id']}/provider", json={"provider": "nope"}).status_code == 404
        r = c.post(f"/v1/threads/{t['id']}/provider", json={"provider": "fake2"})
        assert r.status_code == 200 and r.json()["provider"] == "fake2"


def _run_hook(monkeypatch, capsys, payload, decision, provider="claude"):
    monkeypatch.setenv("BRAIN_PROVIDER", provider)
    tool, inp, _, agy = hook._normalize(payload)
    hook._out(decision.get("decision", ""), decision.get("reason", ""), decision.get("updated_input"), agy)
    out = capsys.readouterr().out
    return tool, inp, (json.loads(out) if out else None)


def test_hook_formats(monkeypatch, capsys):
    agy = {"toolCall": {"name": "run_command", "args": {"CommandLine": "rm -rf x"}}, "conversationId": "c"}
    tool, inp, out = _run_hook(monkeypatch, capsys, agy, {"decision": "defer"}, "agy")
    assert (tool, inp) == ("Bash", {"command": "rm -rf x"})
    assert out["decision"] == "deny" and "approval" in out["reason"]
    _, _, out = _run_hook(monkeypatch, capsys, agy, {"decision": "allow", "updated_input": {"command": "srun x"}}, "agy")
    assert out == {"decision": "allow", "overwrite": {"CommandLine": "srun x"}}
    _, _, out = _run_hook(monkeypatch, capsys, agy, {"decision": "none"}, "agy")
    assert out is None  # agy reads a bare {} as deny, so "no opinion" prints nothing
    edit = {"toolCall": {"name": "write_to_file", "args": {"TargetFile": "/a/b.py"}}}
    assert _run_hook(monkeypatch, capsys, edit, {}, "agy")[:2] == ("Write", {"file_path": "/a/b.py"})

    codex = {"tool_name": "Bash", "tool_input": {"command": "rm -rf x"}, "tool_use_id": "t1"}
    _, _, out = _run_hook(monkeypatch, capsys, codex, {"decision": "defer"}, "codex")
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    _, _, out = _run_hook(monkeypatch, capsys, codex, {"decision": "defer"}, "claude")
    assert out["hookSpecificOutput"]["permissionDecision"] == "defer"
    assert hook._normalize({"tool_name": "apply_patch", "tool_input": {}})[0] == "Edit"


ITEMS = [{"k": "user", "text": "check the vault"},
         {"k": "call", "id": "toolu_1", "name": "Bash", "input": {"command": "cat v.cfg"}, "shell": "cat v.cfg"},
         {"k": "result", "id": "toolu_1", "output": "owner=ZEBRA-9", "error": False},
         {"k": "call", "id": "x2", "name": "web_search", "input": {"q": "a"}, "shell": None},
         {"k": "result", "id": "x2", "output": "hits", "error": False},
         {"k": "assistant", "text": "owner is ZEBRA-9"}]


def test_claude_writer_round_trip(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text(json.dumps({"type": "assistant", "uuid": "u0", "sessionId": "s", "cwd": "/w",
                             "message": {"role": "assistant", "content": [{"type": "text", "text": "ready"}]}}) + "\n")
    transcript.write_claude(f, ITEMS, "2.1.289")
    recs = [json.loads(l) for l in f.read_text().splitlines()]
    assert recs[1]["parentUuid"] == "u0" and all(r["parentUuid"] == p["uuid"] for p, r in zip(recs[1:], recs[2:]))
    items, _ = transcript.read_claude(f)
    assert items[0] == {"k": "assistant", "text": "ready"}
    got = items[1:]
    assert got[0] == ITEMS[0] and got[1]["shell"] == "cat v.cfg" and got[2]["output"] == "owner=ZEBRA-9"
    assert "web_search" in got[3]["text"] and "hits" in got[4]["text"]  # no Claude equivalent: kept as text
    assert got[-1] == ITEMS[-1]


def test_codex_writer_round_trip(tmp_path):
    f = tmp_path / "rollout.jsonl"
    f.write_text(json.dumps({"ordinal": 0, "type": "session_meta", "payload": {"id": "x"}}) + "\n")
    transcript.write_codex(f, ITEMS)
    items, _ = transcript.read_codex(f)
    assert [i["k"] for i in items] == [i["k"] for i in ITEMS]
    assert items[2]["output"] == "owner=ZEBRA-9" and items[1]["shell"] == "cat v.cfg" and items[3]["name"] == "web_search"


def test_agy_reader_maps_generic_steps_to_results(tmp_path):
    f = tmp_path / "t.jsonl"
    rows = [{"step_index": 0, "type": "USER_INPUT", "content": "<USER_REQUEST>\nrun cat\n</USER_REQUEST>\n<X/>"},
            {"step_index": 1, "type": "PLANNER_RESPONSE", "source": "MODEL", "thinking": "hm",
             "tool_calls": [{"name": "run_command", "args": {"CommandLine": "cat s"}}]},
            {"step_index": 2, "type": "GENERIC", "source": "MODEL", "status": "DONE", "content": "Output:\nNEEDLE-1\n"},
            {"step_index": 3, "type": "PLANNER_RESPONSE", "source": "MODEL", "content": "noted"}]
    f.write_text("\n".join(json.dumps(r) for r in rows))
    items, dropped = transcript.read_agy(f)
    assert [i["k"] for i in items] == ["user", "call", "result", "assistant"] and dropped == 1
    assert items[0]["text"] == "run cat" and items[1]["shell"] == "cat s" and "NEEDLE-1" in items[2]["output"]


FAKE_AGY = r'''#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
app = os.path.join(os.path.expanduser("~/.gemini"), a[0].split("=", 1)[1])  # like agy: relative to ~/.gemini
prompt = a[a.index("-p") + 1]
settings = json.load(open(os.path.join(app, "settings.json")))
cmd = os.environ["FAKE_AGY_CMD"]
ok = f"command({cmd})" in settings.get("permissions", {}).get("allow", [])
def out(o): print(json.dumps(o), flush=True)
out({"event": "init", "conversation_id": "conv1"})
p = {"CommandLine": cmd}
out({"event": "step_update", "step_update": {"step_index": 2, "state": "ACTIVE", "step_type": "tool", "tool_name": "run_command", "tool_info": {"parameters": p}}})
silent = os.environ.get("FAKE_AGY_SILENT")  # real agy sometimes reports the denied step as DONE with no error
st = {"step_index": 2, "state": "DONE" if ok or silent else "ERROR", "step_type": "tool", "tool_name": "run_command", "tool_info": {"parameters": p}}
if not ok and not silent:
    st["tool_info"]["error"] = {"message": f'permission check failed for unsandboxed "{cmd}": user denied permission'}
out({"event": "step_update", "step_update": st})
open(os.path.join(app, "prompts.log"), "a").write(prompt + "\n---\n")
out({"event": "result", "result": {"conversation_id": "conv1", "status": "SUCCESS", "response": "ran" if ok else "",
     "denied_actions": [] if ok else [{"action": "command"}]}})
'''


@pytest.mark.parametrize("silent", [False, True])
@pytest.mark.parametrize("command,verdict", [("ls -la /tmp", "none"), ("rm -r /x/vault", "defer"), ("tmux", "deny")])
async def test_agy_rule_denials_go_through_brain_policy(tmp_path, monkeypatch, command, verdict, silent):
    from brain.providers.agy import AgyProvider
    from brain.providers.base import TurnContext
    binp = tmp_path / "agy"
    binp.write_text(FAKE_AGY)
    binp.chmod(0o755)
    monkeypatch.setenv("FAKE_AGY_CMD", command)
    monkeypatch.setenv("FAKE_AGY_SILENT", "1" if silent else "")
    ws = tmp_path / "ws"
    ws.mkdir()
    asked = []

    async def pretool(tool, inp, tuid):
        asked.append(inp["command"])
        return {"decision": verdict}

    ctx = TurnContext(thread_id="t", cwd=ws, prompt="go", resume_session=None, env={}, emit=lambda *_: None,
                      pretool=pretool, extra={"agy_app_data_dir": str(tmp_path / "app")})
    res = await AgyProvider(str(binp)).run_turn(ctx)
    allow = json.loads((tmp_path / "app" / "settings.json").read_text())["permissions"]["allow"]
    prompts = (tmp_path / "app" / "prompts.log").read_text().split("\n---\n")[:-1]
    assert asked == [command] and res.session_id == "conv1"
    if verdict == "none":  # Brain's policy allows it: rule added, retried in the same conversation, ran
        assert f"command({command})" in allow and len(prompts) == 2 and res.text == "ran"
    elif verdict == "defer":  # needs approval: no rule yet, turn parks as deferred
        assert f"command({command})" not in allow and res.stop_reason == "tool_deferred" and len(prompts) == 1
    else:
        assert f"command({command})" not in allow and len(prompts) == 1


@pytest.mark.parametrize("prov", ["fake", "nodefer"])
async def test_every_approval_is_pushed_to_paired_devices(multi, tmp_path, prov):
    brain = multi[0]
    brain.db.x("INSERT INTO devices(id, name, public_key, fcm_token, created_at) VALUES ('dev1','phone','k','fcm-tok',0)")
    brain.db.x("INSERT INTO devices(id, name, public_key, fcm_token, created_at, revoked) VALUES ('dev2','old','k','x',0,1)")
    t = brain.create_thread("hi-tts-v3", "push", "worktree", provider=prov)
    brain.patch_thread(t["id"], muted=True)  # approvals are pushed even for muted threads
    await _turn(brain, t["id"], f"rm -rf {tmp_path}/vaults/old")
    a = brain.pending()["approvals"][0]
    sent = [json.loads(l)["message"] for l in brain.p.push_outbox.read_text().splitlines()]
    pushes = [m for m in sent if m["data"].get("kind") == "approval"]
    assert len(pushes) == 1  # one live device; the revoked one gets nothing
    m = pushes[0]
    assert m["token"] == "fcm-tok" and m["data"]["approval_id"] == a["id"] and m["data"]["thread_id"] == t["id"]
    assert m["android"]["notification"]["channel_id"] == "needs_you" and m["android"]["priority"] == "HIGH"


async def test_approval_decided_while_turn_is_still_ending_resumes_after(brain, tmp_path):
    """Seen live: the phone approved within seconds, before the CLI had exited after deferring. The decision
    must not be dropped: Brain resumes as soon as that turn ends."""
    import asyncio as aio

    class SlowExit(FakeProvider):
        def __init__(self):
            super().__init__("slow")
            self.gate = aio.Event()

        async def run_turn(self, ctx):
            res = await super().run_turn(ctx)
            if res.stop_reason == "tool_deferred":
                await self.gate.wait()  # the CLI is still winding down
            return res

    p = SlowExit()
    brain.providers = {"slow": p}
    t = brain.create_thread("hi-tts-v3", "slow", "worktree", provider="slow")
    await brain.send_message(t["id"], f"rm -rf {tmp_path}/vaults/old")
    for _ in range(100):
        if brain.pending()["approvals"]:
            break
        await aio.sleep(0.02)
    a = brain.pending()["approvals"][0]
    await brain.decide(a["id"], "allow", None, "dev_x")  # turn still running
    assert brain._resume_after == {t["id"]: None}
    p.gate.set()
    await _settle(brain, t["id"])
    await _settle(brain, t["id"])
    done = [e for e in brain.events.replay(t["id"], 0, 500) if e["type"] == "tool.completed"]
    assert done and done[-1]["payload"]["ok"] is True and p.prompts[-1] is None  # resumed the deferred call
    assert brain.thread(t["id"])["state"] == "idle" and not brain._resume_after
