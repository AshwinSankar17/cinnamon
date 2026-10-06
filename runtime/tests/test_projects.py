"""Starting work from the phone: training runs off an existing repo, new projects from scratch, failover endpoints."""
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from brain.api import create_app
from test_flows import _settle


async def test_run_thread_gets_its_own_vault_and_jobs_land_there(brain, tmp_path):
    t = brain.create_thread("hi-tts-v3", "prosody ablation lr 3e-4", run=True)
    vd = Path(t["vault_dir"])
    assert t["mode"] == "worktree" and vd.is_dir() and vd.parent == tmp_path / "vaults"
    assert vd.name.startswith("prosody-ablation") and vd.name.endswith(t["id"][-8:])
    ctx = brain.provider_ctx(brain.thread(t["id"]))
    assert ctx.env["BRAIN_VAULT_DIR"] == str(vd) and str(vd) in ctx.system_append
    await brain.send_message(t["id"], "please submit the job")
    await _settle(brain, t["id"])
    job = brain.db.one("SELECT * FROM jobs WHERE thread_id=?", (t["id"],))
    assert job and Path(job["sentinel"]).is_relative_to(vd)  # run dirs live in the run vault
    # the run vault is in scope: writing there needs no approval
    from brain.policy import classify
    assert classify("Bash", {"command": f"cp ckpt.pt {vd}/"}, brain.policy_ctx(brain.thread(t["id"]))).verdict == "allow"


def test_run_needs_a_vault_root(brain):
    brain.settings.repo("hi-tts-v3").vault_root = ""
    with pytest.raises(ValueError):
        brain.create_thread("hi-tts-v3", "x", run=True)


def test_new_project_from_scratch(brain, tmp_path):
    with pytest.raises(ValueError):
        brain.create_project("TTS distill", "idea")  # roots not configured yet
    brain.settings.raw.update(projects_root=str(tmp_path / "projects"), vault_root=str(tmp_path / "pvaults"))
    out = brain.create_project("TTS distill: tiny student", "Distill hi-tts-v3 into a 50M student.", "fake")
    repo, t = out["repo"], out["thread"]
    path = Path(repo["path"])
    assert repo["id"] == "tts-distill-tiny-student" and path == tmp_path / "projects" / repo["id"]
    assert "50M student" in (path / "README.md").read_text()
    log = subprocess.run(["git", "-C", str(path), "log", "--oneline"], capture_output=True, text=True).stdout
    assert "Start project" in log
    assert Path(repo["vault_root"]).is_dir() and t["mode"] == "worktree" and t["repo_id"] == repo["id"]
    assert brain.settings.repo(repo["id"]) and "tts-distill-tiny-student" in brain.p.settings.read_text()  # persisted
    with pytest.raises(FileExistsError):
        brain.create_project("TTS distill: tiny student", "again")


def test_api_threads_projects_endpoints(brain, tmp_path):
    brain.settings.raw.update(projects_root=str(tmp_path / "projects"), vault_root=str(tmp_path / "pvaults"))
    brain.set_endpoint("https://a.example", ["https://b.example", "https://a.example"])
    with TestClient(create_app(brain, dev_no_auth=True)) as c:
        assert c.get("/v1/endpoints").json() == {"endpoints": ["https://a.example", "https://b.example"]}
        r = c.post("/v1/threads", json={"repo_id": "hi-tts-v3", "title": "lr sweep", "run": True,
                                        "provider": "fake", "message": "sweep lr"})
        assert r.status_code == 200 and r.json()["vault_dir"]
        msgs = [e for e in brain.events.replay(r.json()["id"], 0, 100) if e["type"] == "user.message"]
        assert msgs and msgs[0]["payload"]["text"] == "sweep lr"
        p = c.post("/v1/projects", json={"name": "New idea", "idea": "try flow matching", "provider": "fake"})
        assert p.status_code == 200 and p.json()["repo"]["id"] == "new-idea"
        assert c.post("/v1/projects", json={"name": "New idea", "idea": "x"}).status_code == 409
        assert any(x["id"] == "new-idea" for x in c.get("/v1/repos").json())
    pay = brain.pairing.create("https://a.example", ["https://b.example"])
    assert pay["alt"] == ["https://b.example"]


def test_skills_listing_per_provider(brain, tmp_path, monkeypatch):
    from brain.skills import list_skills
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    wt = tmp_path / "wt"
    sk = lambda root, name, desc, extra="": (root / name).mkdir(parents=True) or (root / name / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n{extra}---\nbody\n")
    sk(wt / ".claude/skills", "shared", "repo version")
    sk(tmp_path / "ch/skills", "shared", "user version")
    sk(tmp_path / "ch/skills", "mine", "user skill")
    sk(tmp_path / "ch/skills", "hidden", "x", "user-invocable: false\n")
    sk(tmp_path / "ch/skills/synced/org1", "pdf", "synced")
    (tmp_path / "ch/commands").mkdir()
    (tmp_path / "ch/commands/deploy.md").write_text("---\ndescription: ship it\nargument-hint: <env>\n---\nDo it.\n")
    plug = tmp_path / "plug"
    sk(plug / "skills", "review", "plugin skill")
    (tmp_path / "ch/plugins").mkdir()
    (tmp_path / "ch/plugins/installed_plugins.json").write_text(
        '{"version": 2, "plugins": {"code-review@market": [{"installPath": "%s"}]}}' % plug)
    sk(home / ".agents/skills", "gpu-triage", "codex+agy user skill")
    sk(home / ".gemini/config/skills", "gpu-triage", "agy user skill")
    sk(wt / ".agents/skills", "local", "repo skill for codex+agy")

    claude = {s["name"]: s for s in list_skills("claude", wt, {"CLAUDE_CONFIG_DIR": str(tmp_path / "ch")})}
    assert claude["shared"]["scope"] == "project" and claude["shared"]["description"] == "repo version"
    assert "hidden" not in claude and claude["mine"]["scope"] == "user"
    assert claude["deploy"]["kind"] == "command" and claude["deploy"]["args"] == "<env>"
    assert "anthropic-skills:pdf" in claude and claude["code-review:review"]["scope"] == "plugin"
    codex = {s["name"] for s in list_skills("codex", wt, {"CODEX_HOME": str(tmp_path / "cx")})}
    assert codex == {"gpu-triage", "local"}
    agy = {s["name"]: s for s in list_skills("agy", wt, {})}
    assert set(agy) == {"gpu-triage", "local"} and agy["local"]["scope"] == "project"
    t = brain.create_thread("hi-tts-v3", "skills", "worktree")
    with TestClient(create_app(brain, dev_no_auth=True)) as c:
        assert c.get(f"/v1/threads/{t['id']}/skills").status_code == 200
        assert c.get("/v1/threads/nope/skills").status_code == 404


def test_attachments_land_in_the_thread_scope(brain):
    t = brain.create_thread("hi-tts-v3", "attach", "worktree")
    with TestClient(create_app(brain, dev_no_auth=True)) as c:
        r = c.post(f"/v1/threads/{t['id']}/attachments", content=b"\x89PNG fake", headers={
            "X-Filename": "../../etc/plot 1.png", "Content-Type": "image/png"})
        assert r.status_code == 200
        a = r.json()
        p = Path(a["path"])
        wt = Path(brain.thread(t["id"])["worktree"])
        assert p.parent == wt / ".brain" / "uploads" and p.read_bytes() == b"\x89PNG fake"
        assert a["name"] == "plot_1.png" and a["mime"] == "image/png" and a["size"] == 9
        assert a["rel"].startswith(".brain/uploads/") and (wt / a["rel"]) == p
        assert c.post(f"/v1/threads/{t['id']}/attachments", content=b"").status_code == 422
        assert c.post("/v1/threads/nope/attachments", content=b"x").status_code == 404
    status = subprocess.run(["git", "-C", str(wt), "status", "--porcelain"], capture_output=True, text=True).stdout
    assert ".brain" not in status  # excluded from git, so it never shows up in diffs or commits
    from brain.policy import classify
    assert classify("Read", {"file_path": str(p)}, brain.policy_ctx(brain.thread(t["id"]))).verdict == "allow"
