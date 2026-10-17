"""Codex and agy terminal sessions shared with the phone, one writer at a time (session-import spec, "Native
delivery"): an open session gets the message the way its CLI queues input; a closed one runs here in it."""
import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path

import pytest

from brain import importer
from brain.providers.base import TurnContext
from brain.providers.codex import CodexProvider
from brain.providers.fake import FakeProvider
from test_flows import _settle
from test_import import _codex_session, owner  # noqa: F401  (owner is a fixture)
from test_shared_session import _notices


@contextmanager
def held(lock: Path):
    """What a Codex or agy terminal does while it has the session open: hold an exclusive flock on its lock file."""
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield


def _add(f: Path, *recs: dict) -> None:
    with f.open("a") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")
    later = os.stat(f).st_mtime + 5
    os.utime(f, (later, later))


def _user(text):
    return {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                 "content": [{"type": "input_text", "text": text}]}}


def _said(text):
    return {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                                                 "content": [{"type": "output_text", "text": text}]}}


def _task(kind):
    return {"type": "event_msg", "payload": {"type": kind}}


def test_the_lock_holder_comes_from_the_kernel_lock_table(tmp_path):
    lock = tmp_path / "s.lock"
    assert importer._holder(lock) is None  # no file
    with held(lock):
        assert importer._holder(lock) == os.getpid()
    assert importer._holder(lock) is None  # the file stays behind when the terminal closes


# ---- Codex --------------------------------------------------------------------------------------
@pytest.fixture
def cx(owner, brain, tmp_path):
    home = tmp_path / "codexhome"
    brain.settings.raw["import"]["codex_dirs"] = [str(home)]
    brain.providers["codex"] = FakeProvider("codex")
    f = _codex_session(home, owner["repo"], "C1")
    t = importer.import_session(brain, "codex", "C1")
    return {**owner, "home": home, "file": f, "tid": t["id"], "lock": home / "thread-writer-locks" / "C1.lock"}


def test_a_codex_import_shares_the_terminal_session(cx, brain):
    t = brain.thread(cx["tid"])
    assert t["import_path"] == str(cx["file"]) and importer.linked(t)
    row = brain.db.one("SELECT * FROM provider_sessions WHERE thread_id=? AND provider='codex'", (cx["tid"],))
    assert row["native_session_id"] == "C1" and row["canon_synced"] == len(brain.transcript(cx["tid"]))


def test_a_codex_session_is_live_while_its_terminal_holds_the_lock(cx, brain):
    assert importer.discover(brain.settings)[0]["live"] is False
    with held(cx["lock"]):
        assert importer.discover(brain.settings)[0]["live"] is True


async def test_open_in_codex_the_message_goes_through_codex_queue(cx, brain, monkeypatch):
    queued = []
    monkeypatch.setattr(importer, "_codex_queue", lambda home, sid, text: queued.append((home, sid, text)))
    with held(cx["lock"]):
        await brain.send_message(cx["tid"], "/plot the loss")
        assert queued == [(cx["home"], "C1", "/plot the loss")]  # not typed: nothing to escape
        assert brain.providers["codex"].prompts == [] and not brain._tasks.get(cx["tid"])
        _add(cx["file"], _task("task_started"), _user("/plot the loss"), _said("plotted"), _task("task_complete"))
        importer.watch(brain)
    assert [i["text"] for i in brain.transcript(cx["tid"])][-2:] == ["/plot the loss", "plotted"]
    msgs = [e for e in brain.events.replay(cx["tid"], 0, 500)
            if e["type"] == "user.message" and e["payload"]["text"] == "/plot the loss"]
    assert len(msgs) == 1


async def test_closed_in_codex_it_runs_here_in_the_owners_home(cx, brain):
    await brain.send_message(cx["tid"], "status?")
    await _settle(brain, cx["tid"])
    ctx = brain.providers["codex"].ctxs[-1]
    assert ctx.resume_session == "C1" and ctx.env["CODEX_HOME"] == str(cx["home"])
    assert brain.providers["codex"].hydrated == []


def test_a_codex_turn_written_over_several_passes_stays_one_turn(cx, brain):
    f, tid = cx["file"], cx["tid"]
    with held(cx["lock"]):
        _add(f, _task("task_started"), _user("long job"), _said("step 1"))
        importer.watch(brain)
        _add(f, _said("step 2"))
        importer.watch(brain)
        evs = [e for e in brain.events.replay(tid, 0, 500) if e["type"] in ("turn.started", "turn.ended")]
        assert evs[-1]["type"] == "turn.started"  # still open: Codex is mid-turn
        _add(f, _task("task_complete"))
        importer.watch(brain)
        importer.watch(brain)
    ends = [e for e in brain.events.replay(tid, 0, 500) if e["type"] == "turn.ended" and e["turn_id"] == evs[-1]["turn_id"]]
    assert len(ends) == 1 and ends[0]["payload"]["text"] == "step 2"


async def test_codex_runs_pass_the_hook_as_a_flag_and_write_no_file(tmp_path):
    home = tmp_path / "owner-codex"
    home.mkdir()
    fake = tmp_path / "codex"
    fake.write_text("#!/bin/bash\nprintf '%s\\n' \"$@\" > \"$(dirname \"$0\")/args\"\n"
                    "echo '{\"type\":\"turn.completed\",\"usage\":{}}'\n")
    fake.chmod(0o755)
    ctx = TurnContext(thread_id="t", cwd=tmp_path, prompt="hi", resume_session="C1", env={"CODEX_HOME": str(home)},
                      emit=lambda *a: None, pretool=None)
    await CodexProvider(binary=str(fake)).run_turn(ctx)
    args = (tmp_path / "args").read_text().splitlines()
    assert any(a.startswith("hooks.PreToolUse=") and "brain.hook" in a for a in args)
    assert list(home.iterdir()) == []  # the owner's Codex home gets no hooks.json
