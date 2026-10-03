#!/usr/bin/env python3
"""Cluster Brain risk tests — run these on aqua-hpc before relying on Brain unattended.

    python3 spikes/spikes.py                       # S1–S3, S5 on this node
    python3 spikes/spikes.py --alloc 4839990       # S2 also reroutes into a running SLURM allocation
    python3 spikes/spikes.py --ssh myhost          # S4: drive claude on another machine over SSH

Needs: the `claude` CLI on PATH and logged in (it uses your plan; each test is a tiny prompt).
Writes raw stream-json for every run to ./spike-out/ and a summary to ./spike-out/report.json.
Send report.json (and any FAIL's raw .jsonl) back so the runtime can be adjusted.

S1  Deferred approval: a PreToolUse command hook returns `defer` for `rm -rf old_vault`; the turn must end
    without running it; `claude -p --resume <id>` with NO prompt must re-attempt the call, and the hook
    then allows it. Run in the permission mode you'll use (default: auto).
S2  Rerouting: the hook rewrites a heavy command via `updatedInput`; the tool result must show the
    rewritten command ran (and, with --alloc, that it ran on the allocation's node).
S3  Auto-mode coexistence: with the hook giving no opinion, ordinary commands still run in auto mode.
S4  SSH (optional): run claude on another host over SSH with stream-json intact.
S5  Footprint: peak RSS and process count of one claude turn (for the admin's caps).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

OUT = Path("spike-out")
HOOK = r'''#!/usr/bin/env python3
import json, os, sys
d = json.load(sys.stdin)
cmd = (d.get("tool_input") or {}).get("command", "")
state = os.environ["SPIKE_STATE"]
log = open(state + ".hooklog", "a"); log.write(json.dumps(d) + "\n"); log.close()
def emit(**h):
    h["hookEventName"] = "PreToolUse"; print(json.dumps({"hookSpecificOutput": h}))
if "rm -rf old_vault" in cmd:
    decision = open(state).read().strip() if os.path.exists(state) else ""
    if decision == "allow":
        emit(permissionDecision="allow", permissionDecisionReason="approved on phone (spike)")
    else:
        emit(permissionDecision="defer", permissionDecisionReason="waiting for the owner's approval (spike)")
elif "gpu_probe.py" in cmd:
    alloc = os.environ.get("SPIKE_ALLOC", "")
    new = (f"srun --jobid={alloc} --overlap -N1 -n1 bash -lc 'echo REROUTED-BY-BRAIN; hostname'" if alloc
           else "echo REROUTED-BY-BRAIN; hostname")
    emit(permissionDecision="allow", updatedInput={**d["tool_input"], "command": new})
'''


class RSSSampler(threading.Thread):
    """Samples RSS of a process tree via /proc (Linux)."""

    def __init__(self, pid: int):
        super().__init__(daemon=True)
        self.pid, self.peak_kb, self.max_procs, self.stop = pid, 0, 0, False

    def _tree(self) -> list[int]:
        kids, out = {}, []
        for p in Path("/proc").iterdir():
            if p.name.isdigit():
                try:
                    ppid = int((p / "stat").read_text().rsplit(")", 1)[1].split()[1])
                    kids.setdefault(ppid, []).append(int(p.name))
                except (OSError, IndexError, ValueError):
                    pass
        stack = [self.pid]
        while stack:
            x = stack.pop()
            out.append(x)
            stack.extend(kids.get(x, []))
        return out

    def run(self) -> None:
        while not self.stop:
            total, pids = 0, self._tree()
            for pid in pids:
                try:
                    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
                        if line.startswith("VmRSS:"):
                            total += int(line.split()[1])
                except OSError:
                    pass
            self.peak_kb = max(self.peak_kb, total)
            self.max_procs = max(self.max_procs, len(pids))
            time.sleep(0.2)


def run_claude(name: str, args: list[str], cwd: Path, env: dict, prefix: list[str] | None = None,
               timeout: int = 300) -> dict:
    cmd = (prefix or []) + args
    t0 = time.time()
    p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    sampler = RSSSampler(p.pid)
    if Path("/proc").exists():
        sampler.start()
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        out, err = p.communicate()
    sampler.stop = True
    (OUT / f"{name}.jsonl").write_text(out)
    (OUT / f"{name}.stderr").write_text(err)
    lines = []
    for line in out.splitlines():
        try:
            lines.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    result = next((m for m in reversed(lines) if m.get("type") == "result"), {})
    tool_results = []
    for m in lines:
        if m.get("type") == "user":
            for b in (m.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    c = b.get("content")
                    tool_results.append(c if isinstance(c, str) else json.dumps(c))
    return {"rc": p.returncode, "secs": round(time.time() - t0, 1), "session_id": result.get("session_id") or
            next((m.get("session_id") for m in lines if m.get("session_id")), None),
            "result": {k: result.get(k) for k in result if k not in ("result",)}, "tool_results": tool_results,
            "peak_rss_mb": round(sampler.peak_kb / 1024, 1), "max_procs": sampler.max_procs,
            "stderr_tail": err[-400:], "n_lines": len(lines)}


def base_args(prompt: str | None, mode: str, settings: dict, resume: str | None = None) -> list[str]:
    a = ["claude", "-p"] + ([prompt] if prompt is not None else [])
    a += ["--output-format", "stream-json", "--verbose", "--permission-mode", mode, "--settings", json.dumps(settings)]
    if resume:
        a += ["--resume", resume]
    return a


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="auto", help="permission mode Brain will use (auto | acceptEdits | bypassPermissions)")
    ap.add_argument("--alloc", help="a running SLURM job id to reroute S2 into (e.g. from `salloc --no-shell`)")
    ap.add_argument("--ssh", help="host for S4 (key-only SSH, claude installed there)")
    a = ap.parse_args()
    if not shutil.which("claude"):
        print("claude CLI not found on PATH")
        return 2
    OUT.mkdir(exist_ok=True)
    ver = subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip()
    work = Path(tempfile.mkdtemp(prefix="brain-spike-"))
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    hook = work / "hook.py"
    hook.write_text(HOOK)
    hook.chmod(0o755)
    state = work / ".decision"
    settings = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command",
                                                                        "command": f"{sys.executable} {hook}"}]}]}}
    env = dict(os.environ, SPIKE_STATE=str(state), SPIKE_ALLOC=a.alloc or "")
    report: dict = {"claude_version": ver, "host": socket.gethostname(), "mode": a.mode, "tests": {}}

    # S1 — defer, then resume with no prompt
    (work / "old_vault").mkdir()
    (work / "old_vault" / "ckpt.pt").write_text("x")
    r1 = run_claude("S1-run", base_args("Use the Bash tool to run exactly this command and nothing else: "
                                        "rm -rf old_vault", a.mode, settings), work, env)
    deferred_ok = (work / "old_vault").exists()
    state.write_text("allow")
    r2 = run_claude("S1-resume", base_args(None, a.mode, settings, resume=r1["session_id"]), work, env) \
        if r1["session_id"] else {"rc": None, "note": "no session id from first run"}
    resumed_ok = not (work / "old_vault").exists()
    r3 = None
    if deferred_ok and not resumed_ok and r1["session_id"]:  # fallback: resume with a nudge
        r3 = run_claude("S1-resume-with-prompt", base_args("The owner approved. Continue.", a.mode, settings,
                                                          resume=r1["session_id"]), work, env)
        resumed_ok_prompt = not (work / "old_vault").exists()
    report["tests"]["S1_defer_resume"] = {
        "pass": deferred_ok and resumed_ok,
        "deferred_without_running": deferred_ok,
        "resume_without_prompt_ran_it": resumed_ok,
        "resume_with_prompt_ran_it": (resumed_ok_prompt if r3 else None),
        "first_run_result_fields": r1.get("result"), "resume_result_fields": r2.get("result"),
        "hook_calls": len((work / ".decision.hooklog").read_text().splitlines()) if (work / ".decision.hooklog").exists() else 0,
    }
    report["tests"]["S5_footprint"] = {"peak_rss_mb": r1.get("peak_rss_mb"), "max_procs": r1.get("max_procs"),
                                       "secs": r1.get("secs")}

    # S2 — updatedInput reroute
    r = run_claude("S2-reroute", base_args("Use the Bash tool to run exactly: python gpu_probe.py", a.mode, settings),
                   work, env)
    outs = "\n".join(r["tool_results"])
    login = socket.gethostname()
    rerouted = "REROUTED-BY-BRAIN" in outs
    on_alloc = None
    if a.alloc and rerouted:
        on_alloc = login not in outs.split("REROUTED-BY-BRAIN", 1)[1]
    report["tests"]["S2_updatedInput_reroute"] = {"pass": rerouted and (on_alloc is not False),
                                                  "rerouted": rerouted, "ran_on_allocation_node": on_alloc,
                                                  "tool_output": outs[-400:]}

    # S3 — ordinary command under the chosen mode with the hook silent
    r = run_claude("S3-auto-ls", base_args("Use the Bash tool to run: ls -a", a.mode, settings), work, env)
    ran = any(".decision" in t or "hook.py" in t for t in r["tool_results"])
    report["tests"]["S3_mode_coexistence"] = {"pass": ran, "tool_output": "\n".join(r["tool_results"])[-300:],
                                              "result_fields": r.get("result")}

    # S4 — over SSH (optional)
    if a.ssh:
        r = run_claude("S4-ssh", ["ssh", "-T", "-o", "BatchMode=yes", a.ssh, "claude", "-p", "'Reply with the word pong'",
                                  "--output-format", "stream-json", "--verbose"], work, env)
        ok = any("pong" in json.dumps(l).lower() for l in [r.get("result")])
        report["tests"]["S4_ssh"] = {"pass": ok and r["rc"] == 0, "rc": r["rc"], "result_fields": r.get("result"),
                                     "stderr_tail": r.get("stderr_tail")}

    (OUT / "report.json").write_text(json.dumps(report, indent=2, default=str))
    print(f"\nclaude {ver} on {report['host']} · mode {a.mode}")
    for k, v in report["tests"].items():
        status = "PASS" if v.get("pass", True) else "FAIL"
        extra = ""
        if k == "S5_footprint":
            status, extra = "INFO", f"peak RSS {v['peak_rss_mb']} MB, {v['max_procs']} processes"
        print(f"  {status:4}  {k} {extra}")
    print(f"\nRaw output in {OUT.resolve()} — send report.json back.")
    shutil.rmtree(work, ignore_errors=True)
    return 0 if all(v.get("pass", True) for v in report["tests"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
