"""Antigravity (agy) adapter: drives `agy -p --output-format stream-json` in a Brain-owned app-data profile.

Permissions (ctx.extra["agy_permissions"]):
  "rules" (default, approach b): Brain keeps settings.json `permissions.allow` = a base list mirroring its auto
      policy + one exact `command(...)` rule per approved call. Headless agy auto-denies any tool call without a
      rule and that ends the turn, so the base list matters. Rules are checked against the hook-REWRITTEN command.
  "skip" (fallback, approach a): --dangerously-skip-permissions; brain-hook is the only gate.
brain-hook is installed in the workspace's .agents/hooks.json (agy reads hooks only there or globally) and kept
out of git via info/exclude. agy has no `defer`: a pending approval is a deny and Brain resumes with a prompt.

agy conversations live in an undocumented SQLite/protobuf store, so Brain cannot write one: switching TO agy
replays missing history verbatim in the next prompt (no `hydrate`). Its transcript_full.jsonl is readable.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from .. import transcript
from .base import TurnContext, TurnResult

DEFAULT_APP_DIR = "antigravity-cli-profiles/brain"  # relative to ~/.gemini, like agy's own --app_data_dir use
BASE_ALLOW = ["read_file(/)", "command(ls)", "command(pwd)", "command(cat)", "command(head)", "command(tail)",
              "command(wc)", "command(grep)", "command(rg)", "command(find)", "command(git status)",
              "command(git diff)", "command(git log)", "command(git show)", "command(brain submit)",
              "command(squeue)", "command(sacct)", "command(srun)"]
EDIT_TOOLS = {"write_to_file", "replace_file_content", "multi_replace_file_content", "sed_file", "notebook_edit"}


def app_dir(ctx: TurnContext) -> tuple[str, Path]:
    """(value for --app_data_dir, absolute path). agy rejects absolute paths but resolves relative ones against
    ~/.gemini, `..` included, so Brain's profile can live in BRAIN_ROOT/agy next to its claude/ and codex/ homes."""
    d = ctx.extra.get("agy_app_data_dir") or DEFAULT_APP_DIR
    gemini = Path.home() / ".gemini"
    full = Path(d) if Path(d).is_absolute() else gemini / d
    return os.path.relpath(full, gemini), full


class AgyProvider:
    name = "agy"
    supports_defer = False

    def __init__(self, binary: str | None = None):
        self.binary = binary or os.environ.get("BRAIN_AGY_BIN") or shutil.which("agy") or "agy"
        self._procs: dict[str, asyncio.subprocess.Process] = {}

    async def interrupt(self, thread_id: str) -> None:
        p = self._procs.get(thread_id)
        if p and p.returncode is None:
            p.send_signal(signal.SIGINT)

    # ---- setup -----------------------------------------------------------------------------------
    @staticmethod
    def _install_hook(cwd: Path) -> None:
        d = cwd / ".agents"
        d.mkdir(exist_ok=True)
        f = d / "hooks.json"
        hooks = json.loads(f.read_text() or "{}") if f.exists() else {}
        want = {"PreToolUse": [{"matcher": "*", "hooks": [
            {"type": "command", "command": f"{sys.executable} -m brain.hook", "timeout": 150}]}]}
        if hooks.get("cluster-brain") != want:
            hooks["cluster-brain"] = want  # other named hooks in the file are kept
            f.write_text(json.dumps(hooks, indent=2))
        try:
            exclude = Path(subprocess.run(["git", "-C", str(cwd), "rev-parse", "--git-path", "info/exclude"],
                                          capture_output=True, text=True, check=True).stdout.strip())
            exclude = exclude if exclude.is_absolute() else cwd / exclude
            if "/.agents/hooks.json" not in (exclude.read_text() if exclude.exists() else ""):
                exclude.parent.mkdir(parents=True, exist_ok=True)
                with exclude.open("a") as fh:
                    fh.write("\n/.agents/hooks.json\n")
        except (subprocess.CalledProcessError, OSError):
            pass

    @staticmethod
    def _settings(ctx: TurnContext, add_allow: list[str] = ()) -> None:
        _, root = app_dir(ctx)
        root.mkdir(parents=True, exist_ok=True)
        f = root / "settings.json"
        s = json.loads(f.read_text() or "{}") if f.exists() else {}
        trusted = s.setdefault("trustedWorkspaces", [])
        for w in (str(ctx.cwd), *ctx.extra.get("vault_roots", [])):
            if w not in trusted:
                trusted.append(w)
        if ctx.extra.get("agy_permissions", "rules") == "rules":
            allow = s.setdefault("permissions", {}).setdefault("allow", [])
            for r in [*ctx.extra.get("agy_allow", BASE_ALLOW), *add_allow]:
                if r not in allow:
                    allow.append(r)
        f.write_text(json.dumps(s, indent=2))
        os.chmod(f, 0o600)

    def on_approved(self, ctx: TurnContext, tool_name: str, tool_input: dict) -> None:
        if tool_name == "Bash" and tool_input.get("command"):
            self._settings(ctx, [f"command({tool_input['command']})"])

    # ---- turn ------------------------------------------------------------------------------------
    async def run_turn(self, ctx: TurnContext) -> TurnResult:
        """One Brain turn. Under "rules", agy checks its own permission rules before the hook for some commands
        (e.g. unsandboxed ones), so a command with no rule is denied without Brain ever seeing it. Each such
        command is put through Brain's policy afterwards: allowed -> add the exact rule and retry in the same
        conversation; always-ask -> Brain records an approval (approving adds the rule and resumes); deny -> stays."""
        self._install_hook(ctx.cwd)
        self._settings(ctx)
        prompt = ctx.prompt or "Continue."
        if ctx.system_append and not ctx.resume_session:
            prompt = f"<operating_rules>\n{ctx.system_append}\n</operating_rules>\n\n{prompt}"  # agy has no system-append flag
        res = TurnResult(session_id=ctx.resume_session)
        for _ in range(3):  # ponytail: at most 2 policy-granted retries per turn
            blocked = await self._once(ctx, prompt, res)
            if res.error or not blocked or ctx.extra.get("agy_permissions") == "skip":
                return res
            granted = []
            for command in blocked:
                d = (await ctx.pretool("Bash", {"command": command}, None)).get("decision")
                if d == "defer":
                    res.stop_reason = "tool_deferred"
                    return res
                if d in ("allow", "none", None):
                    granted.append(command)
            if not granted:
                return res
            self._settings(ctx, [f"command({c})" for c in granted])
            ctx.emit("system.notice", {"text": f"Added agy permission rule(s) for: {'; '.join(granted)}"})
            prompt = "[Brain] Permission granted for: " + "; ".join(granted) + ". Run it now, unchanged."
        return res

    async def _once(self, ctx: TurnContext, prompt: str, res: TurnResult) -> list[str]:
        rel, _ = app_dir(ctx)
        cmd = [self.binary, f"--app_data_dir={rel}", "-p", prompt, "--output-format", "stream-json",
               "--print-timeout", ctx.extra.get("agy_print_timeout", "60m")]
        cmd += ["--conversation", res.session_id] if res.session_id else []
        for v in ctx.extra.get("vault_roots", []):  # agy sandboxes commands to its workspace: make the vault part of it
            cmd.append(f"--add-dir={v}")
        cmd += ["--dangerously-skip-permissions"] if ctx.extra.get("agy_permissions") == "skip" else []
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(ctx.cwd), env={**os.environ, **ctx.env}, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024)
        self._procs[ctx.thread_id] = proc
        run = {"blocked": [], "last_cmd": None}
        try:
            assert proc.stdout
            async for raw in proc.stdout:
                try:
                    self._map(json.loads(raw), ctx, res, run)
                except json.JSONDecodeError:
                    continue
            await proc.wait()
            if proc.returncode not in (0, None) and not res.error:
                err = (await proc.stderr.read()).decode("utf-8", "replace")[-2000:] if proc.stderr else ""
                res.error = err or f"agy exited {proc.returncode}"
        finally:
            self._procs.pop(ctx.thread_id, None)
        return run["blocked"]

    def _map(self, msg: dict, ctx: TurnContext, res: TurnResult, run: dict | None = None) -> None:
        run = run if run is not None else {"blocked": [], "last_cmd": None}
        ev = msg.get("event")
        if ev == "init":
            res.session_id = msg.get("conversation_id") or res.session_id
        elif ev == "step_update":
            s = msg.get("step_update") or {}
            if s.get("step_type") != "tool":
                return
            ti = s.get("tool_info") or {}
            p, name, sid = ti.get("parameters") or {}, s.get("tool_name", "?"), f"agy-{s.get('step_index')}"
            tool = "Bash" if name == "run_command" else ("Edit" if name in EDIT_TOOLS else name)
            if s.get("state") == "ACTIVE":
                if name == "run_command":
                    run["last_cmd"] = p.get("CommandLine")  # a headless denial ends the turn: last one is the culprit
                summary = p.get("CommandLine") or p.get("AbsolutePath") or p.get("TargetFile") or name
                ctx.emit("tool.started", {"tool_use_id": sid, "tool": tool, "summary": str(summary)[:300],
                                          "input_preview": json.dumps(p)[:1500]})
            else:
                err = (ti.get("error") or {}).get("message", "")
                ctx.emit("tool.completed", {"tool_use_id": sid, "tool": tool, "ok": s.get("state") == "DONE",
                                            "output_preview": err[-4000:]})
                if name == "run_command" and "permission check failed" in err and p.get("CommandLine"):
                    run["blocked"].append(p["CommandLine"])  # agy's own rule check denied it before brain-hook ran
        elif ev == "result":
            r = msg.get("result") or {}
            res.session_id = r.get("conversation_id") or res.session_id
            res.text = r.get("response") or ""
            if res.text.strip():
                ctx.emit("assistant.text", {"text": res.text})
            u = r.get("usage") or {}
            res.usage = {"input_tokens": int(u.get("input_tokens", 0) or 0), "output_tokens": int(u.get("output_tokens", 0) or 0)}
            res.stop_reason = "end_turn" if r.get("status") == "SUCCESS" else "error"
            if r.get("status") == "ERROR":
                res.error = r.get("error") or "agy reported an error"
            denied_cmd = any(a.get("action") == "command" for a in r.get("denied_actions") or [])
            if denied_cmd and not run["blocked"] and run["last_cmd"]:
                run["blocked"].append(run["last_cmd"])  # agy sometimes reports the denied step as DONE, no error text
            if r.get("denied_actions") and not run["blocked"]:  # commands in `blocked` are handled by run_turn
                acts = ", ".join(a.get("action", "?") for a in r["denied_actions"])
                ctx.emit("provider.status", {"provider": "agy", "status": "blocked",
                                             "message": f"agy auto-denied {acts} (no permission rule); the turn stopped there"})

    # ---- transcript ------------------------------------------------------------------------------
    def native_items(self, ctx: TurnContext, session_id: str) -> list[dict] | None:
        f = app_dir(ctx)[1] / "brain" / session_id / ".system_generated" / "logs" / "transcript_full.jsonl"
        return transcript.read_agy(f)[0] if f.exists() else None
