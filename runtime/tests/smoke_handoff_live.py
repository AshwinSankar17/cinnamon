#!/usr/bin/env python3
"""Live handoff smoke test: a real `brain serve` drives the REAL claude, codex and agy CLIs through one thread,
switching provider between turns, and checks each new provider recalls facts that existed only in earlier
providers' tool outputs. Also checks a no-defer approval (Codex, then agy with a permission rule).

    python tests/smoke_handoff_live.py [--profile ash]     (from runtime/, inside the venv)

Credentials come from your logged-in profiles by symlink (~/.claude-profiles/P, ~/.codex-profiles/P,
~/.gemini/antigravity-cli-profiles/P); everything else lives in a throwaway BRAIN_ROOT. Costs ~12 small turns.
"""
import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

PORT = 18766
HOME = Path.home()
RECALL = "Do not run any tools or read any files. Earlier in this thread, {what}. Reply with {fmt} and nothing else."


def req(method, path, body=None):
    r = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method,
                               data=json.dumps(body).encode() if body is not None else None,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.loads(resp.read())


def events(tid):
    return req("GET", f"/v1/threads/{tid}/events?after_seq=0&limit=1000")["events"]


def turn(tid, text, timeout=600):
    """Send a message, wait for the turn to end, return (final text, tool commands run in that turn)."""
    before = sum(e["type"] == "turn.ended" for e in events(tid))
    req("POST", f"/v1/threads/{tid}/messages", {"text": text})
    return wait_turn(tid, before, timeout)


def wait_turn(tid, before, timeout=600):
    end = time.time() + timeout
    while time.time() < end:
        evs = events(tid)
        ended = [e for e in evs if e["type"] == "turn.ended"]
        if len(ended) > before and req("GET", f"/v1/threads/{tid}")["state"] != "running":
            start = max(e["seq"] for e in evs if e["type"] == "turn.started")
            tools = [e["payload"].get("summary", "") for e in evs if e["type"] == "tool.started" and e["seq"] > start]
            notes = [e["payload"].get("text") or e["payload"].get("message") for e in evs
                     if e["seq"] > start and e["type"] in ("system.notice", "provider.status")]
            return ended[-1]["payload"]["text"], tools, notes
        time.sleep(1)
    raise SystemExit(f"FAIL: turn did not finish in {timeout}s")


def link(src: Path, dst: Path):
    if not src.exists():
        raise SystemExit(f"missing {src}: log that profile in first")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_symlink() or dst.exists():
        dst.unlink()
    dst.symlink_to(src)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="ash")
    a = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="brain-handoff-live-"))
    root = tmp / ".brain"
    env = dict(os.environ, BRAIN_ROOT=str(root), BRAIN_DEV_NO_AUTH="1",
               PATH=f"{Path(sys.executable).parent}:{os.environ['PATH']}",
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    repo, vault = tmp / "repo", tmp / "vaults"
    repo.mkdir()
    (vault / "old-run").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, env=env)
    (repo / "README.md").write_text("handoff smoke\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True, env=env)

    brain = [sys.executable, "-m", "brain.cli"]
    r = subprocess.run(brain + ["init", "--accept-conditions", "--repos", str(repo), "--vault-root", str(vault),
                                "--endpoint", f"http://127.0.0.1:{PORT}"], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    link(HOME / ".claude-profiles" / a.profile / ".credentials.json", root / "claude" / ".credentials.json")
    link(HOME / ".codex-profiles" / a.profile / "auth.json", root / "codex" / "auth.json")
    link(HOME / ".gemini/antigravity-cli-profiles" / a.profile / "antigravity-oauth-token",
         root / "agy" / "antigravity-oauth-token")  # Brain's default agy profile lives in BRAIN_ROOT/agy

    srv = subprocess.Popen(brain + ["serve", "--port", str(PORT), "--tick", "2"], env=env,
                           stdout=open(tmp / "serve.log", "w"), stderr=subprocess.STDOUT)
    results: dict = {}
    try:
        for _ in range(60):
            try:
                req("GET", "/v1/health")
                break
            except Exception:
                time.sleep(0.5)
        repo_id = req("GET", "/v1/repos")[0]["id"]
        tid = req("POST", "/v1/threads", {"repo_id": repo_id, "title": "handoff", "mode": "worktree",
                                          "provider": "claude"})["id"]
        needles = {}

        def plant(p: str) -> str:
            n = f"NEEDLE-{random.randint(10000, 99999)}-{p.upper()}"
            f = tmp / f"secret-{p}.txt"
            f.write_text(n + "\n")
            needles[p] = (n, f)
            return str(f)

        def check(name, text, tools, want):
            ok = all(n in text for n in want) and not tools
            results[name] = {"pass": ok, "answer": text[-200:], "tools": tools, "want": want}
            print(f"  {'PASS' if ok else 'FAIL'}  {name}: {text.strip()[:120]!r} tools={tools}", flush=True)

        for prov, tool_hint in (("claude", "Use the Bash tool to run"), ("codex", "Run the shell command"),
                                ("agy", "Use the run_command tool to run")):
            if prov != "claude":
                req("POST", f"/v1/threads/{tid}/provider", {"provider": prov})
                seen = [needles[p][0] for p in needles]
                text, tools, notes = turn(tid, RECALL.format(what="files named secret-*.txt were printed",
                                                             fmt="their exact contents, one per line"))
                check(f"recall_on_{prov}", text, tools, seen)
                print("      notes:", notes, flush=True)
            path = plant(prov)
            text, tools, _ = turn(tid, f"{tool_hint}: cat {path}  Then reply with just: noted")
            needles[prov][1].unlink()
            results[f"{prov}_ran_cat"] = {"pass": any("cat" in t for t in tools), "tools": tools}
            print(f"  {'PASS' if results[f'{prov}_ran_cat']['pass'] else 'FAIL'}  {prov} ran cat: {tools}", flush=True)

        req("POST", f"/v1/threads/{tid}/provider", {"provider": "claude"})
        text, tools, notes = turn(tid, RECALL.format(what="files named secret-*.txt were printed",
                                                     fmt="their exact contents, one per line"))
        check("recall_back_on_claude", text, tools, [needles[p][0] for p in needles])
        print("      notes:", notes, flush=True)

        for prov in ("codex", "agy"):  # no-defer approvals: deny while pending, resume with a prompt
            target = vault / f"old-run-{prov}"  # distinct per provider: an approval covers that exact call for the thread
            target.mkdir(exist_ok=True)
            req("POST", f"/v1/threads/{tid}/provider", {"provider": prov})
            before = sum(e["type"] == "turn.ended" for e in events(tid))
            turn(tid, f"Run exactly this shell command: rm -r {target}")
            pend = req("GET", "/v1/pending")["approvals"]
            held = target.exists() and bool(pend)
            if pend:
                before = sum(e["type"] == "turn.ended" for e in events(tid))
                req("POST", f"/v1/approvals/{pend[0]['id']}/decision", {"decision": "allow", "device_sig": ""})
                wait_turn(tid, before)
            ok = held and not target.exists()
            results[f"approval_{prov}"] = {"pass": ok, "held_while_pending": held, "deleted_after": not target.exists()}
            print(f"  {'PASS' if ok else 'FAIL'}  approval on {prov}: held={held} deleted_after={not target.exists()}",
                  flush=True)

        # repo/vault scope: a plain out-of-scope write needs approval (policy, every provider); a disguised one is
        # stopped by Codex's OS sandbox (Claude and agy have no usable sandbox here, so only the policy applies)
        outside = HOME / ".brain-scope-smoke"
        outside.mkdir(exist_ok=True)
        req("POST", f"/v1/threads/{tid}/provider", {"provider": "codex"})
        turn(tid, f"Run exactly this shell command: cp {repo / 'README.md'} {outside / 'plain.txt'}")
        pend = req("GET", "/v1/pending")["approvals"]
        ok = bool(pend) and pend[0]["action_type"] == "write_outside_scope" and not (outside / "plain.txt").exists()
        if pend:
            req("POST", f"/v1/approvals/{pend[0]['id']}/decision", {"decision": "deny", "device_sig": ""})
        results["scope_plain_write_asks"] = {"pass": ok}
        print(f"  {'PASS' if ok else 'FAIL'}  out-of-scope cp asked for approval and did not run", flush=True)
        while req("GET", f"/v1/threads/{tid}")["state"] == "running":
            time.sleep(1)
        evade = outside / "evade.txt"
        turn(tid, f"""Run exactly this shell command: python3 -c "open('{evade}','w').write('x')" """)
        ok = not evade.exists()
        results["scope_codex_sandbox_blocks_disguised_write"] = {"pass": ok}
        print(f"  {'PASS' if ok else 'FAIL'}  codex OS sandbox blocked a disguised out-of-scope write", flush=True)
        for f in (outside / "plain.txt", evade):
            f.unlink(missing_ok=True)
        outside.rmdir()
    finally:
        srv.terminate()
        (Path(__file__).parent / "handoff-live-report.json").write_text(json.dumps(results, indent=2))
        print(f"\nserve log: {tmp / 'serve.log'}")
    return 0 if results and all(v["pass"] for v in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
