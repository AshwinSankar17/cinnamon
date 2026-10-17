"""Import the owner's terminal agent sessions as threads (docs/superpowers/specs/2026-10-15-session-import-design.md)."""
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from brain import importer


def _claude_session(profile: Path, cwd: Path, sid: str, records: list[dict] | None = None, title: str | None = None,
                    continued: str | None = None) -> Path:
    d = profile / "projects" / str(cwd).replace("/", "-")
    d.mkdir(parents=True, exist_ok=True)
    recs = records or [
        {"type": "user", "cwd": str(cwd), "sessionId": sid, "message": {"role": "user", "content": "train the model"}},
        {"type": "assistant", "cwd": str(cwd), "sessionId": sid,
         "message": {"role": "assistant", "content": [{"type": "text", "text": "Submitted job 42."}]}},
    ]
    if title:
        recs.append({"type": "ai-title", "aiTitle": title, "sessionId": sid})
    if continued:
        recs.append({"type": "continued-in", "continuedInSessionId": continued, "sessionId": sid})
    f = d / f"{sid}.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    return f


@pytest.fixture
def owner(tmp_path, git_repo, brain):
    """An owner root holding the fixture repo, a Claude profile, and import settings pointing at both."""
    profile = tmp_path / "profile"
    brain.settings.raw["import"] = {"root": str(tmp_path), "claude_dirs": [str(profile)], "codex_dirs": [], "days": 30}
    return {"root": tmp_path, "profile": profile, "repo": git_repo}


def test_discover_lists_the_owners_git_sessions_newest_first(owner, brain):
    _claude_session(owner["profile"], owner["repo"], "s-old", title="Old work")
    time.sleep(0.01)
    _claude_session(owner["profile"], owner["repo"], "s-new", title="Eval the 200k checkpoint")
    found = importer.discover(brain.settings)
    assert [c["session_id"] for c in found] == ["s-new", "s-old"]
    c = found[0]
    assert c["provider"] == "claude" and c["title"] == "Eval the 200k checkpoint"
    assert c["repo_path"] == str(owner["repo"]) and c["live"] is False


def test_discover_skips_what_is_not_the_owners_or_not_importable(owner, brain, tmp_path):
    other = tmp_path.parent / (tmp_path.name + "2") / "repo"  # shares the owner's root as a string prefix
    other.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    plain = owner["root"] / "notes"  # not a git repo
    plain.mkdir()
    _claude_session(owner["profile"], other, "s-teammate")
    _claude_session(owner["profile"], plain, "s-plain")
    _claude_session(owner["profile"], owner["root"] / "deleted", "s-gone")
    _claude_session(owner["profile"], owner["repo"], "s-moved-on", continued="s-next")
    old = _claude_session(owner["profile"], owner["repo"], "s-ancient")
    os.utime(old, (time.time() - 60 * 86400,) * 2)
    assert importer.discover(brain.settings) == []


def test_discover_needs_a_root(brain):
    brain.settings.raw.pop("import", None)
    assert importer.discover(brain.settings) == []


def test_discover_does_not_parse_every_line(owner, brain):
    big = [{"type": "user", "cwd": str(owner["repo"]), "sessionId": "s-big", "message": {"role": "user", "content": "go"}}]
    f = _claude_session(owner["profile"], owner["repo"], "s-big", records=big)
    with f.open("a") as fh:
        fh.write("not json at all\n" * 1000)  # lines with no key the scan looks for are never parsed
    assert [c["session_id"] for c in importer.discover(brain.settings)] == ["s-big"]


def test_a_session_open_in_a_terminal_is_marked_live(owner, brain):
    _claude_session(owner["profile"], owner["repo"], "s-live")
    (owner["profile"] / "sessions").mkdir()
    stat = Path(f"/proc/{os.getpid()}/stat").read_text()
    start = stat.rsplit(")", 1)[1].split()[19]
    (owner["profile"] / "sessions" / f"{os.getpid()}.json").write_text(
        json.dumps({"pid": os.getpid(), "sessionId": "s-live", "procStart": start}))
    assert importer.discover(brain.settings)[0]["live"] is True


def _codex_session(home: Path, cwd: Path, sid: str, originator: str = "codex_cli_rs") -> Path:
    d = home / "sessions" / "2026" / "10" / "15"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"rollout-2026-10-15T10-00-00-{sid}.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in [
        {"type": "session_meta", "payload": {"id": sid, "cwd": str(cwd), "originator": originator}},
        {"type": "response_item", "payload": {"type": "message", "role": "user",
                                              "content": [{"type": "input_text", "text": "profile the loader"}]}},
    ]) + "\n")
    return f


def test_discover_lists_interactive_codex_sessions_only(owner, brain, tmp_path):
    home = tmp_path / "codexhome"
    brain.settings.raw["import"]["codex_dirs"] = [str(home)]
    _codex_session(home, owner["repo"], "c-1")
    _codex_session(home, owner["repo"], "c-auto", originator="codex_exec")
    found = importer.discover(brain.settings)
    assert [(c["provider"], c["session_id"]) for c in found] == [("codex", "c-1")]
    assert found[0]["title"] == "profile the loader" and found[0]["live"] is True  # written just now


SECRET = "wandb-secret-0123456789"  # the brain fixture's redactor knows this value


def _long_session(owner, sid: str, n: int) -> Path:
    recs = []
    for i in range(n):
        recs.append({"type": "user", "cwd": str(owner["repo"]), "sessionId": sid,
                     "message": {"role": "user", "content": f"step {i} " + "x" * 2000}})
        recs.append({"type": "assistant", "cwd": str(owner["repo"]), "sessionId": sid,
                     "message": {"role": "assistant", "content": [{"type": "text", "text": f"done {i}"}]}})
    return _claude_session(owner["profile"], owner["repo"], sid, records=recs, title="Long one")


def test_import_creates_a_readonly_thread_with_history_and_events(owner, brain):
    recs = [
        {"type": "user", "cwd": str(owner["repo"]), "sessionId": "s1", "message": {"role": "user", "content": "check auth"}},
        {"type": "assistant", "cwd": str(owner["repo"]), "sessionId": "s1", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "echo $WANDB_API_KEY"}}]}},
        {"type": "user", "cwd": str(owner["repo"]), "sessionId": "s1", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": SECRET}]}},
        {"type": "assistant", "cwd": str(owner["repo"]), "sessionId": "s1",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "Key is set."}]}},
    ]
    src = _claude_session(owner["profile"], owner["repo"], "s1", records=recs, title="Auth check")
    before = hashlib.sha256(src.read_bytes()).hexdigest()
    t = importer.import_session(brain, "claude", "s1", provider_override="fake")
    assert t["title"] == "Auth check" and t["mode"] == "readonly" and t["provider"] == "fake"
    assert [i["k"] for i in brain.transcript(t["id"])] == ["user", "call", "result", "assistant"]
    assert SECRET not in json.dumps(brain.transcript(t["id"]))
    types = [e["type"] for e in brain.events.replay(t["id"], 0, 100)]
    assert ["user.message", "tool.started", "tool.completed", "assistant.text"] == [x for x in types if x in
            ("user.message", "tool.started", "tool.completed", "assistant.text")]
    assert SECRET not in json.dumps(brain.events.replay(t["id"], 0, 100))
    assert hashlib.sha256(src.read_bytes()).hexdigest() == before  # the terminal's file is never touched


def test_import_is_idempotent_and_lists_the_thread(owner, brain):
    _claude_session(owner["profile"], owner["repo"], "s2")
    a = importer.import_session(brain, "claude", "s2", provider_override="fake")
    b = importer.import_session(brain, "claude", "s2", provider_override="fake")
    assert a["id"] == b["id"]
    assert [c["thread_id"] for c in importer.candidates(brain)] == [a["id"]]


def test_imported_thread_keeps_the_sessions_age_so_it_ranks_by_real_activity(owner, brain):
    import os, time
    src = _claude_session(owner["profile"], owner["repo"], "s9")
    day_ago = time.time() - 86400
    os.utime(src, (day_ago, day_ago))
    t = importer.import_session(brain, "claude", "s9", provider_override="fake")
    assert abs(brain.thread(t["id"])["updated_at"] - day_ago) < 5  # not the import time


def test_import_registers_a_new_repo_with_a_unique_id(owner, brain, tmp_path):
    other = tmp_path / "x" / "hi-tts-v3"  # same folder name as the fixture repo, different path
    other.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    _claude_session(owner["profile"], other, "s3")
    t = importer.import_session(brain, "claude", "s3", provider_override="fake")
    repo = brain.settings.repo(t["repo_id"])
    assert t["repo_id"] == "hi-tts-v3-2" and repo.path == str(other) and repo.vault_root == ""


def test_import_hands_the_model_only_the_recent_part(owner, brain):
    _long_session(owner, "s4", 400)  # ~800 KB of items
    t = importer.import_session(brain, "claude", "s4", provider_override="fake")
    row = brain.db.one("SELECT * FROM provider_sessions WHERE thread_id=?", (t["id"],))
    items = brain.transcript(t["id"])
    tail = items[row["canon_synced"]:]
    assert 0 < row["canon_synced"] < len(items) and tail[0]["k"] == "user"
    assert sum(len(json.dumps(i)) for i in tail) <= importer.BUDGET_BYTES


def test_unknown_sessions_are_not_importable(owner, brain):
    with pytest.raises(KeyError):
        importer.import_session(brain, "claude", "nope", provider_override="fake")


async def test_the_first_phone_message_hydrates_the_recent_part_then_runs(owner, brain):
    from test_flows import _settle
    src = _long_session(owner, "s5", 400)
    before = hashlib.sha256(src.read_bytes()).hexdigest()
    t = importer.import_session(brain, "claude", "s5", provider_override="fake")
    cut = brain.db.one("SELECT canon_synced FROM provider_sessions WHERE thread_id=?", (t["id"],))["canon_synced"]
    items = brain.transcript(t["id"])
    await brain.send_message(t["id"], "what was the last step?")
    await _settle(brain, t["id"])
    fake = brain.providers["fake"]
    assert fake.hydrated == [items[cut:]]
    assert fake.prompts[-1] == "what was the last step?"
    assert hashlib.sha256(src.read_bytes()).hexdigest() == before


def test_api_lists_and_imports(owner, brain):
    from fastapi.testclient import TestClient
    from brain.api import create_app
    _claude_session(owner["profile"], owner["repo"], "s6", title="From the phone")
    with TestClient(create_app(brain, dev_no_auth=True)) as c:
        lst = c.get("/v1/imports").json()
        assert [(x["session_id"], x["thread_id"]) for x in lst] == [("s6", None)]
        brain.providers["claude"] = brain.providers["fake"]  # the API imports as the session's own provider
        t = c.post("/v1/imports", json={"provider": "claude", "session_id": "s6"}).json()
        assert t["title"] == "From the phone"
        assert c.post("/v1/imports", json={"provider": "claude", "session_id": "nope"}).status_code == 404
        r = c.post("/internal/imports", json={"configure": {"days": 7}}, headers={"X-Brain-Admin-Token": brain.admin_token})
        assert r.status_code == 200 and brain.settings.raw["import"]["days"] == 7


# ---- final-review fixes ---------------------------------------------------------------------------
def test_switching_provider_after_import_keeps_the_budget(owner, brain):
    from brain.providers.fake import FakeProvider
    brain.providers["fake2"] = FakeProvider("fake2")
    _long_session(owner, "r1", 400)
    t = importer.import_session(brain, "claude", "r1", provider_override="fake")
    rows = {r["provider"]: r["canon_synced"] for r in brain.db.q("SELECT * FROM provider_sessions WHERE thread_id=?", (t["id"],))}
    assert rows["fake2"] == rows["fake"] > 0


def test_concurrent_imports_make_one_thread(owner, brain):
    from concurrent.futures import ThreadPoolExecutor
    _claude_session(owner["profile"], owner["repo"], "r2")
    with ThreadPoolExecutor(4) as ex:
        ids = {f.result()["id"] for f in [ex.submit(importer.import_session, brain, "claude", "r2", "fake") for _ in range(4)]}
    assert len(ids) == 1
    assert brain.db.one("SELECT COUNT(*) AS n FROM threads WHERE imported_from='claude:r2'")["n"] == 1


def test_settings_are_saved_atomically(brain, monkeypatch):
    import os as _os
    replaced = []
    real = _os.replace
    monkeypatch.setattr(_os, "replace", lambda a, b: (replaced.append(str(b)), real(a, b)))
    brain.settings.save(brain.p)
    assert replaced == [str(brain.p.settings)]


def test_a_half_finished_import_is_not_returned_as_done(owner, brain, monkeypatch):
    _claude_session(owner["profile"], owner["repo"], "r3")
    monkeypatch.setattr(importer, "_events", lambda *a: (_ for _ in ()).throw(RuntimeError("disk full")))
    with pytest.raises(RuntimeError):
        importer.import_session(brain, "claude", "r3", provider_override="fake")
    monkeypatch.undo()
    t = importer.import_session(brain, "claude", "r3", provider_override="fake")
    assert brain.db.one("SELECT 1 FROM provider_sessions WHERE thread_id=?", (t["id"],))


def test_automated_runs_are_never_listed(owner, brain, tmp_path):
    sdk = [{"type": "user", "cwd": str(owner["repo"]), "sessionId": "r4", "entrypoint": "sdk-cli",
            "message": {"role": "user", "content": "automated"}}]
    _claude_session(owner["profile"], owner["repo"], "r4", records=sdk)
    home = tmp_path / "codexhome"
    brain.settings.raw["import"]["codex_dirs"] = [str(home)]
    _codex_session(home, owner["repo"], "c-plugin", originator="Claude Code")
    assert importer.discover(brain.settings) == []


def test_titles_are_redacted_before_they_are_cut(owner, brain):
    msg = "x" * 50 + SECRET + " please"
    recs = [{"type": "user", "cwd": str(owner["repo"]), "sessionId": "r5", "message": {"role": "user", "content": msg}}]
    _claude_session(owner["profile"], owner["repo"], "r5", records=recs)
    (c,) = importer.candidates(brain)
    assert SECRET[:8] not in c["title"] and len(c["title"]) <= 60
    t = importer.import_session(brain, "claude", "r5", provider_override="fake")
    assert SECRET[:8] not in t["title"]


def test_the_profiles_own_credentials_are_redacted(owner, brain):
    token = "sk-ant-oat01-PROFILE-TOKEN-1234567890"
    owner["profile"].mkdir(parents=True, exist_ok=True)
    (owner["profile"] / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": token}}))
    recs = [{"type": "user", "cwd": str(owner["repo"]), "sessionId": "r6", "message": {"role": "user", "content": "hi"}},
            {"type": "assistant", "cwd": str(owner["repo"]), "sessionId": "r6",
             "message": {"role": "assistant", "content": [{"type": "text", "text": f"token is {token}"}]}}]
    _claude_session(owner["profile"], owner["repo"], "r6", records=recs)
    t = importer.import_session(brain, "claude", "r6", provider_override="fake")
    assert token not in json.dumps(brain.transcript(t["id"])) + json.dumps(brain.events.replay(t["id"], 0, 100))


def test_imported_history_is_grouped_into_turns(owner, brain):
    recs = [
        {"type": "user", "cwd": str(owner["repo"]), "sessionId": "r7", "message": {"role": "user", "content": "ls please"}},
        {"type": "assistant", "cwd": str(owner["repo"]), "sessionId": "r7", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_9", "name": "Bash", "input": {"command": "ls"}}]}},
        {"type": "user", "cwd": str(owner["repo"]), "sessionId": "r7", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_9", "content": "a b"}]}},
        {"type": "assistant", "cwd": str(owner["repo"]), "sessionId": "r7",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "Two files."}]}},
        {"type": "user", "cwd": str(owner["repo"]), "sessionId": "r7", "message": {"role": "user", "content": "thanks"}},
    ]
    _claude_session(owner["profile"], owner["repo"], "r7", records=recs)
    t = importer.import_session(brain, "claude", "r7", provider_override="fake")
    evs = [e for e in brain.events.replay(t["id"], 0, 200) if e["type"] not in ("system.notice",)]
    started = [e["payload"]["turn_id"] for e in evs if e["type"] == "turn.started"]
    assert len(started) == 2 and len([e for e in evs if e["type"] == "turn.ended"]) == 2
    work = [e for e in evs if e["type"] in ("assistant.text", "tool.started", "tool.completed")]
    assert all(e["turn_id"] == started[0] for e in work)


def test_auto_registered_repos_use_the_branch_that_exists(owner, brain, tmp_path):
    other = tmp_path / "y" / "trunkrepo"
    other.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "trunk", str(other)], check=True)
    subprocess.run(["git", "-C", str(other), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i"], check=True)
    _claude_session(owner["profile"], other, "r8")
    t = importer.import_session(brain, "claude", "r8", provider_override="fake")
    assert brain.settings.repo(t["repo_id"]).default_branch == "trunk"


def test_one_unreadable_file_does_not_break_the_listing(owner, brain):
    _claude_session(owner["profile"], owner["repo"], "r9")
    (owner["profile"] / "projects" / "x").mkdir(parents=True, exist_ok=True)
    (owner["profile"] / "projects" / "x" / "dangling.jsonl").symlink_to(owner["root"] / "nowhere.jsonl")
    assert [c["session_id"] for c in importer.discover(brain.settings)] == ["r9"]


# ---- follow: terminal sessions keep arriving -----------------------------------------------------
def _grow(f: Path, sid: str, cwd: Path, *texts: str, raw: str = "") -> None:
    """The terminal adds a turn per text (owner asks, agent answers); `raw` is appended as is (a half-written line)."""
    with f.open("a") as fh:
        for t in texts:
            fh.write(json.dumps({"type": "user", "cwd": str(cwd), "sessionId": sid,
                                 "message": {"role": "user", "content": t}}) + "\n")
            fh.write(json.dumps({"type": "assistant", "cwd": str(cwd), "sessionId": sid,
                                 "message": {"role": "assistant", "content": [{"type": "text", "text": f"re: {t}"}]}}) + "\n")
        fh.write(raw)
    later = time.time() + 5 + len(f.read_text())  # a strictly newer mtime on every growth
    os.utime(f, (later, later))


def _texts(brain, tid: str) -> list[str]:
    return [i.get("text") for i in brain.transcript(tid)]


def test_a_growing_session_adds_only_the_new_part(owner, brain):
    f = _claude_session(owner["profile"], owner["repo"], "f1")
    t = importer.import_session(brain, "claude", "f1", provider_override="fake")
    _grow(f, "f1", owner["repo"], "and now eval")
    importer.follow(brain)
    assert _texts(brain, t["id"]) == ["train the model", "Submitted job 42.", "and now eval", "re: and now eval"]
    msgs = [e["payload"]["text"] for e in brain.events.replay(t["id"], 0, 200) if e["type"] == "user.message"]
    assert msgs == ["train the model", "and now eval"]
    importer.follow(brain)  # nothing new, and no terminal holds the session: the open turn ends
    assert brain.events.replay(t["id"], 0, 200)[-1]["type"] == "turn.ended"
    seq = brain.events.last_seq(t["id"])
    importer.follow(brain)  # nothing new: nothing added
    assert brain.events.last_seq(t["id"]) == seq and len(brain.transcript(t["id"])) == 4


def test_a_half_written_line_arrives_once_complete(owner, brain):
    f = _claude_session(owner["profile"], owner["repo"], "f2")
    t = importer.import_session(brain, "claude", "f2", provider_override="fake")
    line = json.dumps({"type": "user", "cwd": str(owner["repo"]), "sessionId": "f2",
                       "message": {"role": "user", "content": "half"}})
    _grow(f, "f2", owner["repo"], raw=line[:20])
    importer.follow(brain)
    assert len(brain.transcript(t["id"])) == 2
    _grow(f, "f2", owner["repo"], raw=line[20:] + "\n")
    importer.follow(brain)
    assert _texts(brain, t["id"])[2:] == ["half"]


def test_follow_waits_while_a_brain_turn_runs(owner, brain):
    import asyncio
    f = _claude_session(owner["profile"], owner["repo"], "f3")
    t = importer.import_session(brain, "claude", "f3", provider_override="fake")
    busy = asyncio.get_event_loop_policy().new_event_loop().create_future()
    brain._tasks[t["id"]] = busy
    _grow(f, "f3", owner["repo"], "meanwhile")
    importer.follow(brain)
    assert len(brain.transcript(t["id"])) == 2
    busy.cancel()
    importer.follow(brain)
    assert _texts(brain, t["id"])[2:] == ["meanwhile", "re: meanwhile"]


def test_followed_turns_arrive_read_and_bring_the_thread_back(owner, brain):
    f = _claude_session(owner["profile"], owner["repo"], "f4")
    t = importer.import_session(brain, "claude", "f4", provider_override="fake")
    brain.db.x("UPDATE threads SET archived=1 WHERE id=?", (t["id"],))
    _grow(f, "f4", owner["repo"], "back at it")
    importer.follow(brain)
    v = brain.thread_view(brain.thread(t["id"]))
    assert v["unseen"] is False and v["archived"] is False
    assert abs(v["updated_at"] - f.stat().st_mtime) < 1  # ranks by the terminal's activity


def test_new_sessions_are_imported_automatically(owner, brain):
    brain.providers["claude"] = brain.providers["fake"]
    _claude_session(owner["profile"], owner["repo"], "f5", title="Started in the terminal")
    importer.follow(brain)
    (c,) = importer.candidates(brain)
    assert c["thread_id"] and brain.thread(c["thread_id"])["title"] == "Started in the terminal"


def test_threads_imported_before_follow_pick_up_where_they_stopped(owner, brain):
    f = _claude_session(owner["profile"], owner["repo"], "f6")
    t = importer.import_session(brain, "claude", "f6", provider_override="fake")
    brain._append_transcript(t["id"], "user", "turn_x", [{"k": "user", "text": "from the phone"}])  # Brain's own turn
    brain.db.x("UPDATE threads SET import_items=NULL, import_mtime=NULL WHERE id=?", (t["id"],))  # an old import
    _grow(f, "f6", owner["repo"], "terminal again")
    importer.follow(brain)
    assert _texts(brain, t["id"]) == ["train the model", "Submitted job 42.", "from the phone",
                                      "terminal again", "re: terminal again"]


async def test_the_next_phone_message_hands_the_model_the_terminal_work(owner, brain):
    from test_flows import _settle
    f = _claude_session(owner["profile"], owner["repo"], "f7")
    t = importer.import_session(brain, "claude", "f7", provider_override="fake")
    await brain.send_message(t["id"], "hello from the phone")
    await _settle(brain, t["id"])
    _grow(f, "f7", owner["repo"], "did this in the terminal")
    importer.follow(brain)
    await brain.send_message(t["id"], "what did I just do?")
    await _settle(brain, t["id"])
    assert [i.get("text") for i in brain.providers["fake"].hydrated[-1]] == ["did this in the terminal",
                                                                             "re: did this in the terminal"]


async def test_the_scheduler_follows_at_most_once_a_minute(brain, monkeypatch):
    calls = []
    monkeypatch.setattr(importer, "follow", lambda b: calls.append(b))
    await brain.tick()
    await brain.tick()
    assert calls == [brain]

