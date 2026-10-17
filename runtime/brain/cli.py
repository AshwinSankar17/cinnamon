"""`brain` command line: init, serve, pair, submit, devices, doctor, ir-dump, probe."""
from __future__ import annotations

import argparse
import contextlib
import asyncio
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

from . import __version__
from .config import Paths, RepoConfig, Settings, check_permissions, ensure_root, paths

CONDITIONS = Path(__file__).resolve().parent.parent.parent / "CONDITIONS.md"


def _open(p: Paths, *, lease: bool = True):
    from .db import DB, Lease, connect
    ls = None
    if lease:
        ls = Lease(p.lease)
        ls.acquire()
    return DB(connect(p.db)), ls


def cmd_init(a: argparse.Namespace) -> int:
    from .repos import is_repo
    from .site_probe import run_probe, write_profile
    p = ensure_root()
    print(f"Brain root: {p.root} (0700)")
    probs = check_permissions(p, fix=a.fix_perms)
    if probs and not a.fix_perms:
        print("Permission problems (fix with `brain init --fix-perms`):\n  " + "\n  ".join(probs))
        return 2
    if not a.accept_conditions:
        if CONDITIONS.exists():
            print("\n" + CONDITIONS.read_text())
        print("Re-run with --accept-conditions once you (and, if needed, your admin) agree to the conditions above.")
        return 1
    print("Probing the site (sinfo, scontrol, quota, egress)…")
    prof = run_probe("external" if a.external_host else None)
    write_profile(p.site_profile, prof)
    print(f"  placement: rung {prof['rung']} — {prof['rung_reason']}")
    for k, v in prof["egress"].items():
        if k != "https_proxy":
            print(f"  egress {k}: {'ok' if v else 'blocked'}")
    s = Settings.load(p)
    if a.endpoint:
        s.endpoint = a.endpoint
    if a.alt_endpoint:
        s.raw["alt_endpoints"] = a.alt_endpoint
    for key in ("projects_root", "vault_root"):  # where phone-created projects and their vaults go
        v = getattr(a, key)
        if v:
            s.raw[key] = str(Path(v).expanduser().resolve())
            Path(s.raw[key]).mkdir(parents=True, exist_ok=True)
    known = {r.path for r in s.repos}
    for root in a.repos or []:
        rp = Path(root).expanduser().resolve()
        cands = [rp] if is_repo(rp) else [d for d in sorted(rp.iterdir()) if d.is_dir() and (d / ".git").exists()]
        for d in cands:
            if str(d) not in known:
                s.repos.append(RepoConfig(id=d.name, path=str(d), name=d.name,
                                          worktree_root=str(Path(a.worktree_root or d.parent / ".brain-worktrees")),
                                          vault_root=a.vault_root or ""))
                known.add(str(d))
                print(f"  registered repo {d.name} ({d})")
    s.raw.setdefault("claude_permission_mode", "auto")
    s.save(p)
    db, ls = _open(p)
    from .identity import Identity
    ident = Identity(p.identity_key, p.token_secret)
    print(f"Brain identity: {ident.fingerprint()}")
    print(f"Claude config dir for Brain-run sessions: {p.claude_config_dir}\n"
          f"  If `claude` is not logged in there yet, run once:  CLAUDE_CONFIG_DIR={p.claude_config_dir} claude")
    if a.fcm_key:
        src = Path(a.fcm_key).expanduser()
        p.fcm_key.write_bytes(src.read_bytes())
        os.chmod(p.fcm_key, 0o600)
        print(f"FCM key installed at {p.fcm_key} (0600). Give it only the 'Firebase Cloud Messaging API Admin' role.")
    ls.release()
    print("\nNext: `brain serve` (see deploy/ for keep-alive on rung " + prof["rung"] + "), then `brain pair`.")
    return 0


def _internal(path: str, body: dict) -> tuple[int, object]:
    """Owner-only admin calls to the running Brain over its Unix socket (admin token; agents only get the hook token)."""
    from .hook import _UnixHTTPConnection
    p = paths()
    conn = _UnixHTTPConnection(str(p.sock), timeout=20)
    conn.request("POST", path, body=json.dumps(body),
                 headers={"Content-Type": "application/json", "X-Brain-Admin-Token": p.admin_token.read_text().strip()})
    r = conn.getresponse()
    return r.status, json.loads(r.read() or b"null")


def _brain_running() -> bool:
    p = paths()
    return p.sock.exists() and p.hook_token.exists()


def cmd_pair(a: argparse.Namespace) -> int:
    p = paths()
    s = Settings.load(p)
    endpoint = a.endpoint or s.endpoint
    if _brain_running():
        try:
            code, payload = _internal("/internal/pair", {"endpoint": endpoint})
        except OSError:
            code, payload = 0, None
        if code != 200:
            print(f"Brain refused to create a pairing: {payload}")
            return 2
        fp = payload.pop("fingerprint", "")
    else:
        if not endpoint:
            print("No endpoint known. Pass --endpoint https://<your relay hostname>")
            return 2
        from .identity import Identity, Pairing
        db, ls = _open(p)
        ident = Identity(p.identity_key, p.token_secret)
        payload = Pairing(db, ident).create(endpoint, [e for e in s.raw.get("alt_endpoints", []) if e != endpoint])
        fp = ident.fingerprint()
        ls.release()
    text = json.dumps(payload, separators=(",", ":"))
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(text)
        qr.print_ascii(invert=True)
    except ImportError:
        print("(pip install qrcode for an ASCII QR)")
    print(f"\nPairing code {payload['code']} — valid 5 minutes. Paste this into the app if you can't scan:\n{text}")
    print(f"Brain fingerprint: {fp}")
    return 0


def cmd_serve(a: argparse.Namespace) -> int:
    import uvicorn
    from .api import create_app
    from .core import Brain
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = ensure_root()
    probs = check_permissions(p)
    if probs:
        print("Refusing to start:\n  " + "\n  ".join(probs))
        return 2
    db, ls = _open(p)
    brain = Brain(p, Settings.load(p), db)
    dev = bool(os.environ.get("BRAIN_DEV_NO_AUTH"))
    app = create_app(brain, dev_no_auth=dev)
    if p.sock.exists():
        p.sock.unlink()

    class Server(uvicorn.Server):
        # uvicorn re-raises SIGTERM/SIGINT after its graceful shutdown, which kills the process before the lease is
        # released (the next start then waits out a stale lease). Brain handles the signals itself, below.
        @contextlib.contextmanager
        def capture_signals(self):
            yield

    async def main() -> None:
        servers = [Server(uvicorn.Config(app, uds=str(p.sock), log_level="info"))]
        if a.port:
            servers.append(Server(uvicorn.Config(app, host="127.0.0.1", port=a.port, log_level="info")))
        sched = asyncio.create_task(brain.run_scheduler(a.tick))
        watcher = asyncio.create_task(brain.run_watch())
        deadline = time.time() + a.max_hours * 3600 if a.max_hours else None

        async def watchdog() -> None:
            while True:
                await asyncio.sleep(30)
                if deadline and time.time() > deadline:
                    logging.info("max-hours reached; exiting cleanly for the keep-alive to restart")
                    for s in servers:
                        s.should_exit = True
                    return

        wd = asyncio.create_task(watchdog())
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGUSR1, signal.SIGTERM, signal.SIGINT):  # USR1: SLURM --signal=B:USR1@600 hand-over
            try:
                loop.add_signal_handler(sig, lambda: [setattr(s, "should_exit", True) for s in servers])
            except (NotImplementedError, RuntimeError):
                pass
        await asyncio.sleep(0)
        os.chmod(p.sock, 0o600) if p.sock.exists() else None
        await asyncio.gather(*(s.serve() for s in servers))
        sched.cancel()
        watcher.cancel()
        wd.cancel()

    try:
        asyncio.run(main())
    finally:
        ls.release()
    return 0


def cmd_submit(a: argparse.Namespace) -> int:
    """Used by agents inside a thread: `brain submit train.sh -- --gpus=2 --time=24:00:00`."""
    from .hook import _UnixHTTPConnection
    sock = os.environ.get("BRAIN_HOOK_SOCK") or str(paths().sock)
    token = os.environ.get("BRAIN_HOOK_TOKEN") or paths().hook_token.read_text().strip()
    thread = os.environ.get("BRAIN_THREAD_ID") or a.thread
    if not thread:
        print("BRAIN_THREAD_ID is not set (run this from a Brain thread, or pass --thread)")
        return 2
    body = json.dumps({"thread_id": thread, "script": str(Path(a.script).resolve()), "sbatch_args": a.sbatch_args})
    conn = _UnixHTTPConnection(sock, timeout=60)
    conn.request("POST", "/internal/submit", body=body,
                 headers={"Content-Type": "application/json", "X-Brain-Hook-Token": token})
    r = conn.getresponse()
    data = json.loads(r.read() or b"{}")
    if r.status != 200:
        print(f"submit failed: {data}")
        return 1
    print(f"Submitted batch job {data['job_id']} as {data['name']}. "
          f"End your turn now; Brain will wake this thread when the job finishes.")
    return 0


def cmd_listen(a: argparse.Namespace) -> int:
    """Used by agents inside a thread: `brain listen evals/step10k/listen.json` asks the owner for a blind listening test."""
    from .hook import _UnixHTTPConnection
    sock = os.environ.get("BRAIN_HOOK_SOCK") or str(paths().sock)
    token = os.environ.get("BRAIN_HOOK_TOKEN") or paths().hook_token.read_text().strip()
    thread = os.environ.get("BRAIN_THREAD_ID") or a.thread
    if not thread:
        print("BRAIN_THREAD_ID is not set (run this from a Brain thread, or pass --thread)")
        return 2
    conn = _UnixHTTPConnection(sock, timeout=60)
    conn.request("POST", "/internal/listen", body=json.dumps({"thread_id": thread, "path": str(Path(a.manifest).resolve())}),
                 headers={"Content-Type": "application/json", "X-Brain-Hook-Token": token})
    r = conn.getresponse()
    data = json.loads(r.read() or b"{}")
    if r.status != 200:
        print(f"listen.json rejected: {data.get('error', data)}")
        return 1
    print(f"Listening test \"{data['title']}\" sent to the owner's phone ({data['items']} sentences × {data['systems']} "
          f"systems). End your turn now; Brain will wake this thread with the ratings.")
    return 0


def cmd_devices(a: argparse.Namespace) -> int:
    body = {a.action: a.device_id} if a.action in ("revoke", "approve") else {}
    if _brain_running():
        code, rows = _internal("/internal/devices", body)
    else:
        p = paths()
        db, ls = _open(p)
        try:
            if body.get("revoke"):
                from .identity import Identity, Pairing
                Pairing(db, Identity(p.identity_key, p.token_secret)).revoke(a.device_id)
            if body.get("approve"):
                db.x("UPDATE devices SET approved=1 WHERE id=? AND revoked=0", (a.device_id,))
                db.x("UPDATE approvals SET state='allowed', device_id='cli', decided_at=? WHERE action_type='pair_device'"
                     " AND state='pending' AND tool_input=?", (time.time(), a.device_id))
            rows = [dict(r) for r in db.q("SELECT * FROM devices ORDER BY created_at")]
        finally:
            ls.release()
    for r in rows:
        seen = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["last_seen"])) if r.get("last_seen") else "-"
        status = "REVOKED" if r["revoked"] else ("" if r.get("approved", True) else "WAITING FOR APPROVAL")
        print(f"{r['id']}  {r['name']:<20} last seen {seen}  {status}")
    return 0


def cmd_endpoint(a: argparse.Namespace) -> int:
    primary, alternates = a.url, a.alt or []
    if _brain_running():
        code, res = _internal("/internal/endpoint", {"endpoint": primary, "alternates": alternates})
        print(f"endpoint set; signed push sent to {res['pushed_to']} device(s)" if code == 200 else f"failed: {res}")
        return 0 if code == 200 else 1
    p = paths()
    s = Settings.load(p)
    s.endpoint = primary
    s.raw["alt_endpoints"] = [x for x in alternates if x != primary]
    s.save(p)
    print("endpoint saved (Brain not running, so no push sent)")
    return 0


def cmd_import(a: argparse.Namespace) -> int:
    if not _brain_running():
        print("Start Brain first (`brain serve`); import runs inside it.")
        return 1
    conf = {k: v for k, v in {"root": a.root and str(Path(a.root).expanduser().resolve()),
                              "claude_dirs": a.claude_dir, "codex_dirs": a.codex_dir, "days": a.days}.items() if v}
    if conf:
        code, res = _internal("/internal/imports", {"configure": conf})
        if code != 200:
            print(f"failed: {res}")
            return 1
    if a.session:
        code, res = _internal("/internal/imports", {"provider": a.provider, "session_id": a.session})
        print(f"imported as thread {res['id']}: {res['title']}" if code == 200 else f"failed: {res}")
        return 0 if code == 200 else 1
    code, res = _internal("/internal/imports", {"all": True} if a.all else {"list": True})
    if code != 200:
        print(f"failed: {res}")
        return 1
    for c in res:
        if a.all:
            print(f"imported {c['id']}: {c['title']}")
        else:
            state = f"thread {c['thread_id']}" if c["thread_id"] else ("open in a terminal" if c["live"] else "not imported")
            print(f"{c['provider']:6} {c['session_id']}  {Path(c['repo_path']).name:20} {c['title'][:50]:50} {state}")
    if not res:
        print("No importable sessions. Set the folder with --root and the profiles with --claude-dir / --codex-dir.")
    return 0


def cmd_doctor(a: argparse.Namespace) -> int:
    p = paths()
    probs = check_permissions(p)
    print("permissions: " + ("ok" if not probs else "\n  " + "\n  ".join(probs)))
    from .db import Lease
    cur = Lease(p.lease).read()
    print(f"lease: {cur or 'none (Brain not running)'}")
    print(f"site profile: {'present' if p.site_profile.exists() else 'missing — run brain init'}")
    print(f"FCM key: {'present' if p.fcm_key.exists() else 'not configured (pushes go to push-outbox.jsonl)'}")
    print(f"disabled flag: {'SET' if p.disabled_flag.exists() else 'no'}")
    return 1 if probs else 0


def cmd_ir_dump(a: argparse.Namespace) -> int:
    from .audit import IRLog
    from .redact import SecretRegistry
    print(IRLog(paths().ir_dir, SecretRegistry()).dump(a.since_hours * 3600))
    return 0


def cmd_probe(a: argparse.Namespace) -> int:
    from .site_probe import run_probe
    import yaml
    print(yaml.safe_dump(run_probe("external" if a.external_host else None), sort_keys=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="brain", description=f"Cluster Brain {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init", help="set up ~/.brain, probe the site, register repos")
    i.add_argument("--accept-conditions", action="store_true")
    i.add_argument("--fix-perms", action="store_true")
    i.add_argument("--repos", nargs="*", help="repo paths or folders containing repos")
    i.add_argument("--worktree-root", help="where thread worktrees go (default: <repo parent>/.brain-worktrees)")
    i.add_argument("--vault-root", help="experiment vault root (shared filesystem)")
    i.add_argument("--endpoint", help="public endpoint the phone will use (relay URL)")
    i.add_argument("--alt-endpoint", nargs="*", help="failover relay URLs the phone tries when the primary is down")
    i.add_argument("--projects-root", help="where new projects started from the phone are created")
    i.add_argument("--fcm-key", help="path to a Firebase service-account JSON (FCM Admin role only)")
    i.add_argument("--external-host", action="store_true", help="this machine is an always-on host (rung A)")
    i.set_defaults(fn=cmd_init)

    s = sub.add_parser("serve", help="run Brain (Unix socket, optional TCP port for the relay)")
    s.add_argument("--port", type=int, default=0, help="also listen on 127.0.0.1:PORT (for cloudflared/ssh -R)")
    s.add_argument("--tick", type=float, default=10.0)
    s.add_argument("--max-hours", type=float, default=12.0, help="exit cleanly after N hours (0 = never)")
    s.set_defaults(fn=cmd_serve)

    pr = sub.add_parser("pair", help="print a one-time pairing QR/code")
    pr.add_argument("--endpoint")
    pr.set_defaults(fn=cmd_pair)

    sb = sub.add_parser("submit", help="(for agents) submit a SLURM job and register a wake-up")
    sb.add_argument("script")
    sb.add_argument("sbatch_args", nargs=argparse.REMAINDER)
    sb.add_argument("--thread")
    sb.set_defaults(fn=cmd_submit)

    ls = sub.add_parser("listen", help="(for agents) ask the owner for a blind listening test of a listen.json")
    ls.add_argument("manifest")
    ls.add_argument("--thread")
    ls.set_defaults(fn=cmd_listen)

    d = sub.add_parser("devices", help="list, approve or revoke paired devices")
    d.add_argument("action", nargs="?", choices=["list", "approve", "revoke"], default="list")
    d.add_argument("device_id", nargs="?")
    d.set_defaults(fn=cmd_devices)

    ep = sub.add_parser("endpoint", help="record a new relay URL and push it (signed) to paired phones")
    ep.add_argument("action", choices=["set"])
    ep.add_argument("url")
    ep.add_argument("--alt", nargs="*", help="failover relay URLs (replaces the current list)")
    ep.set_defaults(fn=cmd_endpoint)

    im = sub.add_parser("import", help="list or import your terminal Claude/Codex sessions as threads")
    im.add_argument("session", nargs="?", help="session id to import (see --list)")
    im.add_argument("--provider", default="claude", choices=["claude", "codex"])
    im.add_argument("--list", action="store_true")
    im.add_argument("--all", action="store_true", help="import every listed session not imported yet")
    im.add_argument("--root", help="only sessions whose folder is under this (yours)")
    im.add_argument("--claude-dir", action="append", help="a Claude profile folder, e.g. ~/.claude-profiles/you")
    im.add_argument("--codex-dir", action="append", help="a Codex home, e.g. ~/.codex")
    im.add_argument("--days", type=float)
    im.set_defaults(fn=cmd_import)

    sub.add_parser("doctor", help="check permissions, lease, configuration").set_defaults(fn=cmd_doctor)
    ir = sub.add_parser("ir-dump", help="print the incident-response log for an admin")
    ir.add_argument("--since-hours", type=float, default=24)
    ir.set_defaults(fn=cmd_ir_dump)
    pb = sub.add_parser("probe", help="print what the site probe sees (no changes)")
    pb.add_argument("--external-host", action="store_true")
    pb.set_defaults(fn=cmd_probe)

    a = ap.parse_args(argv)
    if a.cmd == "submit" and a.sbatch_args and a.sbatch_args[0] == "--":
        a.sbatch_args = a.sbatch_args[1:]
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
