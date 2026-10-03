#!/usr/bin/env python3
"""Fake SLURM for tests: sbatch/squeue/sacct/scancel backed by a JSON state dir ($FAKESLURM_DIR).

sbatch runs the script immediately in the background (as if it were scheduled at once) and records
the exit code when it finishes. Good enough to exercise Brain's sentinel + squeue + sacct logic in CI.
"""
import json, os, signal, subprocess, sys, time
from pathlib import Path

D = Path(os.environ.get("FAKESLURM_DIR", "/tmp/fakeslurm"))
D.mkdir(parents=True, exist_ok=True)
STATE = D / "state.json"

def load():
    return json.loads(STATE.read_text()) if STATE.exists() else {"next": 1000, "jobs": {}}

def save(s):
    STATE.write_text(json.dumps(s))

def alive(pid):
    try:
        os.kill(pid, 0); return True
    except OSError:
        return False

def sbatch(args):
    s = load(); jid = str(s["next"]); s["next"] += 1
    script = args[-1]; name = "job"; out = None; chdir = None
    for a in args[:-1]:
        if a.startswith("--job-name="): name = a.split("=", 1)[1]
        if a.startswith("--output="): out = a.split("=", 1)[1].replace("%j", jid)
        if a.startswith("--chdir="): chdir = a.split("=", 1)[1]
    rcfile = D / f"{jid}.rc"
    env = dict(os.environ, SLURM_JOB_ID=jid)
    outf = open(out or os.devnull, "w")
    p = subprocess.Popen(["sh", "-c", f'bash "$0"; echo $? > "{rcfile}"', script], env=env, stdout=outf,
                         stderr=subprocess.STDOUT, cwd=chdir, start_new_session=True)
    s["jobs"][jid] = {"name": name, "pid": p.pid, "state": "RUNNING", "submitted": time.time()}
    save(s); print(jid)

def finished_state(jid, job):
    rc = D / f"{jid}.rc"
    if job.get("state") == "CANCELLED": return "CANCELLED", 0
    if rc.exists():
        code = int(rc.read_text().strip() or 1)
        return ("COMPLETED" if code == 0 else "FAILED"), code
    return None, None

def squeue(args):
    s = load(); ids = None; fmt = "%i|%T|%r"
    for i, a in enumerate(args):
        if a == "-j": ids = args[i + 1].split(",")
        if a == "-o": fmt = args[i + 1]
    for jid, job in s["jobs"].items():
        if ids and jid not in ids: continue
        st, _ = finished_state(jid, job)
        if st is None and alive(job["pid"]):
            print(fmt.replace("%i", jid).replace("%T", "RUNNING").replace("%r", "None").replace("%j", job["name"]))

def sacct(args):
    s = load(); jid = args[args.index("-j") + 1]
    job = s["jobs"].get(jid)
    if not job: return
    st, code = finished_state(jid, job)
    if st is None: st, code = ("RUNNING", 0) if alive(job["pid"]) else ("FAILED", 1)
    print(f"{jid}|{st}|{code}:0")

def scancel(args):
    s = load()
    for jid in [a for a in args if not a.startswith("-")]:
        job = s["jobs"].get(jid)
        if job:
            try: os.killpg(job["pid"], signal.SIGTERM)
            except OSError: pass
            job["state"] = "CANCELLED"
    save(s)

if __name__ == "__main__":
    cmd = Path(sys.argv[0]).name
    {"sbatch": sbatch, "squeue": squeue, "sacct": sacct, "scancel": scancel}[cmd](sys.argv[1:])
