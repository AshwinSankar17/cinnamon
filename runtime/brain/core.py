"""Brain: threads, turns, approvals, jobs, durable waits, pause — the single durable control plane."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from . import repos, slurm, transcript
from .audit import AuditWriter, IRLog
from .config import SITE_DISABLED_FLAG, Paths, Settings
from .db import DB, row_dict
from .events import EventStore
from .identity import Identity, Pairing
from .policy import PolicyContext, classify
from .providers.base import Provider, TurnContext, TurnResult
from .providers.claude import tool_kind
from .push import PushSender
from .webpush import WebPush
from .redact import SecretRegistry

log = logging.getLogger("brain")

SYSTEM_APPEND = (
    "You are running under Cluster Brain on a shared HPC cluster. The owner is usually away from the "
    "terminal and follows along on a phone. Rules: (1) Never run training, inference, synthesis or large "
    "ffmpeg jobs directly on this node; submit them with `brain submit <script.sh> [sbatch args]`, which "
    "returns a job id. (2) After submitting, do not poll with sleep/squeue loops: end your turn with a short "
    "plan. Brain wakes you with the result when the job finishes. (3) Commit to your branch freely; pushing "
    "to protected branches, deleting vaults or checkpoints, or deleting outside the worktree pauses for the "
    "owner's approval. Do not ask for that approval in chat: just run the command, and Brain pauses it and "
    "asks the owner on their phone. If an action is deferred or denied pending approval, end the turn and say "
    "what you were about to do. (4) To get the owner's ears on audio (TTS samples, A/B of checkpoints or sampler "
    "settings), write a listen.json next to the samples and run `brain listen path/to/listen.json`; the owner "
    "rates them blind on the phone and Brain wakes you with the results. Format: {\"version\": 1, \"title\": str, "
    "\"systems\": [{\"id\", \"label\"}], \"items\": [{\"id\", \"text\", \"samples\": {system_id: path}, "
    "\"reference\": path?, \"prompt\": path?, \"metrics\": {system_id: {name: value}}?}], \"metrics\": "
    "{system_id: {name: value}}?}; paths are relative to listen.json and must be inside the repo or vault."
)


NOISE = re.compile(r"(^|/)(__pycache__|\.pytest_cache|\.mypy_cache|\.ipynb_checkpoints)/|\.py[co]$|(^|/)\.DS_Store$")
PROJECT_GITIGNORE = ("__pycache__/\n*.py[co]\n.pytest_cache/\n.mypy_cache/\n.ipynb_checkpoints/\n.venv/\n*.egg-info/\n"
                     ".DS_Store\nwandb/\n.brain/\n")

RUN_APPEND = (" This thread is a training run. Keep checkpoints, logs, metrics and outputs in its run vault "
              "{vault} ($BRAIN_VAULT_DIR), not in the repo; jobs you submit get run directories there.")

SCRATCH_ROOTS = tuple(dict.fromkeys(x for x in ("/tmp", os.environ.get("TMPDIR", ""), tempfile.gettempdir()) if x))


def now() -> float:
    return time.time()


def _git_exclude(worktree: str, pattern: str) -> None:
    """Keep Brain's own files out of the repo via .git/info/exclude (never touches the repo's .gitignore)."""
    r = subprocess.run(["git", "-C", worktree, "rev-parse", "--path-format=absolute", "--git-path", "info/exclude"],
                       capture_output=True, text=True)
    if r.returncode:
        return
    f = Path(r.stdout.strip())
    if pattern not in (f.read_text().splitlines() if f.exists() else []):
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open("a") as fh:
            fh.write(f"\n{pattern}\n")


def _git_common_dir(path: str | None) -> str | None:
    """The repo's .git (a worktree commits into it), so sandboxed agents can still commit."""
    if not path:
        return None
    r = subprocess.run(["git", "-C", path, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                       capture_output=True, text=True)
    return r.stdout.strip() or None if r.returncode == 0 else None


class Brain:
    def __init__(self, p: Paths, settings: Settings, db: DB, *, providers: dict[str, Provider] | None = None,
                 redactor: SecretRegistry | None = None):
        self.p = p
        self.settings = settings
        self.db = db
        self.redactor = redactor or SecretRegistry.from_environment(extra_files=[p.fcm_key])
        self.audit = AuditWriter(p.audit_dir, self.redactor)
        self.ir = IRLog(p.ir_dir, self.redactor)
        self.events = EventStore(db, self.redactor, self.audit, p.blobs)
        self.ident = Identity(p.identity_key, p.token_secret)
        self.pairing = Pairing(db, self.ident)
        self.push = PushSender(p.fcm_key, p.push_outbox, settings.fcm_project_id)
        self.webpush = WebPush(p.vapid_key, settings.raw.get("vapid_subject", "mailto:brain@cluster-brain.invalid"))
        # Two local credentials. hook.token goes into every agent's environment and only reaches /internal/hook and
        # /internal/submit. admin.token (pairing, devices, endpoints) is never given to agents.
        for f in (p.hook_token, p.admin_token):
            if not f.exists():
                f.write_text(uuid.uuid4().hex + uuid.uuid4().hex)
                os.chmod(f, 0o600)
        self.hook_token = p.hook_token.read_text().strip()
        self.admin_token = p.admin_token.read_text().strip()
        self.redactor.add(self.hook_token)  # `env` in a tool call must not put it on the phone or in the audit log
        self.redactor.add(self.admin_token)
        if providers is None:
            from .providers.agy import AgyProvider
            from .providers.claude import ClaudeProvider
            from .providers.codex import CodexProvider
            from .providers.fake import FakeProvider
            providers = {"claude": ClaudeProvider(), "codex": CodexProvider(), "agy": AgyProvider(),
                         "fake": FakeProvider()}
        self.providers = providers
        self.watcher = slurm.Watcher()
        self._tasks: dict[str, asyncio.Task] = {}
        self._turn_counts: dict[str, dict[str, int]] = {}
        # what arrived for a thread while it could not take it: ("user", text) or ("wake", reason)
        self._queued: dict[str, list[tuple[str, str]]] = {}
        self._elevate: set[str] = set()
        self._stopping: set[str] = set()  # the owner pressed Stop on this thread's running turn
        self._resume_after: dict[str, str | None] = {}  # decisions that arrived while their turn was still running  # threads whose next turn may leave the OS sandbox (an approval was granted)

    # ---- pause / disable -------------------------------------------------------------------------
    @property
    def paused(self) -> bool:
        r = self.db.one("SELECT v FROM meta WHERE k='paused'")
        return bool(r and r["v"] == "1") or self.p.disabled_flag.exists() or SITE_DISABLED_FLAG.exists()

    async def set_paused(self, value: bool) -> bool:
        self.db.x("INSERT INTO meta(k,v) VALUES('paused',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                  ("1" if value else "0",))
        self.ir.log("pause" if value else "resume")
        if value:
            for tid in list(self._tasks):
                await self.stop(tid, reason="Brain paused", keep_queue=True)
        else:
            for tid in list(self._queued):  # what arrived while paused runs now, as promised
                self._drain(tid)
        return self.paused

    # ---- threads ---------------------------------------------------------------------------------
    def thread(self, thread_id: str) -> dict | None:
        return row_dict(self.db.one("SELECT * FROM threads WHERE id=?", (thread_id,)))

    def thread_view(self, t: dict) -> dict:
        last = self.db.one("SELECT * FROM events WHERE thread_id=? AND visibility='user' ORDER BY seq DESC LIMIT 1",
                           (t["id"],))
        out = {k: t[k] for k in ("id", "repo_id", "title", "mode", "branch", "provider", "state", "parent_id",
                                 "fork_turn", "last_seq", "updated_at", "vault_dir")}
        out.update(pinned=bool(t["pinned"]), archived=bool(t["archived"]), muted=bool(t["muted"]),
                   last_event=EventStore._row_to_event(last) if last else None)
        return out

    def list_threads(self, repo_id: str | None = None, state: str | None = None, archived: bool = False) -> list[dict]:
        sql = "SELECT * FROM threads WHERE archived=?"
        args: list[Any] = [1 if archived else 0]
        if repo_id:
            sql += " AND repo_id=?"
            args.append(repo_id)
        if state:
            sql += " AND state=?"
            args.append(state)
        sql += " ORDER BY pinned DESC, updated_at DESC"
        return [self.thread_view(dict(r)) for r in self.db.q(sql, args)]

    def _set_state(self, thread_id: str, state: str) -> None:
        self.db.x("UPDATE threads SET state=?, updated_at=? WHERE id=?", (state, now(), thread_id))

    def create_thread(self, repo_id: str, title: str = "", mode: str = "readonly", provider: str | None = None,
                      base_ref: str | None = None, parent_id: str | None = None, fork_turn: int | None = None,
                      run: bool = False) -> dict:
        """run=True: a training run off this repo. It gets a worktree and its own vault folder
        (<repo vault>/<slug>-<thread8>) for checkpoints, logs and outputs; Brain's jobs write their run dirs there."""
        repo = self.settings.repo(repo_id)
        if not repo:
            raise KeyError(f"unknown repo {repo_id}")
        provider = provider or self.settings.default_provider
        if provider not in self.providers:
            raise KeyError(f"provider {provider} is not configured")
        if run and not repo.vault_root:
            raise ValueError(f"repo {repo_id} has no vault root; set one with `brain init --vault-root`")
        tid = f"thr_{uuid.uuid4().hex[:16]}"
        title = title or ("New run" if run else "New thread")
        mode = "worktree" if run else mode
        worktree, branch, vault_dir = repo.path, None, None
        if mode == "worktree":
            root = repo.worktree_root or str(Path(repo.path).parent / ".brain-worktrees")
            wt, branch = repos.create_worktree(repo.path, root, tid, title, base_ref or repo.default_branch)
            worktree = str(wt)
        if run:
            vd = Path(repo.vault_root) / f"{repos.slugify(title)}-{tid[-8:]}"
            vd.mkdir(parents=True, exist_ok=False)
            vault_dir = str(vd)
        self.db.x(
            "INSERT INTO threads(id, repo_id, title, mode, branch, worktree, provider, state, parent_id, fork_turn,"
            " created_at, updated_at, vault_dir) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, repo_id, title, mode, branch, worktree, provider, "idle", parent_id, fork_turn, now(), now(), vault_dir),
        )
        where = "read-only" if mode == "readonly" else branch
        self.events.append(tid, "system.notice", {"text": f"Thread created on {repo.name or repo.id} ({where})."
                                                  + (f" Run vault: {vault_dir}" if vault_dir else "")})
        return self.thread_view(self.thread(tid))

    def create_project(self, name: str, idea: str, provider: str | None = None) -> dict:
        """A new idea from scratch: a fresh git repo under `projects_root`, its own vault under `vault_root`,
        registered with Brain, plus a worktree thread whose first message is the idea."""
        projects, vaults = self.settings.raw.get("projects_root"), self.settings.raw.get("vault_root")
        if not projects or not vaults:
            raise ValueError("set projects_root and vault_root first: `brain init --projects-root DIR --vault-root DIR`")
        slug = repos.slugify(name, 40)
        if not slug:
            raise ValueError("project name needs letters or digits")
        if self.settings.repo(slug):
            raise FileExistsError(f"a repo named {slug} is already registered")
        path, vault = Path(projects).expanduser() / slug, Path(vaults).expanduser() / slug
        if path.exists():
            raise FileExistsError(f"{path} already exists")
        path.mkdir(parents=True)
        vault.mkdir(parents=True, exist_ok=True)
        (path / "README.md").write_text(f"# {name}\n\n{idea.strip()}\n")
        (path / ".gitignore").write_text(PROJECT_GITIGNORE)
        repos.git(path, "init", "-q", "-b", "main")
        repos.git(path, "add", "README.md", ".gitignore")
        repos.git(path, "-c", "user.name=Cluster Brain", "-c", "user.email=brain@localhost",
                  "commit", "-qm", "Start project: " + name)
        from .config import RepoConfig
        self.settings.repos.append(RepoConfig(id=slug, path=str(path), name=name,
                                              worktree_root=str(path.parent / ".brain-worktrees"), vault_root=str(vault)))
        self.settings.save(self.p)
        self.ir.log("project.create", repo=slug, path=str(path), vault=str(vault))
        t = self.create_thread(slug, name, "worktree", provider)
        return {"repo": self.repo_view(self.settings.repo(slug)), "thread": t}

    @staticmethod
    def repo_view(r) -> dict:
        return {"id": r.id, "name": r.name or r.id, "path": r.path, "default_branch": r.default_branch,
                "profile": r.profile, "archive_policy": r.archive_policy, "vault_root": r.vault_root}

    def patch_thread(self, thread_id: str, **fields: Any) -> dict:
        allowed = {k: v for k, v in fields.items() if k in ("title", "pinned", "archived", "muted") and v is not None}
        for k, v in allowed.items():
            self.db.x(f"UPDATE threads SET {k}=?, updated_at=? WHERE id=?",
                      (int(v) if isinstance(v, bool) else v, now(), thread_id))
        return self.thread_view(self.thread(thread_id))

    def fork_thread(self, thread_id: str, title: str, mode: str = "worktree", at_seq: int | None = None) -> dict:
        t = self.thread(thread_id)
        if not t:
            raise KeyError(thread_id)
        new = self.create_thread(t["repo_id"], title, mode, t["provider"], base_ref=t["branch"] or None,
                                 parent_id=thread_id, fork_turn=at_seq)
        upto = at_seq or t["last_seq"]
        self.events.append(new["id"], "system.notice",
                           {"text": f"Forked from {t['title']} at event {upto}. Earlier history is available "
                                    f"in the parent thread."})
        self.events.append(thread_id, "system.notice", {"text": f"Forked → {title}"})
        return self.thread_view(self.thread(new["id"]))

    def switch_provider(self, thread_id: str, provider: str) -> dict:
        """Move the thread to another provider. Its next turn first brings that provider's native session up to
        date with every transcript item it has not seen (native append, or verbatim replay for agy)."""
        t = self.thread(thread_id)
        if not t:
            raise KeyError(thread_id)
        if provider not in self.providers:
            raise KeyError(f"provider {provider} is not configured")
        if thread_id in self._tasks and not self._tasks[thread_id].done():
            raise RuntimeError("a turn is running; stop it or wait for it to end before switching")
        if self.db.one("SELECT 1 FROM approvals WHERE thread_id=? AND state='pending'", (thread_id,)):
            raise RuntimeError("decide the pending approval first; it belongs to the current provider")
        if t["provider"] != provider:
            self.db.x("UPDATE threads SET provider=?, updated_at=? WHERE id=?", (provider, now(), thread_id))
            n = self.db.one("SELECT COUNT(*) AS n FROM transcript_items WHERE thread_id=?", (thread_id,))["n"]
            self.events.append(thread_id, "provider.switched", {"from": t["provider"], "to": provider, "history_items": n})
            self.ir.log("provider.switch", thread=thread_id, frm=t["provider"], to=provider)
        return self.thread_view(self.thread(thread_id))

    def save_attachment(self, thread_id: str, filename: str, data: bytes, mime: str = "") -> dict:
        """A file from the phone, saved in the thread's own scope (<worktree>/.brain/uploads, excluded from git)
        so the agent can read it without leaving its repo."""
        t = self.thread(thread_id)
        if not t or not t["worktree"]:
            raise ValueError("thread has no workspace")
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).name).strip("._") or "file"
        if not data:
            raise ValueError("empty file")
        d = Path(t["worktree"]) / ".brain" / "uploads"
        d.mkdir(parents=True, exist_ok=True)
        _git_exclude(t["worktree"], "/.brain/")
        path = d / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}-{name[:80]}"
        path.write_bytes(data)
        self.ir.log("upload", thread=thread_id, path=str(path), bytes=len(data))
        return {"path": str(path), "rel": str(path.relative_to(t["worktree"])), "name": name, "size": len(data),
                "mime": mime.split(";")[0].strip()}

    # ---- transcript ------------------------------------------------------------------------------
    def transcript(self, thread_id: str, start: int = 0) -> list[dict]:
        return [json.loads(r["data"]) for r in
                self.db.q("SELECT data FROM transcript_items WHERE thread_id=? AND idx>=? ORDER BY idx", (thread_id, start))]

    def _append_transcript(self, thread_id: str, provider: str, turn_id: str | None, items: list[dict]) -> None:
        if not items:
            return
        with self.db.tx() as c:
            nxt = c.execute("SELECT COALESCE(MAX(idx)+1, 0) FROM transcript_items WHERE thread_id=?", (thread_id,)).fetchone()[0]
            c.executemany("INSERT INTO transcript_items(thread_id, idx, provider, turn_id, kind, data, ts) VALUES (?,?,?,?,?,?,?)",
                          [(thread_id, nxt + i, provider, turn_id, it["k"], json.dumps(it), now()) for i, it in enumerate(items)])

    @staticmethod
    async def _native(prov: Provider, ctx: TurnContext, sid: str) -> list[dict] | None:
        """Non-user items of the provider's native session (Brain records user prompts itself)."""
        reader = getattr(prov, "native_items", None)
        items = await asyncio.to_thread(reader, ctx, sid) if reader else None
        return None if items is None else [i for i in items if i["k"] != "user"]

    def _save_session(self, thread_id: str, provider: str, sid: str, native_seen: int | None) -> None:
        canon = self.db.one("SELECT COUNT(*) AS n FROM transcript_items WHERE thread_id=?", (thread_id,))["n"]
        if native_seen is None:  # could not read the native session: keep the count only if it is the same session
            row = self.db.one("SELECT * FROM provider_sessions WHERE thread_id=? AND provider=?", (thread_id, provider))
            native_seen = row["native_seen"] if row and row["native_session_id"] == sid else 0
        self.db.x("INSERT INTO provider_sessions(thread_id, provider, native_session_id, sync_seq, updated_at, native_seen,"
                  " canon_synced) VALUES (?,?,?,?,?,?,?) ON CONFLICT(thread_id, provider) DO UPDATE SET"
                  " native_session_id=excluded.native_session_id, sync_seq=excluded.sync_seq, updated_at=excluded.updated_at,"
                  " native_seen=excluded.native_seen, canon_synced=excluded.canon_synced",
                  (thread_id, provider, sid, self.events.last_seq(thread_id), now(), native_seen, canon))

    async def _catch_up(self, thread_id: str, prov: Provider, ctx: TurnContext, missing: list[dict],
                        emit: Any) -> None:
        """Give the provider the transcript items its native session lacks: hydrate natively, else replay."""
        hydrate = getattr(prov, "hydrate", None)
        if hydrate:
            try:
                sid = await hydrate(ctx, missing)
                native = await self._native(prov, ctx, sid)
                self._save_session(thread_id, prov.name, sid, len(native) if native is not None else None)
                ctx.resume_session = sid
                emit("system.notice", {"text": f"{prov.name} now holds the thread's full history "
                                                f"(+{len(missing)} items from other providers)."})
                return
            except Exception as e:  # never lose the turn: fall back to a verbatim replay
                log.exception("hydrate failed")
                emit("provider.status", {"provider": prov.name, "status": "error",
                                         "message": f"native handoff failed ({e}); replaying history in the prompt"})
        ctx.prompt = transcript.replay_prompt(missing, ctx.prompt)
        emit("system.notice", {"text": f"Replaying {len(missing)} history items verbatim to {prov.name}."})

    # ---- turns -----------------------------------------------------------------------------------
    def provider_ctx(self, t: dict, prompt: str | None = None, emit: Any = None, resume: str | None = None) -> TurnContext:
        repo = self.settings.repo(t["repo_id"])
        thread_id = t["id"]
        env = {
            "BRAIN_HOOK_SOCK": str(self.p.sock), "BRAIN_HOOK_TOKEN": self.hook_token, "BRAIN_THREAD_ID": thread_id,
            "BRAIN_PROVIDER": t["provider"], "BRAIN_PROFILE": repo.profile if repo else "auto",
            "BRAIN_VAULT_DIR": t.get("vault_dir") or (repo.vault_root if repo else "") or "",
            "CLAUDE_CONFIG_DIR": str(self.p.claude_config_dir), "CODEX_HOME": str(self.p.codex_home),
            "GIT_AUTHOR_NAME": "Cluster Brain agent", "GIT_COMMITTER_NAME": "Cluster Brain agent",
        }

        async def submit(script_text: str) -> str:
            path = Path(t["worktree"] or ".") / ".brain-submit.sh"
            path.write_text(script_text)
            return self.submit_job(thread_id, path, [])["job_id"]

        raw = self.settings.raw
        vaults = [repo.vault_root] if repo and repo.vault_root else []
        extra = {"submit": submit, "agy_app_data_dir": str(self.p.root / "agy"),
                 # OS-sandbox write scope for CLIs that have one (Codex; agy's workspace): worktree + these
                 "scope_roots": [x for x in (*vaults, _git_common_dir(t["worktree"]), *SCRATCH_ROOTS) if x],
                 "vault_roots": vaults, "elevated": thread_id in self._elevate,
                 **{k: raw[k] for k in ("codex_sandbox", "agy_permissions", "agy_app_data_dir",
                                        "agy_allow", "agy_print_timeout") if k in raw}}
        return TurnContext(thread_id=thread_id, cwd=Path(t["worktree"] or "."), prompt=prompt, resume_session=resume,
                           env=env, emit=emit or (lambda *_: None),
                           pretool=lambda tool, inp, tuid: self.pretool(thread_id, tool, inp, tuid),
                           system_append=SYSTEM_APPEND + (RUN_APPEND.format(vault=t["vault_dir"]) if t.get("vault_dir") else ""),
                           permission_mode=raw.get("claude_permission_mode", "auto"),
                           readonly=t["mode"] == "readonly", extra=extra)

    def policy_ctx(self, t: dict) -> PolicyContext:
        repo = self.settings.repo(t["repo_id"])
        job_ids = frozenset(r["job_id"] for r in self.db.q("SELECT job_id FROM jobs"))
        return PolicyContext(profile=repo.profile if repo else "auto", worktree=t["worktree"],
                             vault_roots=tuple(x for x in [repo.vault_root if repo else ""] if x),
                             protected_branches=tuple(repo.protected_branches if repo else ("main", "master")),
                             brain_job_ids=job_ids, scratch_roots=SCRATCH_ROOTS, run_vault=t.get("vault_dir"),
                             brain_root=str(self.p.root))

    async def send_message(self, thread_id: str, text: str, actor: dict | None = None) -> int:
        t = self.thread(thread_id)
        if not t:
            raise KeyError(thread_id)
        if t["archived"]:  # talking to an archived thread brings it back
            self.db.x("UPDATE threads SET archived=0, updated_at=? WHERE id=?", (now(), thread_id))
        ev = self.events.append(thread_id, "user.message", {"text": text}, actor=actor or {"kind": "user"})
        if self.paused:
            self.events.append(thread_id, "system.notice", {"text": "Brain is paused; this message will run on resume."})
            self._queued.setdefault(thread_id, []).append(("user", text))
            return ev["seq"]
        if thread_id in self._tasks and not self._tasks[thread_id].done():
            # mirror the model's own behaviour for a message typed mid-turn: steer the running turn where the
            # provider does (Claude Code reads it at its next step), else queue it for right after this turn
            steer = getattr(self.providers.get(t["provider"]), "steer", None)
            if steer and await steer(thread_id, text):
                # ponytail: recorded now, so in the shared history it sits before this turn's replies, not between them
                self._append_transcript(thread_id, "user", None, [{"k": "user", "text": text}])
                self.events.append(thread_id, "system.notice", {"text": "Sent into the running turn."})
                return ev["seq"]
            self._queued.setdefault(thread_id, []).append(("user", text))
            self.events.append(thread_id, "system.notice", {"text": "Queued: runs as soon as this turn ends."})
            return ev["seq"]
        self._queued.setdefault(thread_id, []).append(("user", text))
        self._drain(thread_id)  # with anything held back, e.g. a job that finished while the thread was stopped
        return ev["seq"]

    def _start(self, thread_id: str, prompt: str | None, wake_reason: str | None = None,
               retry_of: str | None = None) -> None:
        task = asyncio.create_task(self._run_turn(thread_id, prompt, wake_reason, retry_of))
        self._tasks[thread_id] = task

    def _drain(self, thread_id: str) -> None:
        """Run what queued up for an idle thread as one turn (nothing is ever dropped while Brain is paused)."""
        if self.paused or (thread_id in self._tasks and not self._tasks[thread_id].done()):
            return
        queued = self._queued.pop(thread_id, [])
        if queued:
            woken = all(k == "wake" for k, _ in queued)
            self._start(thread_id, "\n\n".join(text for _, text in queued),
                        wake_reason="wait fired" if woken else None)

    async def stop(self, thread_id: str, reason: str = "Stopped from the phone", keep_queue: bool = False) -> list[str]:
        """Stop the running turn. Like Esc in Claude Code, messages queued behind it are not sent: they are
        returned (the phone puts them back in the composer). keep_queue (pausing) keeps them for later."""
        t = self.thread(thread_id)
        task = self._tasks.get(thread_id)
        held = self._queued.pop(thread_id, [])  # taken first, so the stopped turn's end does not run them
        if t and task and not task.done():
            self._stopping.add(thread_id)
            prov = self.providers.get(t["provider"])
            if prov:
                await prov.interrupt(thread_id)  # the model's own stop; it force-kills only if that is ignored
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=10)
            except asyncio.TimeoutError:
                task.cancel()
            self._stopping.discard(thread_id)
            self.events.append(thread_id, "system.notice", {"text": reason})
        if keep_queue:
            self._queued[thread_id] = held + self._queued.get(thread_id, [])
            return []
        wakes = [q for q in held if q[0] == "wake"]  # e.g. "job finished": kept, delivered with the next message
        if wakes:
            self._queued[thread_id] = wakes + self._queued.get(thread_id, [])
        unsent = [text for kind, text in held if kind == "user"]
        if unsent:
            self.events.append(thread_id, "system.notice", {"text": f"Not sent (stopped): {len(unsent)} queued "
                                                            f"message{'s' if len(unsent) > 1 else ''}, back in your composer."})
        return unsent

    def retry_turn(self, thread_id: str) -> None:
        """Run the thread's last turn again, with the same prompt, when it failed or was stopped."""
        t = self.thread(thread_id)
        if not t:
            raise KeyError(thread_id)
        if thread_id in self._tasks and not self._tasks[thread_id].done():
            raise RuntimeError("a turn is running; stop it first")
        last = self.db.one("SELECT * FROM turns WHERE thread_id=? ORDER BY started_seq DESC LIMIT 1", (thread_id,))
        if not last or last["ended_at"] is None or last["stop_reason"] not in ("error", "cancelled"):
            raise RuntimeError("the last turn did not fail or stop; send a new message instead")
        # a retry replaces any automatic resume the failure scheduled (e.g. after a usage limit)
        self.db.x("UPDATE waits SET state='cancelled' WHERE thread_id=? AND state='open' AND kind='provider_quota_reset'",
                  (thread_id,))
        if t["archived"]:
            self.db.x("UPDATE threads SET archived=0, updated_at=? WHERE id=?", (now(), thread_id))
        prompt = last["prompt"]
        if prompt is None and last["wake_reason"]:  # a woken turn (approval decided, job done) has no saved prompt
            prompt = "Your previous turn failed or was stopped. Pick up where you left off."
        self._start(thread_id, prompt, last["wake_reason"], retry_of=last["turn_id"])

    async def pretool(self, thread_id: str, tool_name: str, tool_input: dict, tool_use_id: str | None) -> dict:
        """The single decision path for every tool call (called by brain-hook and in-process providers)."""
        t = self.thread(thread_id)
        if not t:
            return {"decision": "none"}
        if self.paused:
            return {"decision": "deny", "reason": "Brain is paused by the owner. Stop and end your turn."}
        if t["mode"] == "readonly" and tool_name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            return {"decision": "deny", "reason": "This thread is read-only. Describe the change instead."}
        d = classify(tool_name, tool_input, self.policy_ctx(t))
        if tool_name == "Bash":
            self.ir.log("exec", thread=thread_id, verdict=d.verdict, cmd=str(tool_input.get("command", ""))[:300])
        if d.verdict == "deny":
            return {"decision": "deny", "reason": d.reason}
        if d.verdict == "allow":
            return {"decision": "none", "route": d.route}
        # always-ask: find an existing approval for this exact call, else create one and defer
        key = hashlib.sha256(json.dumps([tool_name, tool_input], sort_keys=True).encode()).hexdigest()
        row = self.db.one(
            "SELECT * FROM approvals WHERE thread_id=? AND (tool_use_id=? OR tool_input=?) ORDER BY created_at DESC LIMIT 1",
            (thread_id, tool_use_id or "-", key))
        if row and row["state"] == "allowed":
            return {"decision": "allow", "reason": "Approved by the owner on their phone."}
        if row and row["state"] == "denied":
            note = f" Owner says: {row['note']}" if row["note"] else ""
            return {"decision": "deny", "reason": f"The owner denied this.{note} Do not retry; continue or end the turn."}
        if row and row["state"] == "pending":
            return {"decision": "defer", "reason": "Still waiting for the owner's approval."}
        aid = f"apr_{uuid.uuid4().hex[:12]}"
        self.db.x(
            "INSERT INTO approvals(id, thread_id, tool_use_id, action_type, summary, decision_summary, tool_input, state,"
            " created_at, raw_input) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (aid, thread_id, tool_use_id, d.action_type, d.summary, json.dumps(d.decision_summary), key, "pending", now(),
             json.dumps({"tool_name": tool_name, "tool_input": tool_input})))
        self.db.x("INSERT INTO waits(id, thread_id, kind, predicate, created_at) VALUES (?,?,?,?,?)",
                  (f"wt_{uuid.uuid4().hex[:12]}", thread_id, "approval", json.dumps({"approval_id": aid}), now()))
        self.events.append(thread_id, "approval.requested",
                           {"approval_id": aid, "action_type": d.action_type, "summary": d.summary,
                            "decision_summary": d.decision_summary})
        self._notify(thread_id, "approval", f"{d.decision_summary.get('verb', 'Approval needed')}",
                     f"{t['title']} · {d.decision_summary.get('target', '')} · pending until you decide",
                     {"thread_id": thread_id, "approval_id": aid})
        return {"decision": "defer", "reason": "Waiting for the owner's approval on their phone. End your turn."}

    async def _run_turn(self, thread_id: str, prompt: str | None, wake_reason: str | None = None,
                        retry_of: str | None = None) -> None:
        t = self.thread(thread_id)
        prov = self.providers[t["provider"]]
        turn_id = f"turn_{uuid.uuid4().hex[:12]}"
        counts = {"reads": 0, "edits": 0, "commands": 0, "failed": 0}
        started = now()
        self._set_state(thread_id, "running")
        start_ev = self.events.append(thread_id, "turn.started", {"turn_id": turn_id, "provider": t["provider"],
                                                                   "wake_reason": wake_reason, "retry_of": retry_of},
                                      turn_id=turn_id)
        self.db.x("INSERT INTO turns(turn_id, thread_id, provider, started_seq, started_at, prompt, wake_reason)"
                  " VALUES (?,?,?,?,?,?,?)", (turn_id, thread_id, t["provider"], start_ev["seq"], started, prompt, wake_reason))
        pre_ref = None
        if t["mode"] == "worktree" and t["worktree"]:
            try:
                pre_ref = f"refs/brain/{thread_id}/{start_ev['seq']}-pre"
                repos.snapshot(t["worktree"], pre_ref, f"before {turn_id}")
            except repos.GitError as e:
                log.warning("snapshot failed: %s", e)
                pre_ref = None

        def emit(type_: str, payload: dict) -> None:
            if type_ == "tool.started":
                k = tool_kind(payload.get("tool", ""))
                if k in counts:
                    counts[k] += 1
            if type_ == "tool.completed" and not payload.get("ok", True):
                counts["failed"] += 1
            self.events.append(thread_id, type_, payload, turn_id=turn_id,
                               actor={"kind": "agent", "provider": t["provider"]})

        sess = self.db.one("SELECT * FROM provider_sessions WHERE thread_id=? AND provider=?", (thread_id, t["provider"]))
        ctx = self.provider_ctx(t, prompt, emit, sess["native_session_id"] if sess else None)
        self._elevate.discard(thread_id)  # one turn only
        missing = self.transcript(thread_id, sess["canon_synced"] if sess else 0)  # history this provider lacks
        if retry_of and missing and missing[-1] == {"k": "user", "text": prompt}:
            missing = missing[:-1]  # the failed turn's prompt: it is sent again below, not replayed
        if prompt is not None and not retry_of:  # a retry's prompt is already in the transcript
            self._append_transcript(thread_id, "user", turn_id, [{"k": "user", "text": prompt}])
        self.ir.log("provider.start", thread=thread_id, provider=t["provider"], turn=turn_id)
        try:
            if missing:
                await self._catch_up(thread_id, prov, ctx, missing, emit)
            res = await prov.run_turn(ctx)
        except asyncio.CancelledError:
            res = TurnResult(stop_reason="cancelled")
        except Exception as e:  # provider crash must never corrupt the thread
            log.exception("provider failed")
            res = TurnResult(error=f"{type(e).__name__}: {e}")
        if thread_id in self._stopping:  # the owner stopped it: an interrupted exit is not a failure
            self._stopping.discard(thread_id)
            res.stop_reason, res.error, res.rate_limited_until = "cancelled", None, None
        elif res.error and res.stop_reason in (None, "end_turn"):
            res.stop_reason = "error"
        self.ir.log("provider.end", thread=thread_id, provider=t["provider"], turn=turn_id,
                    stop=res.stop_reason, error=(res.error or "")[:120] or None)

        sid = res.session_id or ctx.resume_session
        if sid:  # ingest what this turn added to the native session into the provider-neutral transcript
            native_seen = None
            try:
                native = await self._native(prov, ctx, sid)
                if native is not None:
                    row = self.db.one("SELECT * FROM provider_sessions WHERE thread_id=? AND provider=?",
                                      (thread_id, t["provider"]))
                    seen = row["native_seen"] if row and row["native_session_id"] == sid else 0
                    self._append_transcript(thread_id, t["provider"], turn_id, native[seen:])
                    native_seen = len(native)
            except Exception:  # a transcript problem must not fail the turn; the native session still has it
                log.exception("transcript ingest failed")
            self._save_session(thread_id, t["provider"], sid, native_seen)
        diffstat = {"files": 0, "added": 0, "removed": 0}
        if pre_ref:
            try:
                post_ref = f"refs/brain/{thread_id}/{start_ev['seq']}-post"
                repos.snapshot(t["worktree"], post_ref, f"after {turn_id}")
                for fc in repos.diff_between(t["worktree"], pre_ref, post_ref):
                    if NOISE.search(fc.path):  # compiled caches and editor droppings: not a change worth showing
                        continue
                    diffstat["files"] += 1
                    diffstat["added"] += fc.added
                    diffstat["removed"] += fc.removed
                    self.events.append(thread_id, "file.diff", {"path": fc.path, "added": fc.added,
                                                                "removed": fc.removed, "diff": fc.diff}, turn_id=turn_id)
            except repos.GitError as e:
                log.warning("post snapshot failed: %s", e)
        if res.error:
            self.events.append(thread_id, "provider.status", {"provider": t["provider"], "status": "error",
                                                              "message": res.error[-500:]}, turn_id=turn_id)
        if res.rate_limited_until:
            self.db.x("INSERT INTO waits(id, thread_id, kind, predicate, created_at, next_check_at) VALUES (?,?,?,?,?,?)",
                      (f"wt_{uuid.uuid4().hex[:12]}", thread_id, "provider_quota_reset",
                       json.dumps({"resets_at": res.rate_limited_until, "prompt": prompt}), now(), res.rate_limited_until))
            self._notify(thread_id, "needs_you", f"{t['provider'].title()} paused: usage window used up",
                         f"{t['title']} · resumes {time.strftime('%H:%M', time.localtime(res.rate_limited_until))}",
                         {"thread_id": thread_id})
        dur = int((now() - started) * 1000)
        self.events.append(thread_id, "turn.ended", {
            "turn_id": turn_id, "text": (res.text or "")[:2000], "counts": counts, "diffstat": diffstat,
            "duration_ms": dur, "usage": res.usage, "stop_reason": res.stop_reason}, turn_id=turn_id)
        end_seq = self.events.last_seq(thread_id)
        self.db.x("UPDATE turns SET ended_seq=?, ended_at=?, reads=?, edits=?, commands=?, failed=?, input_tokens=?,"
                  " output_tokens=?, stop_reason=?, text=? WHERE turn_id=?",
                  (end_seq, now(), counts["reads"], counts["edits"], counts["commands"], counts["failed"],
                   res.usage.get("input_tokens", 0), res.usage.get("output_tokens", 0), res.stop_reason,
                   (res.text or "")[:2000], turn_id))
        self._ledger("turns", 1, thread_id)
        self._ledger("output_tokens", res.usage.get("output_tokens", 0), thread_id)
        self._set_state(thread_id, self._resting_state(thread_id))
        first_line = next((ln.strip() for ln in (res.text or "").splitlines() if ln.strip()), "Done.")[:200]
        if res.stop_reason == "error":  # the owner hears about failures, including a provider that will not start
            self._notify(thread_id, "failed", f"{t['title']}: turn failed", (res.error or "")[-200:],
                         {"thread_id": thread_id, "turn_id": turn_id})
        elif res.stop_reason != "cancelled" and wake_reason == "wait fired":  # a job came back: say what the agent made of it
            j = self.db.one("SELECT label, name FROM jobs WHERE thread_id=? ORDER BY updated_at DESC LIMIT 1", (thread_id,))
            what = (j["label"] or j["name"]) if j else "job"
            self._notify(thread_id, "done", f"{t['title']}: {what} finished", first_line, {"thread_id": thread_id})
        else:
            done_after = float(self.settings.raw.get("notify_done_after_s", 600))  # owner-started turns: long ones only
            if dur > done_after * 1000 and not res.error and self._resting_state(thread_id) == "idle":
                self._notify(thread_id, "done", f"{t['title']}: turn finished", first_line, {"thread_id": thread_id})
        self._tasks.pop(thread_id, None)
        if thread_id in self._resume_after and not self.paused and self._resting_state(thread_id) == "idle":
            resume = self._resume_after.pop(thread_id)
            if resume is None and res.stop_reason != "tool_deferred":
                # e.g. the deferred call was inside a subagent and the main turn moved on without it
                self.events.append(thread_id, "system.notice", {"text": "The approved action was not retried: the "
                                                                "turn finished without it. Ask again if it is still needed."})
            else:
                self._start(thread_id, resume, wake_reason="approval decided during the turn")
                return  # queued messages run after the resumed turn
        # a message sent mid-turn runs next even if the thread now waits on a job or an approval: the owner asked
        self._drain(thread_id)

    def _resting_state(self, thread_id: str) -> str:
        if self.db.one("SELECT 1 FROM approvals WHERE thread_id=? AND state='pending'", (thread_id,)):
            return "needs_you"
        if self.db.one("SELECT 1 FROM waits WHERE thread_id=? AND state='open'", (thread_id,)):
            return "waiting"
        return "paused" if self.paused else "idle"

    # ---- listening tests & files -----------------------------------------------------------------
    def scope_roots(self, t: dict) -> list[Path]:
        repo = self.settings.repo(t["repo_id"])
        return [Path(x).resolve() for x in (t["worktree"], t.get("vault_dir"), repo.vault_root if repo else None) if x]

    def register_listening(self, thread_id: str, manifest: str) -> dict:
        from . import listening
        t = self.thread(thread_id)
        if not t:
            raise KeyError(thread_id)
        path = (Path(t["worktree"] or ".") / manifest).resolve()
        test = listening.load(path, self.scope_roots(t))  # raises ListenError with a readable reason
        lid = f"lst_{uuid.uuid4().hex[:12]}"
        self.db.x("INSERT INTO listening_tests(id, thread_id, path, title, n_items, n_systems, created_at)"
                  " VALUES (?,?,?,?,?,?,?)", (lid, thread_id, str(path), test["title"], len(test["items"]),
                                              len(test["systems"]), now()))
        self.events.append(thread_id, "listen.ready", {"listen_id": lid, "title": test["title"],
                                                       "items": len(test["items"]), "systems": len(test["systems"])})
        self._notify(thread_id, "needs_you", f"Ready to listen: {test['title']}",
                     f"{t['title']} · {len(test['items'])} sentences × {len(test['systems'])} systems",
                     {"thread_id": thread_id, "listen_id": lid})
        self.ir.log("listen.ready", thread=thread_id, id=lid, path=str(path))
        return {"listen_id": lid, "title": test["title"], "items": len(test["items"]), "systems": len(test["systems"])}

    def listening_row(self, lid: str) -> dict | None:
        return row_dict(self.db.one("SELECT * FROM listening_tests WHERE id=?", (lid,)))

    def listening(self, lid: str) -> dict:
        from . import listening
        r = self.listening_row(lid)
        if not r:
            raise KeyError(lid)
        t = self.thread(r["thread_id"])
        test = listening.load(Path(r["path"]), self.scope_roots(t))
        res = Path(r["path"]).with_name("listen_results.json")
        saved = json.loads(res.read_text()) if res.exists() else None
        return {**test, "id": lid, "thread_id": r["thread_id"], "state": r["state"], "created_at": r["created_at"],
                "ratings": (saved or {}).get("ratings"), "summary": (saved or {}).get("summary")}

    def listening_file(self, lid: str, rel: str) -> Path:
        from . import listening
        r = self.listening_row(lid)
        if not r:
            raise KeyError(lid)
        return listening.resolve_file(Path(r["path"]).parent, rel, self.scope_roots(self.thread(r["thread_id"])),
                                      audio_only=True)

    async def rate_listening(self, lid: str, ratings: dict, device_id: str | None) -> dict:
        from . import listening
        test = self.listening(lid)
        summary = listening.summarize(test, ratings)
        res = Path(self.listening_row(lid)["path"]).with_name("listen_results.json")
        tmp = res.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "listen_id": lid, "title": test["title"], "rated_at": now(),
                                   "rated_by": device_id, "blind": True, "summary": summary, "ratings": ratings}, indent=2))
        os.replace(tmp, res)
        self.db.x("UPDATE listening_tests SET state='rated', summary=?, rated_at=? WHERE id=?",
                  (summary["text"], now(), lid))
        tid = test["thread_id"]
        self.events.append(tid, "listen.rated", {"listen_id": lid, "title": test["title"], "summary": summary["text"],
                                                 "rated_items": summary["rated_items"]},
                           actor={"kind": "user", "device_id": device_id})
        notes = ("\nOwner notes:\n" + "\n".join(f"- {n}" for n in summary["notes"])) if summary["notes"] else ""
        await self.wake(tid, f"[Brain] The owner finished the blind listening test \"{test['title']}\" "
                             f"({summary['rated_items']}/{summary['total_items']} sentences rated).\n{summary['text']}{notes}\n"
                             f"Full ratings: {res}. Use them to decide the next step.")
        return summary

    def list_files(self, t: dict, root: str, rel: str) -> dict:
        roots = {"worktree": t["worktree"], "run": t.get("vault_dir"),
                 "vault": (self.settings.repo(t["repo_id"]).vault_root if self.settings.repo(t["repo_id"]) else None)}
        base = roots.get(root)
        if not base:
            raise KeyError(f"this thread has no {root} folder")
        b = Path(base).resolve()
        d = (b / rel).resolve()
        if not (d == b or b in d.parents) or not d.is_dir():
            raise KeyError("no such folder")
        entries = []
        for e in sorted(d.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:1000]:
            if e.name in (".git",):
                continue
            try:
                st = e.stat()
            except OSError:
                continue
            entries.append({"name": e.name, "dir": e.is_dir(), "size": st.st_size, "mtime": st.st_mtime})
        return {"root": root, "path": str(d.relative_to(b)) if d != b else "", "abs": str(d), "entries": entries,
                "roots": [k for k, v in roots.items() if v]}

    # ---- device approvals ------------------------------------------------------------------------
    DEVICES = "brain:devices"  # pseudo thread id for approvals that belong to Brain itself

    def request_device_approval(self, device_id: str, name: str) -> str:
        """A newly paired device waits until an existing (approved) device allows it."""
        d = self.db.one("SELECT * FROM devices WHERE id=?", (device_id,))
        fp = hashlib.sha256(d["public_key"].encode()).hexdigest()[:16]
        fp = ":".join(fp[i:i + 2] for i in range(0, 16, 2))
        aid = f"apr_{uuid.uuid4().hex[:12]}"
        summary = f"Pair new device \"{name}\" (key {fp})"
        ds = {"verb": "let a new device control Brain", "target": f"{name} · key {fp}",
              "blast_radius": "full control of Brain and its agents from that device",
              "reversible": "yes: revoke it any time (brain devices revoke)", "fallback": "deny: it stays unpaired"}
        self.db.x("INSERT INTO approvals(id, thread_id, tool_use_id, action_type, summary, decision_summary, tool_input,"
                  " state, created_at, raw_input) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (aid, self.DEVICES, None, "pair_device", summary, json.dumps(ds), device_id, "pending", now(),
                   json.dumps({"device_id": device_id})))
        self.ir.log("device.pending", device=device_id, name=name, approval=aid)
        for dev in self.db.q("SELECT * FROM devices WHERE revoked=0 AND approved=1"):
            if dev["fcm_token"] or not dev["web_push"]:
                self.push.send(dev["fcm_token"], "approval", "New device wants to pair", summary, {"approval_id": aid})
            if dev["web_push"]:
                self._send_web(dev["id"], json.loads(dev["web_push"]), "approval", "New device wants to pair",
                               summary, {"approval_id": aid})
        return aid

    def _decide_device(self, a: dict, state: str, device_id: str | None) -> None:
        target = json.loads(self.db.one("SELECT raw_input FROM approvals WHERE id=?", (a["id"],))["raw_input"])["device_id"]
        if state == "allowed":
            self.db.x("UPDATE devices SET approved=1 WHERE id=? AND revoked=0", (target,))
        else:
            self.pairing.revoke(target)
        self.ir.log("device.decided", device=target, state=state, by=device_id)

    def approve_device(self, device_id: str) -> bool:
        """Local fallback (`brain devices approve`): allow a pending device and close its approval."""
        row = self.db.one("SELECT id FROM approvals WHERE action_type='pair_device' AND state='pending' AND tool_input=?",
                          (device_id,))
        self.db.x("UPDATE devices SET approved=1 WHERE id=? AND revoked=0", (device_id,))
        if row:
            self.db.x("UPDATE approvals SET state='allowed', device_id='cli', decided_at=? WHERE id=?", (now(), row["id"]))
        self.ir.log("device.decided", device=device_id, state="allowed", by="cli")
        return bool(self.db.one("SELECT 1 FROM devices WHERE id=? AND approved=1", (device_id,)))

    # ---- approvals -------------------------------------------------------------------------------
    def approval(self, aid: str) -> dict | None:
        r = self.db.one("SELECT * FROM approvals WHERE id=?", (aid,))
        if not r:
            return None
        d = dict(r)
        d["decision_summary"] = json.loads(d["decision_summary"])
        d.pop("tool_input", None)
        d.pop("raw_input", None)
        return d

    async def decide(self, aid: str, decision: str, note: str | None, device_id: str | None) -> dict:
        a = self.approval(aid)
        if not a:
            raise KeyError(aid)
        if a["state"] != "pending":
            return a
        state = "allowed" if decision == "allow" else "denied"
        self.db.x("UPDATE approvals SET state=?, note=?, device_id=?, decided_at=? WHERE id=?",
                  (state, note, device_id, now(), aid))
        if a["action_type"] == "pair_device":
            self._decide_device(a, state, device_id)
            return self.approval(aid)
        self.events.append(a["thread_id"], "approval.decided", {"approval_id": aid, "decision": decision, "note": note},
                           actor={"kind": "user", "device_id": device_id})
        self.ir.log("approval", id=aid, decision=decision, device=device_id)
        for w in self.db.q("SELECT * FROM waits WHERE thread_id=? AND kind='approval' AND state='open'", (a["thread_id"],)):
            if json.loads(w["predicate"]).get("approval_id") == aid:
                self.db.x("UPDATE waits SET state='fired', fired_at=? WHERE id=?", (now(), w["id"]))
        t = self.thread(a["thread_id"])
        prov = self.providers.get(t["provider"]) if t else None
        if state == "allowed":
            self._elevate.add(a["thread_id"])  # the approved action may be outside the sandbox scope
        raw = json.loads(self.db.one("SELECT raw_input FROM approvals WHERE id=?", (aid,))["raw_input"] or "{}")
        if state == "allowed" and prov and getattr(prov, "on_approved", None) and raw:
            prov.on_approved(self.provider_ctx(t), raw["tool_name"], raw["tool_input"])  # e.g. agy permission rule
        if getattr(prov, "supports_defer", True):
            resume = None  # the CLI re-attempts the deferred call itself
        elif state == "allowed":
            resume = f"[Brain] The owner approved this on their phone:\n{a['summary']}\nRun exactly that, unchanged, now."
        else:
            resume = (f"[Brain] The owner denied this:\n{a['summary']}\n" + (f"Owner says: {note}\n" if note else "")
                      + "Do not run it; continue without it or end the turn.")
        running = a["thread_id"] in self._tasks and not self._tasks[a["thread_id"]].done()
        if running:  # the turn is still winding down after the deferral: resume as soon as it ends
            self._resume_after[a["thread_id"]] = resume
        elif not self.paused:
            self._start(a["thread_id"], resume, wake_reason=f"approval {decision}")
        return self.approval(aid)

    # ---- jobs & waits ----------------------------------------------------------------------------
    def submit_job(self, thread_id: str, script: Path, extra_args: list[str]) -> dict:
        t = self.thread(thread_id)
        run = f"run_{uuid.uuid4().hex[:8]}"
        repo = self.settings.repo(t["repo_id"]) if t else None
        base = (Path(t["vault_dir"]) if t and t.get("vault_dir") else Path(repo.vault_root) if repo and repo.vault_root
                else Path(t["worktree"] if t else ".") / ".brain-runs")
        run_dir = base / "runs" / run
        name = f"brain.{thread_id[-8:]}.{run[-8:]}"
        job_id, sentinel = slurm.submit(script, job_name=name, comment={"brain_thread": thread_id, "brain_run": run},
                                        run_dir=run_dir, extra_args=extra_args,
                                        env_exports={"BRAIN_THREAD_ID": thread_id, "BRAIN_RUN_ID": run},
                                        cwd=Path(t["worktree"]) if t and t["worktree"] else None)
        gpus = next((int(a.split("=")[-1].split(":")[-1]) for a in extra_args if a.startswith("--gpus")
                     and a.split("=")[-1].split(":")[-1].isdigit()), None)
        label = script.stem if script.name != ".brain-submit.sh" else name
        self.db.x("INSERT INTO jobs(job_id, thread_id, run_id, name, gpus, sentinel, state, submitted_at, updated_at, label)"
                  " VALUES (?,?,?,?,?,?,?,?,?,?)", (job_id, thread_id, run, name, gpus, str(sentinel), "PENDING", now(), now(), label))
        self.db.x("INSERT INTO waits(id, thread_id, kind, predicate, created_at) VALUES (?,?,?,?,?)",
                  (f"wt_{uuid.uuid4().hex[:12]}", thread_id, "slurm_job_completed", json.dumps({"job_id": job_id}), now()))
        self.events.append(thread_id, "job.submitted", {"job_id": job_id, "name": name, "gpus": gpus,
                                                        "run_dir": str(run_dir)})
        self.events.append(thread_id, "wait.created", {"kind": "slurm_job_completed", "predicate": f"job {job_id}"})
        self.ir.log("sbatch", job=job_id, thread=thread_id, name=name, script=str(script))
        self._ledger("jobs", 1, thread_id)
        return {"job_id": job_id, "run_id": run, "name": name}

    def cancel_job(self, job_id: str) -> bool:
        ok = slurm.cancel(job_id)
        self.ir.log("scancel", job=job_id, ok=ok)
        return ok

    def jobs(self, limit: int = 100) -> list[dict]:
        out = []
        for r in self.db.q("SELECT j.*, t.title AS thread_title FROM jobs j LEFT JOIN threads t ON t.id = j.thread_id"
                           " ORDER BY j.submitted_at DESC LIMIT ?", (limit,)):
            d = dict(r)
            out.append({"job_id": d["job_id"], "thread_id": d["thread_id"], "name": d["name"], "state": d["state"],
                        "label": d["label"] or d["name"], "thread_title": d["thread_title"],
                        "gpus": d["gpus"], "elapsed": int(d["updated_at"] - d["submitted_at"]),
                        "time_limit": d["time_limit"], "exit_code": d["exit_code"],
                        "submitted_at": d["submitted_at"], "updated_at": d["updated_at"]})
        return out

    def expire_threads(self) -> list[str]:
        """Archive threads idle longer than thread_ttl_days (0 = never). Pinned threads, threads that need the owner
        or are working, and threads with an open wait or approval never expire. History and branch are kept."""
        ttl = float(self.settings.raw.get("thread_ttl_days", 3) or 0)
        if ttl <= 0:
            return []
        rows = self.db.q(
            "SELECT id FROM threads t WHERE archived=0 AND pinned=0 AND state='idle' AND updated_at < ?"
            " AND NOT EXISTS (SELECT 1 FROM approvals a WHERE a.thread_id=t.id AND a.state='pending')"
            " AND NOT EXISTS (SELECT 1 FROM waits w WHERE w.thread_id=t.id AND w.state='open')",
            (now() - ttl * 86400,))
        out = []
        for r in rows:
            if r["id"] in self._tasks:
                continue
            self.db.x("UPDATE threads SET archived=1 WHERE id=?", (r["id"],))
            days = f"{ttl:g}"
            self.events.append(r["id"], "system.notice",
                               {"text": f"Archived after {days} idle days. Send a message to bring it back."})
            out.append(r["id"])
        if out:
            self.ir.log("expire", threads=len(out))
        return out

    async def tick(self, force: bool = False) -> None:
        """One scheduler pass: job states, wait conditions, wakes, idle-thread expiry."""
        self.expire_threads()
        jobs = [dict(r) for r in self.db.q("SELECT * FROM jobs WHERE state NOT IN (%s)" %
                                           ",".join("?" * len(slurm.TERMINAL)), list(slurm.TERMINAL))]
        updates = await asyncio.to_thread(self.watcher.poll, jobs, force)
        for u in updates:
            self.db.x("UPDATE jobs SET state=?, exit_code=?, updated_at=? WHERE job_id=?",
                      (u.state, u.exit_code, now(), u.job_id))
            j = self.db.one("SELECT * FROM jobs WHERE job_id=?", (u.job_id,))
            if j and j["thread_id"]:
                self.events.append(j["thread_id"], "job.state", {"job_id": u.job_id, "state": u.state,
                                                                 "exit_code": u.exit_code, "reason": u.reason})
            if u.state in slurm.TERMINAL:
                self.ir.log("job.end", job=u.job_id, state=u.state, exit=u.exit_code)
                if u.state != "COMPLETED" and j:
                    t = self.thread(j["thread_id"])
                    self._notify(j["thread_id"], "failed", f"{j['label'] or j['name']} {u.state.lower()}",
                                 f"{t['title'] if t else ''} · exit {u.exit_code}", {"thread_id": j["thread_id"], "job_id": u.job_id})
        for w in self.db.q("SELECT * FROM waits WHERE state='open' AND kind IN ('slurm_job_completed','provider_quota_reset')"):
            pred = json.loads(w["predicate"])
            fire, reason = False, ""
            if w["kind"] == "slurm_job_completed":
                j = self.db.one("SELECT * FROM jobs WHERE job_id=?", (pred["job_id"],))
                if j and j["state"] in slurm.TERMINAL:
                    fire = True
                    reason = (f"[Brain] SLURM job {j['job_id']} ({j['name']}) finished: {j['state']}, exit code "
                              f"{j['exit_code']}. Its output is in the run directory next to {j['sentinel']}. "
                              f"Continue the plan.")
            elif w["kind"] == "provider_quota_reset" and now() >= float(pred.get("resets_at", 0)):
                fire, reason = True, pred.get("prompt") or "[Brain] Usage window reset. Continue where you left off."
            if fire:
                self.db.x("UPDATE waits SET state='fired', fired_at=? WHERE id=?", (now(), w["id"]))
                self.events.append(w["thread_id"], "wait.fired", {"wait_id": w["id"], "kind": w["kind"],
                                                                  "predicate": json.dumps(pred)})
                await self.wake(w["thread_id"], reason)

    async def wake(self, thread_id: str, reason: str) -> None:
        if self.paused or (thread_id in self._tasks and not self._tasks[thread_id].done()):
            self._queued.setdefault(thread_id, []).append(("wake", reason))
            return
        self._ledger("wakes", 1, thread_id)
        self._start(thread_id, reason, wake_reason="wait fired")

    async def run_scheduler(self, interval: float = 10.0) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("scheduler tick failed")
            await asyncio.sleep(interval)

    # ---- pending / notifications / ledger --------------------------------------------------------
    def pending(self) -> dict:
        approvals = [self.approval(r["id"]) for r in self.db.q("SELECT id FROM approvals WHERE state='pending' ORDER BY created_at")]
        waits = [dict(r) | {"predicate": json.loads(r["predicate"])} for r in
                 self.db.q("SELECT * FROM waits WHERE state='open' AND kind!='approval'")]
        jobs = [j for j in self.jobs(50) if j["state"] not in slurm.TERMINAL]
        need = [self.thread_view(dict(r)) for r in self.db.q("SELECT * FROM threads WHERE state='needs_you' AND archived=0")]
        listen = [dict(r) for r in self.db.q("SELECT id, thread_id, title, n_items, n_systems, created_at FROM listening_tests"
                                             " WHERE state='pending' ORDER BY created_at")]
        since = now() - 12 * 3600
        fin = self.db.one("SELECT COUNT(*) AS n FROM jobs WHERE state='COMPLETED' AND updated_at>?", (since,))["n"]
        failed = self.db.one("SELECT COUNT(*) AS n FROM jobs WHERE state IN ('FAILED','TIMEOUT','OUT_OF_MEMORY','NODE_FAIL') AND updated_at>?", (since,))["n"]
        turns = self.db.one("SELECT COUNT(*) AS n, COALESCE(SUM(edits),0) AS e FROM turns WHERE ended_at>?", (since,))
        return {"approvals": approvals, "waits": waits, "jobs": jobs, "threads_needing_you": need, "listening": listen,
                "since": {"ts": since, "jobs_finished": fin, "jobs_failed": failed, "turns": turns["n"],
                          "edits": turns["e"], "paused": self.paused}}

    def _notify(self, thread_id: str, kind: str, title: str, body: str, data: dict) -> None:
        t = self.thread(thread_id)
        if t and t["muted"] and kind not in ("approval", "needs_you"):
            return
        for d in self.db.q("SELECT * FROM devices WHERE revoked=0"):
            if d["fcm_token"] or not d["web_push"]:  # native app (or the dry-run outbox when nothing is registered)
                self.push.send(d["fcm_token"], kind, title, body, data)
            if d["web_push"]:  # PWA
                self._send_web(d["id"], json.loads(d["web_push"]), kind, title, body, data)

    def _send_web(self, device_id: str, sub: dict, kind: str, title: str, body: str, data: dict) -> None:
        msg = {"title": title[:120], "body": body[:240], "data": {"v": "1", "kind": kind, **data}}
        urgency = "high" if kind in ("approval", "needs_you", "failed") else "normal"

        def go() -> None:
            status = self.webpush.send(sub, msg, urgency=urgency)
            with open(self.p.push_outbox, "a") as f:
                f.write(json.dumps({"ts": now(), "kind": kind, "web_push": sub["endpoint"].split("/")[2],
                                    "status": status}) + "\n")
            if status in (404, 410):  # the browser dropped this subscription
                self.db.x("UPDATE devices SET web_push=NULL WHERE id=? AND web_push=?", (device_id, json.dumps(sub)))
        try:
            asyncio.get_running_loop().run_in_executor(None, go)  # never block the event loop on a push service
        except RuntimeError:
            go()

    @property
    def endpoints(self) -> list[str]:
        """Primary relay URL first, then alternates the phone fails over to (e.g. a tunnel and Tailscale)."""
        return [e for e in dict.fromkeys([self.settings.endpoint, *self.settings.raw.get("alt_endpoints", [])]) if e]

    def set_endpoint(self, endpoint: str, alternates: list[str] | None = None) -> int:
        """Record the relay's current public URL (and optional alternates) and push the primary, signed, to every
        paired device. The app fetches the full list over its authenticated session (GET /v1/endpoints)."""
        self.settings.endpoint = endpoint
        if alternates is not None:
            self.settings.raw["alt_endpoints"] = [a for a in alternates if a and a != endpoint]
        self.settings.save(self.p)
        sent = 0
        ts = str(int(now()))
        for d in self.db.q("SELECT * FROM devices WHERE revoked=0"):
            sig = self.ident.sign(b"cb-ep-v1" + endpoint.encode() + ts.encode() + d["id"].encode())
            self.push.send(d["fcm_token"], "transport", "Endpoint changed", endpoint,
                           {"endpoint": endpoint, "endpoint_sig": sig, "ts": ts})
            sent += 1
        self.ir.log("endpoint", url=endpoint, devices=sent)
        return sent

    def _ledger(self, metric: str, value: float, thread_id: str) -> None:
        day = time.strftime("%Y-%m-%d")
        for scope in ("all", thread_id):
            self.db.x("INSERT INTO ledger(day, scope, metric, value) VALUES (?,?,?,?) ON CONFLICT(day, scope, metric)"
                      " DO UPDATE SET value=value+excluded.value", (day, scope, metric, value))

    def ledger_today(self) -> dict:
        day = time.strftime("%Y-%m-%d")
        return {r["metric"]: r["value"] for r in self.db.q("SELECT metric, value FROM ledger WHERE day=? AND scope='all'", (day,))}
