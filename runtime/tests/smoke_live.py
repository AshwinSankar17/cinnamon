#!/usr/bin/env python3
"""Live smoke test: real `brain serve` on its Unix socket + TCP port, the Claude adapter driving a stand-in
`claude` CLI, the real brain-hook round-trip, `brain submit` into fake SLURM, a wake-up, and a deferred
approval resumed with `--resume` and no prompt.

    python tests/smoke_live.py        (from runtime/, inside the venv)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PORT = 18765


def req(method, path, body=None):
    r = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method,
                               data=json.dumps(body).encode() if body is not None else None,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=10) as resp:
        return json.loads(resp.read())


def wait_for(pred, what, timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.3)
    raise SystemExit(f"FAIL: timed out waiting for {what}")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="brain-smoke-"))
    env = dict(os.environ, BRAIN_ROOT=str(tmp / ".brain"), FAKESLURM_DIR=str(tmp / "slurm"),
               BRAIN_DEV_NO_AUTH="1", BRAIN_CLAUDE_BIN=str(HERE / "fakeclaude" / "claude"),
               PATH=f"{HERE / 'fakeslurm'}:{Path(sys.executable).parent}:{os.environ['PATH']}",
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    repo = tmp / "hi-tts-v3"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, env=env)
    (repo / "job.sh").write_text("#!/bin/bash\n#SBATCH --time=00:01:00\necho training step 1; sleep 1; echo done\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True, env=env)
    vault = tmp / "vaults"
    (vault / "old-run").mkdir(parents=True)

    brain = [sys.executable, "-m", "brain.cli"]
    r = subprocess.run(brain + ["init", "--accept-conditions", "--repos", str(repo), "--vault-root", str(vault),
                                "--endpoint", f"http://127.0.0.1:{PORT}"], env=env, capture_output=True, text=True)
    print(r.stdout[-600:])
    assert r.returncode == 0, r.stderr
    srv = subprocess.Popen(brain + ["serve", "--port", str(PORT), "--tick", "1"], env=env,
                           stdout=open(tmp / "serve.log", "w"), stderr=subprocess.STDOUT)
    try:
        wait_for(lambda: _ok(lambda: req("GET", "/v1/health")), "server up")
        print("health:", req("GET", "/v1/health"))
        pr = subprocess.run(brain + ["pair"], env=env, capture_output=True, text=True)
        assert "Pairing code" in pr.stdout, pr.stdout + pr.stderr
        print("pair: ok (via running Brain's socket)")

        th = req("POST", "/v1/threads", {"repo_id": "hi-tts-v3", "title": "sanity run", "mode": "worktree",
                                         "provider": "claude"})
        tid = th["id"]
        print("thread:", tid, th["branch"])

        req("POST", f"/v1/threads/{tid}/messages", {"text": "please submit the sanity job"})
        jobs = wait_for(lambda: req("GET", "/v1/jobs"), "job submitted via `brain submit` from inside the turn")
        print("job:", jobs[0]["job_id"], jobs[0]["name"])
        wait_for(lambda: req("GET", "/v1/jobs")[0]["state"] == "COMPLETED", "job completion via sentinel")
        def woke():
            evs = req("GET", f"/v1/threads/{tid}/events?after_seq=0&limit=500")["events"]
            starts = [e for e in evs if e["type"] == "turn.started"]
            ends = [e for e in evs if e["type"] == "turn.ended"]
            return len(starts) >= 2 and len(ends) >= 2 and starts[-1]["payload"].get("wake_reason") == "wait fired"
        wait_for(woke, "wake-up turn after the job finished")
        print("wake: ok")

        req("POST", f"/v1/threads/{tid}/messages", {"text": f"clean up: rm -rf {vault}/old-run"})
        pend = wait_for(lambda: req("GET", "/v1/pending")["approvals"], "approval created by the hook")
        a = pend[0]
        print("approval:", a["id"], a["action_type"], a["decision_summary"]["verb"])
        assert (vault / "old-run").exists(), "deferred action ran before approval!"
        assert req("GET", f"/v1/threads/{tid}")["state"] == "needs_you"
        req("POST", f"/v1/approvals/{a['id']}/decision", {"decision": "allow", "device_sig": "dev"})
        wait_for(lambda: not (vault / "old-run").exists(), "approved action to run after resume")
        wait_for(lambda: req("GET", f"/v1/threads/{tid}")["state"] == "idle", "thread back to idle")
        print("deferred approval → resume: ok")

        evs = req("GET", f"/v1/threads/{tid}/events?after_seq=0&limit=500")["events"]
        print("event types:", sorted({e["type"] for e in evs}))
        ir = subprocess.run(brain + ["ir-dump"], env=env, capture_output=True, text=True).stdout
        assert "sbatch" in ir and "approval" in ir
        print("ir-dump: ok")
        print("\nSMOKE PASSED")
        return 0
    finally:
        srv.terminate()
        srv.wait(10)
        log = (tmp / "serve.log").read_text()
        if "Traceback" in log:
            print(log[-3000:])
        shutil.rmtree(tmp, ignore_errors=True)


def _ok(fn):
    try:
        return fn()
    except Exception:
        return None


if __name__ == "__main__":
    sys.exit(main())
