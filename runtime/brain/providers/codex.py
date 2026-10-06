"""Codex adapter: drives `codex exec --json` with Brain's own CODEX_HOME (ctx.env, see Brain._run_turn).

brain-hook is installed as a user-level PreToolUse hook in $CODEX_HOME/hooks.json and speaks Codex's
Claude-style contract (permissionDecision / updatedInput). Codex has no `defer`: a pending approval is a deny,
and Brain resumes the session with a prompt once the owner decides. Brain installs the hook itself, so it runs
with --dangerously-bypass-hook-trust instead of asking someone to trust it in the TUI.

Writes are OS-sandboxed to the thread's repo/vault scope (see _cmd).
Note: Codex's built-in exec policy can still refuse some commands (e.g. `rm -rf`) after the hook allows them.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import sys
import time
from pathlib import Path

from .. import transcript
from .base import TurnContext, TurnResult

BASH_WRAP = re.compile(r"^/bin/(?:ba)?sh -lc '(.*)'$", re.S)


def _plain(cmd) -> str:
    cmd = " ".join(cmd) if isinstance(cmd, list) else str(cmd or "")
    m = BASH_WRAP.match(cmd)
    return m.group(1) if m else cmd


class CodexProvider:
    name = "codex"
    supports_defer = False

    def __init__(self, binary: str | None = None):
        self.binary = binary or os.environ.get("BRAIN_CODEX_BIN") or shutil.which("codex") or "codex"
        self._procs: dict[str, asyncio.subprocess.Process] = {}

    async def interrupt(self, thread_id: str) -> None:
        p = self._procs.get(thread_id)
        if p and p.returncode is None:
            p.send_signal(signal.SIGINT)

    @staticmethod
    def _home(ctx: TurnContext) -> Path:
        return Path(ctx.env.get("CODEX_HOME") or Path.home() / ".codex")

    def _install_hook(self, ctx: TurnContext) -> None:
        home = self._home(ctx)
        home.mkdir(parents=True, exist_ok=True)
        f = home / "hooks.json"
        want = {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [
            {"type": "command", "command": f"{sys.executable} -m brain.hook", "timeout": 150}]}]}}
        if not f.exists() or json.loads(f.read_text() or "{}") != want:
            f.write_text(json.dumps(want, indent=2))

    def _cmd(self, ctx: TurnContext, prompt: str, sid: str | None, hooks: bool = True) -> list[str]:
        # OS-enforced repo/vault scope: writable = worktree (cwd) + vault + the repo's .git + temp; network stays on
        # (Brain's Unix socket for `brain submit`, pip, git). After the owner approves an action, that one turn runs
        # unsandboxed (ctx.extra["elevated"]) so the approved out-of-scope change can happen; brain-hook still gates it.
        sandbox = "read-only" if ctx.readonly else ("danger-full-access" if ctx.extra.get("elevated")
                                                    else ctx.extra.get("codex_sandbox", "workspace-write"))
        cmd = [self.binary, "exec", "--json", "--skip-git-repo-check", "-s", sandbox]
        if sandbox == "workspace-write":
            cmd += ["-c", f"sandbox_workspace_write.writable_roots={json.dumps(ctx.extra.get('scope_roots', []))}",
                    "-c", "sandbox_workspace_write.network_access=true"]
        if hooks:
            cmd.append("--dangerously-bypass-hook-trust")
        if ctx.system_append:
            cmd += ["-c", f"developer_instructions={json.dumps(ctx.system_append)}"]  # JSON string == TOML basic string
        return cmd + (["resume", sid] if sid else []) + [prompt]

    async def _spawn(self, ctx: TurnContext, cmd: list[str]) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            *cmd, cwd=str(ctx.cwd), env={**os.environ, **ctx.env}, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024)

    async def run_turn(self, ctx: TurnContext) -> TurnResult:
        self._install_hook(ctx)
        proc = await self._spawn(ctx, self._cmd(ctx, ctx.prompt or "Continue.", ctx.resume_session))
        self._procs[ctx.thread_id] = proc
        res = TurnResult(session_id=ctx.resume_session)
        try:
            assert proc.stdout
            async for raw in proc.stdout:
                try:
                    self._map(json.loads(raw), ctx, res)
                except json.JSONDecodeError:
                    continue
            await proc.wait()
            if proc.returncode not in (0, None) and not res.error:
                err = (await proc.stderr.read()).decode("utf-8", "replace")[-2000:] if proc.stderr else ""
                res.error = err or f"codex exited {proc.returncode}"
        finally:
            self._procs.pop(ctx.thread_id, None)
        if res.error and re.search(r"usage limit|rate limit|quota", res.error, re.I):
            res.rate_limited_until = time.time() + 3600  # ponytail: Codex reports no reset time in exec --json
        return res

    def _map(self, msg: dict, ctx: TurnContext, res: TurnResult) -> None:
        t = msg.get("type")
        if t == "thread.started":
            res.session_id = msg.get("thread_id") or res.session_id
        elif t in ("item.started", "item.completed"):
            it = msg.get("item") or {}
            typ, iid = it.get("type"), it.get("id", "")
            if typ == "agent_message" and t == "item.completed" and it.get("text", "").strip():
                res.text = it["text"]
                ctx.emit("assistant.text", {"text": it["text"]})
            elif typ == "command_execution":
                cmd = _plain(it.get("command"))
                if t == "item.started":
                    ctx.emit("tool.started", {"tool_use_id": iid, "tool": "Bash", "summary": cmd[:300],
                                              "input_preview": json.dumps({"command": cmd})[:1500]})
                else:
                    ctx.emit("tool.completed", {"tool_use_id": iid, "tool": "Bash", "ok": it.get("exit_code") == 0,
                                                "exit_code": it.get("exit_code"),
                                                "output_preview": (it.get("aggregated_output") or "")[-4000:]})
            elif typ == "error":
                if "bypass-hook-trust" not in (it.get("message") or ""):  # our own flag's warning
                    ctx.emit("provider.status", {"provider": "codex", "status": "error", "message": it.get("message", "")})
            elif typ not in ("reasoning", None) and t == "item.completed":  # file_change, mcp_tool_call, web_search...
                summary = ", ".join(c.get("path", "") for c in it.get("changes") or []) or typ
                tool = "Edit" if typ == "file_change" else typ
                ctx.emit("tool.started", {"tool_use_id": iid, "tool": tool, "summary": summary[:300],
                                          "input_preview": json.dumps(it)[:1500]})
                ctx.emit("tool.completed", {"tool_use_id": iid, "tool": tool, "ok": it.get("status") != "failed",
                                            "output_preview": ""})
        elif t == "turn.completed":
            u = msg.get("usage") or {}
            res.stop_reason = "end_turn"
            res.usage = {"input_tokens": int(u.get("input_tokens", 0) or 0), "output_tokens": int(u.get("output_tokens", 0) or 0)}
        elif t in ("turn.failed", "error"):
            res.stop_reason = "error"
            res.error = (msg.get("error") or {}).get("message") or msg.get("message") or json.dumps(msg)[:500]

    # ---- transcript ------------------------------------------------------------------------------
    def _rollout(self, ctx: TurnContext, sid: str) -> Path | None:
        return next((self._home(ctx) / "sessions").rglob(f"*{sid}.jsonl"), None)

    def native_items(self, ctx: TurnContext, session_id: str) -> list[dict] | None:
        f = self._rollout(ctx, session_id)
        return transcript.read_codex(f)[0] if f else None

    async def hydrate(self, ctx: TurnContext, items: list[dict]) -> str:
        """Append foreign history to this thread's Codex rollout (seeding one first) so `exec resume` carries it."""
        sid = ctx.resume_session
        if not sid or not self._rollout(ctx, sid):
            p = await self._spawn(ctx, self._cmd(ctx, "Reply with exactly: ready", None, hooks=False))
            out, err = await p.communicate()
            sid = next((json.loads(l)["thread_id"] for l in out.decode().splitlines()
                        if l.startswith("{") and '"thread.started"' in l), None)
            if not sid:
                raise RuntimeError(f"codex seed failed: {err.decode(errors='replace')[-500:]}")
        f = self._rollout(ctx, sid)
        if not f:
            raise RuntimeError(f"Codex session {sid} has no rollout file")
        transcript.write_codex(f, items)
        return sid
