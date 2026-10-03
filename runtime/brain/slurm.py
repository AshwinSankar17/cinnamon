"""SLURM submission and a scheduler-polite job watcher.

Sentinel first, scheduler second: every submitted script is wrapped so that `trap … EXIT` writes
`<run_dir>/.brain/status.json` with the exit code. The watcher stats those files every 30 s, runs one
batched `squeue -j <ids>` at the site floor (default 60 s), and calls `sacct -j <id>` only for jobs that
vanished from squeue without a sentinel. Never `scontrol` loops, never `sacct -u $USER` over wide windows.
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "PREEMPTED",
            "BOOT_FAIL", "DEADLINE"}


@dataclass
class JobUpdate:
    job_id: str
    state: str
    exit_code: int | None = None
    reason: str = ""


def _run(cmd: list[str], timeout: float = 30) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"{cmd[0]}: timed out"


def wrap_script(original: Path, sentinel: Path) -> str:
    """A wrapper that keeps the original #SBATCH directives and writes the sentinel on exit."""
    directives = [line for line in original.read_text().splitlines() if line.startswith("#SBATCH")]
    s = shlex.quote(str(sentinel))
    return "\n".join([
        "#!/bin/bash",
        *directives,
        f"mkdir -p {shlex.quote(str(sentinel.parent))}",
        "_brain_done() {",
        "  rc=$?",
        f"  printf '{{\"job_id\":\"%s\",\"exit_code\":%d,\"ended_at\":%d,\"restart\":%s}}\\n' "
        f"\"${{SLURM_JOB_ID:-?}}\" \"$rc\" \"$(date +%s)\" \"${{SLURM_RESTART_COUNT:-0}}\" > {s}.tmp && mv {s}.tmp {s}",
        "}",
        "trap _brain_done EXIT",
        f"bash {shlex.quote(str(original))} \"$@\"",
        "",
    ])


def submit(script: Path, *, job_name: str, comment: dict, run_dir: Path, extra_args: list[str] | None = None,
           env_exports: dict[str, str] | None = None, cwd: Path | None = None) -> tuple[str, Path]:
    run_dir.mkdir(parents=True, exist_ok=True)
    sentinel = run_dir / ".brain" / "status.json"
    wrapper = run_dir / ".brain" / "submit.sh"
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text(wrap_script(script, sentinel))
    wrapper.chmod(0o700)
    exports = ",".join(["ALL"] + [f"{k}={v}" for k, v in (env_exports or {}).items()])
    cmd = ["sbatch", "--parsable", f"--job-name={job_name}", f"--comment={json.dumps(comment, separators=(',', ':'))}",
           "--signal=B:TERM@120", f"--export={exports}", f"--output={run_dir / 'slurm-%j.out'}",
           *(extra_args or []), str(wrapper)]
    if cwd:
        cmd.insert(1, f"--chdir={cwd}")
    rc, out, err = _run(cmd)
    if rc != 0:
        raise RuntimeError(f"sbatch failed ({rc}): {err.strip() or out.strip()}")
    job_id = out.strip().split(";")[0].strip()
    if not re.fullmatch(r"\d+(_\d+)?", job_id):
        raise RuntimeError(f"unexpected sbatch output: {out!r}")
    return job_id, sentinel


def cancel(job_id: str) -> bool:
    rc, _, _ = _run(["scancel", job_id])
    return rc == 0


def read_sentinel(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


class Watcher:
    def __init__(self, squeue_interval: float = 60.0, sentinel_interval: float = 30.0):
        self.squeue_interval = squeue_interval
        self.sentinel_interval = sentinel_interval
        self._last_squeue = 0.0
        self._last_sentinel = 0.0

    def poll(self, jobs: list[dict], force: bool = False) -> list[JobUpdate]:
        """jobs: rows with job_id, state, sentinel. Returns state changes."""
        now = time.time()
        updates: list[JobUpdate] = []
        live = [j for j in jobs if j["state"] not in TERMINAL]
        if not live:
            return updates
        if force or now - self._last_sentinel >= self.sentinel_interval:
            self._last_sentinel = now
            for j in live:
                s = read_sentinel(Path(j["sentinel"])) if j.get("sentinel") else None
                if s is not None:
                    rc = int(s.get("exit_code", 1))
                    updates.append(JobUpdate(j["job_id"], "COMPLETED" if rc == 0 else "FAILED", rc, "sentinel"))
        done = {u.job_id for u in updates}
        remaining = [j for j in live if j["job_id"] not in done]
        if remaining and (force or now - self._last_squeue >= self.squeue_interval):
            self._last_squeue = now
            ids = ",".join(j["job_id"] for j in remaining)
            rc, out, _ = _run(["squeue", "-h", "-j", ids, "-o", "%i|%T|%r"])
            if rc == 0:
                seen: dict[str, tuple[str, str]] = {}
                for line in out.splitlines():
                    parts = line.strip().split("|")
                    if len(parts) >= 2:
                        seen[parts[0]] = (parts[1], parts[2] if len(parts) > 2 else "")
                for j in remaining:
                    if j["job_id"] in seen:
                        st, reason = seen[j["job_id"]]
                        if st != j["state"]:
                            updates.append(JobUpdate(j["job_id"], st, None, reason))
                    else:
                        updates.extend(self._sacct(j["job_id"]))
        return updates

    def _sacct(self, job_id: str) -> list[JobUpdate]:
        rc, out, _ = _run(["sacct", "-j", job_id, "-n", "-P", "-X", "-o", "JobID,State,ExitCode"])
        if rc != 0 or not out.strip():
            return []
        parts = out.strip().splitlines()[0].split("|")
        state = parts[1].split()[0] if len(parts) > 1 else "UNKNOWN"
        code = None
        if len(parts) > 2 and ":" in parts[2]:
            try:
                code = int(parts[2].split(":")[0])
            except ValueError:
                pass
        return [JobUpdate(job_id, state, code, "sacct")]
