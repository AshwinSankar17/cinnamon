"""One session for the terminal and the phone, one writer at a time (session-import spec, "Shared session")."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from brain import importer, transcript
from brain.providers.fake import FakeProvider
from test_flows import _settle
from test_import import _claude_session, _grow, owner  # noqa: F401  (owner is a fixture)


@pytest.fixture
def linked(owner, brain):
    """A terminal Claude session imported to run as Claude: the thread shares the terminal's session."""
    brain.providers["claude"] = FakeProvider("claude")
    f = _claude_session(owner["profile"], owner["repo"], "L1")
    t = importer.import_session(brain, "claude", "L1")
    return {**owner, "file": f, "tid": t["id"], "sid": "L1"}


def _open_in_terminal(profile: Path, sid: str, pid: int, status: str = "idle", **extra) -> Path:
    stat = Path(f"/proc/{pid}/stat").read_text()
    (profile / "sessions").mkdir(parents=True, exist_ok=True)
    f = profile / "sessions" / f"{pid}.json"
    f.write_text(json.dumps({"pid": pid, "sessionId": sid, "procStart": stat.rsplit(")", 1)[1].split()[19],
                             "status": status, "kind": "interactive", **extra}))
    return f


RULE = "─" * 40
EMPTY = f"● done\n{RULE}\n❯\xa0\n{RULE}\n  ⏵⏵ auto mode on"


@pytest.fixture
def tmux(monkeypatch):
    """Pretend the session's pane checks out and its input box is empty; record what would be typed into it."""
    sent = []
    monkeypatch.setattr(importer, "_pane", lambda rec: ("/tmp/tmux-test/default", "%7"))
    monkeypatch.setattr(importer, "_paste", lambda sock, pane, text: sent.append((sock, pane, text)))
    monkeypatch.setattr(importer, "_screen", lambda sock, pane: EMPTY)
    return sent


def _notices(brain, tid):
    return [e["payload"]["text"] for e in brain.events.replay(tid, 0, 500) if e["type"] == "system.notice"]


# ---- reading the session file -------------------------------------------------------------------
def test_the_reader_keeps_messages_typed_mid_turn(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text(json.dumps({"type": "attachment", "attachment": {
        "type": "queued_command", "prompt": "also check the logs", "commandMode": "prompt"}}) + "\n")
    assert transcript.read_claude(f)[0] == [{"k": "user", "text": "also check the logs"}]


def test_reading_from_an_offset_takes_only_complete_new_lines(tmp_path):
    f = tmp_path / "s.jsonl"
    one = json.dumps({"type": "user", "message": {"role": "user", "content": "one"}}) + "\n"
    two = json.dumps({"type": "user", "message": {"role": "user", "content": "two"}}) + "\n"
    f.write_text(one + two[:10])
    items, end = transcript.read_claude_from(f, 0)
    assert [i["text"] for i in items] == ["one"] and end == len(one.encode())
    f.write_text(one + two)
    items, end = transcript.read_claude_from(f, end)
    assert [i["text"] for i in items] == ["two"] and end == len((one + two).encode())


# ---- linking ------------------------------------------------------------------------------------
def test_a_claude_import_shares_the_terminal_session(linked, brain):
    t = brain.thread(linked["tid"])
    assert t["import_path"] == str(linked["file"])
    row = brain.db.one("SELECT * FROM provider_sessions WHERE thread_id=? AND provider='claude'", (linked["tid"],))
    assert row["native_session_id"] == "L1" and row["canon_synced"] == len(brain.transcript(linked["tid"]))


def test_older_imports_are_linked_without_repeating_history(owner, brain):
    brain.providers["claude"] = FakeProvider("claude")
    f = _claude_session(owner["profile"], owner["repo"], "L2")
    t = importer.import_session(brain, "claude", "L2")
    brain.db.x("UPDATE threads SET import_path=NULL, import_offset=NULL, import_items=2, import_mtime=0 WHERE id=?",
               (t["id"],))
    brain.db.x("UPDATE provider_sessions SET native_session_id=NULL WHERE thread_id=?", (t["id"],))
    _grow(f, "L2", owner["repo"], "later")
    importer.follow(brain)
    assert [i["text"] for i in brain.transcript(t["id"])] == ["train the model", "Submitted job 42.", "later", "re: later"]
    row = brain.db.one("SELECT * FROM provider_sessions WHERE thread_id=? AND provider='claude'", (t["id"],))
    assert row["native_session_id"] == "L2" and row["canon_synced"] == 4
    assert brain.thread(t["id"])["import_path"] == str(f)


# ---- closed in the terminal: Brain runs it in the same session ----------------------------------
async def test_a_closed_session_runs_in_the_terminal_session_under_the_profile(linked, brain):
    await brain.send_message(linked["tid"], "status?")
    await _settle(brain, linked["tid"])
    fake = brain.providers["claude"]
    ctx = fake.ctxs[-1]
    assert ctx.resume_session == "L1" and ctx.env["CLAUDE_CONFIG_DIR"] == str(linked["profile"])
    assert fake.hydrated == []  # the session already holds everything
    n = len(brain.transcript(linked["tid"]))
    importer.watch(brain)
    assert len(brain.transcript(linked["tid"])) == n


# ---- open in a tmux pane: the terminal runs it -------------------------------------------------
async def test_open_in_tmux_the_message_is_pasted_and_brain_runs_nothing(linked, brain, tmux):
    _open_in_terminal(linked["profile"], "L1", os.getpid(), status="busy")
    await brain.send_message(linked["tid"], "and plot the loss")
    assert tmux == [("/tmp/tmux-test/default", "%7", "and plot the loss")]
    assert brain.providers["claude"].prompts == [] and not brain._tasks.get(linked["tid"])
    assert any("terminal" in n for n in _notices(brain, linked["tid"]))
    _grow(linked["file"], "L1", linked["repo"], "and plot the loss")  # the terminal runs it
    importer.watch(brain)
    assert [i["text"] for i in brain.transcript(linked["tid"])][-2:] == ["and plot the loss", "re: and plot the loss"]
    msgs = [e for e in brain.events.replay(linked["tid"], 0, 500)
            if e["type"] == "user.message" and e["payload"]["text"] == "and plot the loss"]
    assert len(msgs) == 1  # shown once, not again when it comes back from the file


async def test_a_pane_at_a_prompt_or_shell_waits_until_it_is_free(linked, brain, tmux):
    rec = _open_in_terminal(linked["profile"], "L1", os.getpid(), status="waiting")
    await brain.send_message(linked["tid"], "next step")
    assert tmux == [] and brain._queued[linked["tid"]]
    _open_in_terminal(linked["profile"], "L1", os.getpid(), status="idle")
    await brain.tick()
    assert [x[2] for x in tmux] == ["next step"] and not brain._queued.get(linked["tid"])
    assert rec.exists()


async def test_idle_with_a_background_shell_takes_the_message(linked, brain, tmux):
    _open_in_terminal(linked["profile"], "L1", os.getpid(), status="shell")
    await brain.send_message(linked["tid"], "and plot the loss")
    assert [x[2] for x in tmux] == ["and plot the loss"]


async def test_a_half_typed_draft_is_never_merged_with_the_message(linked, brain, tmux, monkeypatch):
    _open_in_terminal(linked["profile"], "L1", os.getpid(), status="idle")
    monkeypatch.setattr(importer, "_screen", lambda sock, pane: EMPTY.replace("❯\xa0", "❯\xa0half typed"))
    await brain.send_message(linked["tid"], "next step")
    assert tmux == [] and brain._queued[linked["tid"]]
    monkeypatch.setattr(importer, "_screen", lambda sock, pane: EMPTY)
    await brain.tick()
    assert [x[2] for x in tmux] == ["next step"] and not brain._queued.get(linked["tid"])


@pytest.mark.parametrize("screen,glyph,ready", [
    (EMPTY, "❯", True),
    (EMPTY.replace("❯\xa0", '❯\xa0Try "write a test for <filepath>"'), "❯", True),  # the placeholder
    (EMPTY.replace("❯\xa0", "❯\xa0ab"), "❯", False),
    (f"{'─' * 30} bg1 reply ─\n❯\xa0\n{RULE}\n  ⏵⏵ auto mode on", "❯", True),  # a named session labels the rule
    (EMPTY.replace("❯\xa0", "!\xa0"), "❯", False),  # shell mode
    (f"{RULE}\n❯\xa0line one\n  line two\n{RULE}", "❯", False),
    (" Do you want to proceed?\n ❯ 1. Yes\n   2. No\n Esc to cancel", "❯", False),  # a dialog, no input box
    (f"{RULE}\n>\n{RULE}\n? for shortcuts", ">", True),
    (f"{RULE}\n> draft\n{RULE}\n? for shortcuts", ">", False),
])
def test_the_input_box_must_be_empty(screen, glyph, ready):
    assert importer._box_empty(screen, glyph) is ready


@pytest.mark.parametrize("provider,text,typed", [
    ("claude", "!rm -rf build", "\n!rm -rf build"),
    ("claude", "/compact", "\n/compact"),
    ("claude", "plot it", "plot it"),
    ("agy", "/exit is a path", "\u200b/exit is a path"),
    ("agy", "#tag", "#tag"),
])
def test_commands_typed_from_the_phone_arrive_as_text(provider, text, typed):
    assert importer._literal(provider, text) == typed


async def test_a_message_starting_with_a_bang_is_typed_as_text_and_shown_once(linked, brain, tmux):
    _open_in_terminal(linked["profile"], "L1", os.getpid(), status="idle")
    await brain.send_message(linked["tid"], "!ls")
    assert [x[2] for x in tmux] == ["\n!ls"]
    _grow(linked["file"], "L1", linked["repo"], "\n!ls")  # Claude records the leading newline
    importer.watch(brain)
    assert len([e for e in brain.events.replay(linked["tid"], 0, 500) if e["type"] == "user.message"]) == 2


async def test_a_message_the_terminal_never_took_is_reported(linked, brain, tmux):
    _open_in_terminal(linked["profile"], "L1", os.getpid(), status="idle")
    await brain.send_message(linked["tid"], "lost one")
    brain._relayed[linked["tid"]] = [("lost one", time.time() - 120)]
    await brain.tick()
    assert any("did not take" in n for n in _notices(brain, linked["tid"]))
    assert not brain._relayed.get(linked["tid"])


async def test_a_busy_terminal_has_time_to_take_the_message(linked, brain, tmux):
    _open_in_terminal(linked["profile"], "L1", os.getpid(), status="busy")
    await brain.send_message(linked["tid"], "after this")
    brain._relayed[linked["tid"]] = [("after this", time.time() - 120)]
    await brain.tick()
    assert not any("did not take" in n for n in _notices(brain, linked["tid"]))


async def test_wakes_never_reach_an_open_terminal(linked, brain, tmux):
    _open_in_terminal(linked["profile"], "L1", os.getpid(), status="idle")
    await brain.wake(linked["tid"], "Job 42 finished")
    assert tmux == [] and brain._queued[linked["tid"]] == [("wake", "Job 42 finished")]


def test_the_pane_must_own_the_session_process():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert importer._ancestor(child.pid, os.getpid())
        assert not importer._ancestor(os.getpid(), child.pid)
    finally:
        child.kill()


# ---- open elsewhere: Brain takes over when it is idle -------------------------------------------
@pytest.fixture
def terminal_proc():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    yield p
    p.kill()
    p.wait()


async def test_an_idle_session_outside_tmux_is_taken_over(linked, brain, terminal_proc, monkeypatch):
    monkeypatch.setattr(importer, "_pane", lambda rec: None)
    _open_in_terminal(linked["profile"], "L1", terminal_proc.pid, status="idle")
    await brain.send_message(linked["tid"], "carry on")
    await _settle(brain, linked["tid"])
    assert terminal_proc.wait(timeout=5) is not None  # closed
    assert brain.providers["claude"].ctxs[-1].resume_session == "L1"
    assert any("claude --resume L1" in n for n in _notices(brain, linked["tid"]))


async def test_a_busy_session_outside_tmux_holds_until_idle(linked, brain, terminal_proc, monkeypatch):
    monkeypatch.setattr(importer, "_pane", lambda rec: None)
    _open_in_terminal(linked["profile"], "L1", terminal_proc.pid, status="busy")
    await brain.send_message(linked["tid"], "when you can")
    assert brain.providers["claude"].prompts == [] and terminal_proc.poll() is None
    _open_in_terminal(linked["profile"], "L1", terminal_proc.pid, status="idle")
    await brain.tick()
    await _settle(brain, linked["tid"])
    assert brain.providers["claude"].prompts == ["when you can"] and terminal_proc.wait(timeout=5) is not None


@pytest.mark.parametrize("status", ["idle", "shell", "busy"])
async def test_a_background_session_gets_the_message_through_attach(linked, brain, terminal_proc, monkeypatch, status):
    monkeypatch.setattr(importer, "_pane", lambda rec: None)
    sent = []
    monkeypatch.setattr(importer, "_attach_paste", lambda profile, job, text: sent.append((profile, job, text)) or True)
    _open_in_terminal(linked["profile"], "L1", terminal_proc.pid, status=status, kind="bg", jobId="ab12cd34")
    await brain.send_message(linked["tid"], "go on")
    assert sent == [(linked["profile"], "ab12cd34", "go on")]
    assert terminal_proc.poll() is None and brain.providers["claude"].prompts == []  # nothing stopped, nothing run


async def test_a_background_session_at_a_prompt_holds(linked, brain, terminal_proc, monkeypatch):
    monkeypatch.setattr(importer, "_pane", lambda rec: None)
    monkeypatch.setattr(importer, "_attach_paste", lambda *a: pytest.fail("typed into a prompt"))
    _open_in_terminal(linked["profile"], "L1", terminal_proc.pid, status="waiting", kind="bg", jobId="ab12cd34")
    await brain.send_message(linked["tid"], "go on")
    assert brain._queued[linked["tid"]] and terminal_proc.poll() is None


@pytest.fixture
def attach_sock(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="ba", dir="/tmp"))  # a Unix socket path must stay short; pytest's are long
    monkeypatch.setattr(importer, "ATTACH_SOCK", d / "s")
    yield d / "s"
    subprocess.run(["tmux", "-S", str(d / "s"), "kill-server"], capture_output=True)
    shutil.rmtree(d, ignore_errors=True)


def test_attach_types_into_a_hidden_window_and_closes_it(tmp_path, monkeypatch, attach_sock):
    """A stand-in `claude` that draws an input box and records what is typed: the real tmux does the rest."""
    got = tmp_path / "got.txt"
    fake = tmp_path / "bin" / "claude"
    fake.parent.mkdir()
    box = f"{RULE}\n❯\xa0\n{RULE}"
    fake.write_text(f"""#!{sys.executable}
import sys
print({box!r}, flush=True)
open({str(got)!r}, "w").write(" ".join(sys.argv[1:]) + "|" + sys.stdin.readline())
""")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake.parent}:{os.environ['PATH']}")
    assert importer._attach_paste(tmp_path / "profile", "ab12cd34", "go on") is True
    assert got.read_text() == "attach ab12cd34|go on\n"
    gone = subprocess.run(["tmux", "-S", str(attach_sock), "has-session", "-t", "a-ab12cd34"],
                          capture_output=True)
    assert gone.returncode != 0


def test_attach_that_never_shows_an_input_box_holds(tmp_path, monkeypatch, attach_sock):
    fake = tmp_path / "bin" / "claude"
    fake.parent.mkdir()
    fake.write_text(f"#!{sys.executable}\nimport time\nprint('Do you want to proceed?', flush=True)\ntime.sleep(30)\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake.parent}:{os.environ['PATH']}")
    monkeypatch.setattr(importer, "ATTACH_WAIT_S", 1.0)
    assert importer._attach_paste(tmp_path / "profile", "ab12cd34", "go on") is False


# ---- watching the file --------------------------------------------------------------------------
def test_watch_reads_only_the_new_bytes(linked, brain):
    f = linked["file"]
    text = f.read_text()
    f.write_text(text.replace("train the model", "XXXXXXXXXXXXXXX"))  # earlier bytes changed: never re-read
    _grow(f, "L1", linked["repo"], "fresh")
    importer.watch(brain)
    assert [i["text"] for i in brain.transcript(linked["tid"])] == ["train the model", "Submitted job 42.", "fresh",
                                                                     "re: fresh"]


def test_older_linked_threads_say_they_now_share_the_session(owner, brain):
    brain.providers["claude"] = FakeProvider("claude")
    _claude_session(owner["profile"], owner["repo"], "L3")
    t = importer.import_session(brain, "claude", "L3")
    brain.db.x("UPDATE threads SET import_path=NULL, import_offset=NULL WHERE id=?", (t["id"],))
    importer.follow(brain)
    assert any("now shares your terminal session" in n for n in _notices(brain, t["id"]))


def _line(f: Path, rec: dict) -> None:
    with f.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")
    later = time.time() + 5 + len(f.read_text())
    os.utime(f, (later, later))


def test_a_terminal_turn_written_over_several_passes_stays_one_turn(linked, brain):
    f, tid = linked["file"], linked["tid"]
    rec = _open_in_terminal(linked["profile"], "L1", os.getpid(), status="busy")
    _line(f, {"type": "user", "message": {"role": "user", "content": "long job"}})
    importer.watch(brain)
    _line(f, {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "step 1"}]}})
    importer.watch(brain)
    brain._import_turns.clear()  # a Brain restart mid-turn: the open turn is found again from the events
    _line(f, {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "step 2"}]}})
    importer.watch(brain)
    evs = [e for e in brain.events.replay(tid, 0, 500) if e["seq"] > 0]
    tail = evs[[e["type"] for e in evs].index("user.message", 3):]
    assert [e["type"] for e in tail] == ["user.message", "turn.started", "assistant.text", "assistant.text"]
    assert len({e["turn_id"] for e in tail[1:]}) == 1
    _open_in_terminal(linked["profile"], "L1", os.getpid(), status="idle")  # the terminal finished the turn
    importer.watch(brain)
    importer.watch(brain)
    ends = [e for e in brain.events.replay(tid, 0, 500) if e["type"] == "turn.ended" and e["turn_id"] == tail[1]["turn_id"]]
    assert len(ends) == 1 and ends[0]["payload"]["text"] == "step 2"
    assert rec.exists()
