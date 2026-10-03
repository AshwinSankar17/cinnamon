"""Brain: threads, turns, approvals, jobs, durable waits, pause — the single durable control plane."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from . import repos, slurm
from .audit import AuditWriter, IRLog
from .config import SITE_DISABLED_FLAG, Paths, Settings
from .db import DB, row_dict
from .events import EventStore
from .identity import Identity, Pairing
from .policy import PolicyContext, classify
from .providers.base import Provider, TurnContext, TurnResult
from .providers.claude import tool_kind
from .push import PushSender
from .redact import SecretRegistry

log = logging.getLogger("brain")

SYSTEM_APPEND = (
    "You are running under Cluster Brain on a shared HPC cluster. The owner is usually away from the "
    "terminal and follows along on a phone. Rules: (1) Never run training, inference, synthesis or large "
    "ffmpeg jobs directly on this node; submit them with `brain submit <script.sh> [sbatch args]`, which "
    "returns a job id. (2) After submitting, do not poll with sleep/squeue loops: end your turn with a short "
    "plan. Brain wakes you with the result when the job finishes. (3) Commit to your branch freely; pushing "
    "to protected branches, deleting vaults or checkpoints, or deleting outside the worktree pauses for the "
    "owner's approval. If an action is deferred, end the turn and say what you were about to do."
)


def now() -> float:
    return time.time()


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
        if not p.hook_token.exists():
            p.hook_token.write_text(uuid.uuid4().hex + uuid.uuid4().hex)
            os.chmod(p.hook_token, 0o600)
        self.hook_token = p.hook_token.read_text().strip()
        if providers is None:
            from .providers.claude import ClaudeProvider
            from .providers.fake import FakeProvider
            providers = {"claude": ClaudeProvider(), "fake": FakeProvider()}
        self.providers = providers
        self.watcher = slurm.Watcher()
        self._tasks: dict[str, asyncio.Task] = {}
        self._turn_counts: dict[str, dict[str, int]] = {}
        self._queued: dict[str, list[str]] = {}

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
                await self.stop(tid, reason="Brain paused")
        return self.paused

    # ---- threads ---------------------------------------------------------------------------------
    def thread(self, thread_id: str) -> dict | None:
        return row_dict(self.db.one("SELECT * FROM threads WHERE id=?", (thread_id,)))

    def thread_view(self, t: dict) -> dict:
        last = self.db.one("SELECT * FROM events WHERE thread_id=? AND visibility='user' ORDER BY seq DESC LIMIT 1",
                           (t["id"],))
        out = {k: t[k] for k in ("id", "repo_id", "title", "mode", "branch", "provider", "state", "parent_id",
                                 "fork_turn", "last_seq", "updated_at")}
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
                      base_ref: str | None = None, parent_id: str | None = None, fork_turn: int | None = None) -> dict:
        repo = self.settings.repo(repo_id)
        if not repo:
            raise KeyError(f"unknown repo {repo_id}")
        provider = provider or self.settings.default_provider
        if provider not in self.providers:
            raise KeyError(f"provider {provider} is not configured")
        tid = f"thr_{uuid.uuid4().hex[:16]}"
        title = title or "New thread"
        worktree, branch = repo.path, None
        if mode == "worktree":
            root = repo.worktree_root or str(Path(repo.path).parent / ".brain-worktrees")
            wt, branch = repos.create_worktree(repo.path, root, tid, title, base_ref or repo.default_branch)
            worktree = str(wt)
        self.db.x(
            "INSERT INTO threads(id, repo_id, title, mode, branch, worktree, provider, state, parent_id, fork_turn,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, repo_id, title, mode, branch, worktree, provider, "idle", parent_id, fork_turn, now(), now()),
        )
        self.events.append(tid, "system.notice", {"text": f"Thread created on {repo.name or repo.id}"
                                                  f" ({'read-only' if mode == 'readonly' else branch})."})
        return self.thread_view(self.thread(tid))

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

    # ---- turns -----------------------------------------------------------------------------------
    def policy_ctx(self, t: dict) -> PolicyContext:
        repo = self.settings.repo(t["repo_id"])
        job_ids = frozenset(r["job_id"] for r in self.db.q("SELECT job_id FROM jobs"))
        return PolicyContext(profile=repo.profile if repo else "auto", worktree=t["worktree"],
                             vault_roots=tuple(x for x in [repo.vault_root if repo else ""] if x),
                             protected_branches=tuple(repo.protected_branches if repo else ("main", "master")),
                             brain_job_ids=job_ids)

    async def send_message(self, thread_id: str, text: str, actor: dict | None = None) -> int:
        t = self.thread(thread_id)
        if not t:
            raise KeyError(thread_id)
        ev = self.events.append(thread_id, "user.message", {"text": text}, actor=actor or {"kind": "user"})
        if self.paused:
            self.events.append(thread_id, "system.notice", {"text": "Brain is paused; this message will run on resume."})
            self._queued.setdefault(thread_id, []).append(text)
            return ev["seq"]
        if thread_id in self._tasks and not self._tasks[thread_id].done():
            self._queued.setdefault(thread_id, []).append(text)
            self.events.append(thread_id, "system.notice", {"text": "Queued until the current turn ends."})
            return ev["seq"]
        self._start(thread_id, text)
        return ev["seq"]

    def _start(self, thread_id: str, prompt: str | None, wake_reason: str | None = None) -> None:
        task = asyncio.create_task(self._run_turn(thread_id, prompt, wake_reason))
        self._tasks[thread_id] = task

    async def stop(self, thread_id: str, reason: str = "Stopped from the phone") -> None:
        t = self.thread(thread_id)
        task = self._tasks.get(thread_id)
        if t and task and not task.done():
            prov = self.providers.get(t["provider"])
            if prov:
                await prov.interrupt(thread_id)
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=20)
            except asyncio.TimeoutError:
                task.cancel()
            self.events.append(thread_id, "system.notice", {"text": reason})
        self._queued.pop(thread_id, None)

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
            " created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (aid, thread_id, tool_use_id, d.action_type, d.summary, json.dumps(d.decision_summary), key, "pending", now()))
        self.db.x("INSERT INTO waits(id, thread_id, kind, predicate, created_at) VALUES (?,?,?,?,?)",
                  (f"wt_{uuid.uuid4().hex[:12]}", thread_id, "approval", json.dumps({"approval_id": aid}), now()))
        self.events.append(thread_id, "approval.requested",
                           {"approval_id": aid, "action_type": d.action_type, "summary": d.summary,
                            "decision_summary": d.decision_summary})
        self._notify(thread_id, "approval", f"{d.decision_summary.get('verb', 'Approval needed')}",
                     f"{t['title']} · {d.decision_summary.get('target', '')} · pending until you decide",
                     {"thread_id": thread_id, "approval_id": aid})
        return {"decision": "defer", "reason": "Waiting for the owner's approval on their phone. End your turn."}

    async def _run_turn(self, thread_id: str, prompt: str | None, wake_reason: str | None = None) -> None:
        t = self.thread(thread_id)
        prov = self.providers[t["provider"]]
        turn_id = f"turn_{uuid.uuid4().hex[:12]}"
        counts = {"reads": 0, "edits": 0, "commands": 0, "failed": 0}
        started = now()
        self._set_state(thread_id, "running")
        start_ev = self.events.append(thread_id, "turn.started", {"turn_id": turn_id, "provider": t["provider"],
                                                                   "wake_reason": wake_reason}, turn_id=turn_id)
        self.db.x("INSERT INTO turns(turn_id, thread_id, provider, started_seq, started_at) VALUES (?,?,?,?,?)",
                  (turn_id, thread_id, t["provider"], start_ev["seq"], started))
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

        sess = self.db.one("SELECT native_session_id FROM provider_sessions WHERE thread_id=? AND provider=?",
                           (thread_id, t["provider"]))
        repo = self.settings.repo(t["repo_id"])
        env = {
            "BRAIN_HOOK_SOCK": str(self.p.sock), "BRAIN_HOOK_TOKEN": self.hook_token, "BRAIN_THREAD_ID": thread_id,
            "BRAIN_PROFILE": repo.profile if repo else "auto", "CLAUDE_CONFIG_DIR": str(self.p.claude_config_dir),
            "GIT_AUTHOR_NAME": "Cluster Brain agent", "GIT_COMMITTER_NAME": "Cluster Brain agent",
        }

        async def submit(script_text: str) -> str:
            path = Path(t["worktree"] or ".") / ".brain-submit.sh"
            path.write_text(script_text)
            return self.submit_job(thread_id, path, [])["job_id"]

        ctx = TurnContext(thread_id=thread_id, cwd=Path(t["worktree"] or "."), prompt=prompt,
                          resume_session=sess["native_session_id"] if sess else None, env=env, emit=emit,
                          pretool=lambda tool, inp, tuid: self.pretool(thread_id, tool, inp, tuid),
                          system_append=SYSTEM_APPEND, permission_mode=self.settings.raw.get("claude_permission_mode", "auto"),
                          extra={"submit": submit})
        self.ir.log("provider.start", thread=thread_id, provider=t["provider"], turn=turn_id)
        try:
            res = await prov.run_turn(ctx)
        except asyncio.CancelledError:
            res = TurnResult(stop_reason="cancelled")
        except Exception as e:  # provider crash must never corrupt the thread
            log.exception("provider failed")
            res = TurnResult(error=f"{type(e).__name__}: {e}")
        self.ir.log("provider.end", thread=thread_id, provider=t["provider"], turn=turn_id,
                    stop=res.stop_reason, error=(res.error or "")[:120] or None)

        if res.session_id:
            self.db.x("INSERT INTO provider_sessions(thread_id, provider, native_session_id, sync_seq, updated_at)"
                      " VALUES (?,?,?,?,?) ON CONFLICT(thread_id, provider) DO UPDATE SET"
                      " native_session_id=excluded.native_session_id, sync_seq=excluded.sync_seq, updated_at=excluded.updated_at",
                      (thread_id, t["provider"], res.session_id, self.events.last_seq(thread_id), now()))
        diffstat = {"files": 0, "added": 0, "removed": 0}
        if pre_ref:
            try:
                post_ref = f"refs/brain/{thread_id}/{start_ev['seq']}-post"
                repos.snapshot(t["worktree"], post_ref, f"after {turn_id}")
                for fc in repos.diff_between(t["worktree"], pre_ref, post_ref):
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
        if dur > 30_000 and not res.error and self._resting_state(thread_id) == "idle":
            self._notify(thread_id, "done", f"{t['title']}: turn finished",
                         (res.text or "Done.")[:200], {"thread_id": thread_id})
        self._tasks.pop(thread_id, None)
        queued = self._queued.pop(thread_id, [])
        if queued and not self.paused and self._resting_state(thread_id) == "idle":
            self._start(thread_id, "\n\n".join(queued))

    def _resting_state(self, thread_id: str) -> str:
        if self.db.one("SELECT 1 FROM approvals WHERE thread_id=? AND state='pending'", (thread_id,)):
            return "needs_you"
        if self.db.one("SELECT 1 FROM waits WHERE thread_id=? AND state='open'", (thread_id,)):
            return "waiting"
        return "paused" if self.paused else "idle"

    # ---- approvals -------------------------------------------------------------------------------
    def approval(self, aid: str) -> dict | None:
        r = self.db.one("SELECT * FROM approvals WHERE id=?", (aid,))
        if not r:
            return None
        d = dict(r)
        d["decision_summary"] = json.loads(d["decision_summary"])
        d.pop("tool_input", None)
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
        self.events.append(a["thread_id"], "approval.decided", {"approval_id": aid, "decision": decision, "note": note},
                           actor={"kind": "user", "device_id": device_id})
        self.ir.log("approval", id=aid, decision=decision, device=device_id)
        for w in self.db.q("SELECT * FROM waits WHERE thread_id=? AND kind='approval' AND state='open'", (a["thread_id"],)):
            if json.loads(w["predicate"]).get("approval_id") == aid:
                self.db.x("UPDATE waits SET state='fired', fired_at=? WHERE id=?", (now(), w["id"]))
        if not self.paused and a["thread_id"] not in self._tasks:
            self._start(a["thread_id"], None, wake_reason=f"approval {decision}")  # resume the deferred call
        return self.approval(aid)

    # ---- jobs & waits ----------------------------------------------------------------------------
    def submit_job(self, thread_id: str, script: Path, extra_args: list[str]) -> dict:
        t = self.thread(thread_id)
        run = f"run_{uuid.uuid4().hex[:8]}"
        repo = self.settings.repo(t["repo_id"]) if t else None
        base = Path(repo.vault_root) if repo and repo.vault_root else Path(t["worktree"] if t else ".") / ".brain-runs"
        run_dir = base / "runs" / run
        name = f"brain.{thread_id[-8:]}.{run[-8:]}"
        job_id, sentinel = slurm.submit(script, job_name=name, comment={"brain_thread": thread_id, "brain_run": run},
                                        run_dir=run_dir, extra_args=extra_args,
                                        env_exports={"BRAIN_THREAD_ID": thread_id, "BRAIN_RUN_ID": run},
                                        cwd=Path(t["worktree"]) if t and t["worktree"] else None)
        gpus = next((int(a.split("=")[-1].split(":")[-1]) for a in extra_args if a.startswith("--gpus")
                     and a.split("=")[-1].split(":")[-1].isdigit()), None)
        self.db.x("INSERT INTO jobs(job_id, thread_id, run_id, name, gpus, sentinel, state, submitted_at, updated_at)"
                  " VALUES (?,?,?,?,?,?,?,?,?)", (job_id, thread_id, run, name, gpus, str(sentinel), "PENDING", now(), now()))
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
        for r in self.db.q("SELECT * FROM jobs ORDER BY submitted_at DESC LIMIT ?", (limit,)):
            d = dict(r)
            out.append({"job_id": d["job_id"], "thread_id": d["thread_id"], "name": d["name"], "state": d["state"],
                        "gpus": d["gpus"], "elapsed": int(d["updated_at"] - d["submitted_at"]),
                        "time_limit": d["time_limit"], "exit_code": d["exit_code"]})
        return out

    async def tick(self, force: bool = False) -> None:
        """One scheduler pass: job states, wait conditions, wakes."""
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
                    self._notify(j["thread_id"], "failed", f"{j['name']} {u.state.lower()}",
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
        if self.paused:
            self._queued.setdefault(thread_id, []).append(reason)
            return
        if thread_id in self._tasks and not self._tasks[thread_id].done():
            self._queued.setdefault(thread_id, []).append(reason)
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
        since = now() - 12 * 3600
        fin = self.db.one("SELECT COUNT(*) AS n FROM jobs WHERE state='COMPLETED' AND updated_at>?", (since,))["n"]
        failed = self.db.one("SELECT COUNT(*) AS n FROM jobs WHERE state IN ('FAILED','TIMEOUT','OUT_OF_MEMORY','NODE_FAIL') AND updated_at>?", (since,))["n"]
        turns = self.db.one("SELECT COUNT(*) AS n, COALESCE(SUM(edits),0) AS e FROM turns WHERE ended_at>?", (since,))
        return {"approvals": approvals, "waits": waits, "jobs": jobs, "threads_needing_you": need,
                "since": {"ts": since, "jobs_finished": fin, "jobs_failed": failed, "turns": turns["n"],
                          "edits": turns["e"], "paused": self.paused}}

    def _notify(self, thread_id: str, kind: str, title: str, body: str, data: dict) -> None:
        t = self.thread(thread_id)
        if t and t["muted"] and kind not in ("approval", "needs_you"):
            return
        for d in self.db.q("SELECT * FROM devices WHERE revoked=0"):
            self.push.send(d["fcm_token"], kind, title, body, data)

    def set_endpoint(self, endpoint: str) -> int:
        """Record the relay's current public URL and push it, signed, to every paired device."""
        self.settings.endpoint = endpoint
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
