"""Claude Code adapter: drives the installed `claude` CLI in print mode with stream-json output.

This is the same subprocess the Python Agent SDK spawns. Driving it directly keeps the deferred-approval
path identical to the documented one: a PreToolUse command hook (brain-hook) returns `defer`, the process
exits, and after the owner decides on the phone Brain runs `claude -p --resume <session>` with no prompt so
Claude re-attempts the deferred call; the hook then answers allow/deny from the stored decision.

Run spikes/spikes.py on the cluster first: it confirms the exact result fields your Claude Code version
emits for a deferred turn, which `_map_result` below reads defensively.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from .. import transcript
from .base import TurnContext, TurnResult, kill_group, stop_process

READ_TOOLS = {"Read", "Glob", "Grep", "LS", "WebFetch", "WebSearch"}
EDIT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def hook_settings(python: str | None = None) -> dict[str, Any]:
    py = python or sys.executable
    return {
        "hooks": {
            "PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": f"{py} -m brain.hook",
                                                        "timeout": 150}]}],
        },
        # Keep native transcripts for months so a quiet thread can still resume natively.
        "cleanupPeriodDays": 3650,
    }


def _summarize_input(tool: str, inp: dict) -> str:
    if tool == "Bash":
        return str(inp.get("command", ""))[:300]
    for k in ("file_path", "path", "pattern", "url", "query", "notebook_path"):
        if k in inp:
            return f"{k}={inp[k]}"[:300]
    return json.dumps(inp)[:300]


def _tool_result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(c.get("text", ""))
        return "\n".join(parts)
    return "" if content is None else str(content)


class ClaudeProvider:
    name = "claude"
    supports_defer = True

    def __init__(self, binary: str | None = None):
        self.binary = binary or os.environ.get("BRAIN_CLAUDE_BIN") or shutil.which("claude") or "claude"
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._version: str | None = None

    # ---- transcript ------------------------------------------------------------------------------
    def _session_file(self, ctx: TurnContext, sid: str) -> Path | None:
        root = Path(ctx.env.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
        return next(root.glob(f"*/{sid}.jsonl"), None)

    def native_items(self, ctx: TurnContext, session_id: str) -> list[dict] | None:
        f = self._session_file(ctx, session_id)
        return transcript.read_claude(f)[0] if f else None

    async def _run_quiet(self, ctx: TurnContext, *args: str) -> str:
        p = await asyncio.create_subprocess_exec(self.binary, *args, cwd=str(ctx.cwd), env={**os.environ, **ctx.env},
                                                 stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.PIPE)
        out, err = await p.communicate()
        if p.returncode:
            raise RuntimeError(f"claude {args[0]} failed: {err.decode(errors='replace')[-500:]}")
        return out.decode(errors="replace")

    async def hydrate(self, ctx: TurnContext, items: list[dict]) -> str:
        """Append foreign history to this thread's Claude session (seeding one first) so --resume carries it."""
        sid = ctx.resume_session
        if not sid or not self._session_file(ctx, sid):
            # ponytail: a one-line seed turn costs a tiny request; writing a session from scratch is unverified
            sid = json.loads(await self._run_quiet(ctx, "-p", "Reply with exactly: ready", "--output-format", "json"))["session_id"]
        if self._version is None:
            self._version = (await self._run_quiet(ctx, "--version")).split()[0]
        f = self._session_file(ctx, sid)
        if not f:
            raise RuntimeError(f"Claude session {sid} has no transcript file")
        transcript.write_claude(f, items, self._version)
        return sid

    async def interrupt(self, thread_id: str) -> None:
        await stop_process(self._procs.get(thread_id))  # SIGINT records the turn; escalates only if ignored

    def _cmd(self, ctx: TurnContext) -> list[str]:
        cmd = [self.binary, "-p"]
        if ctx.prompt is not None:
            cmd.append(ctx.prompt)
        cmd += ["--output-format", "stream-json", "--verbose",
                "--permission-mode", ctx.permission_mode,
                "--settings", json.dumps(hook_settings())]
        if ctx.system_append:
            cmd += ["--append-system-prompt", ctx.system_append]
        if ctx.resume_session:
            cmd += ["--resume", ctx.resume_session]
        return cmd

    async def run_turn(self, ctx: TurnContext) -> TurnResult:
        env = {**os.environ, **ctx.env, "CLAUDE_CODE_RESUME_INTERRUPTED_TURN": "1"}
        proc = await asyncio.create_subprocess_exec(
            *self._cmd(ctx), cwd=str(ctx.cwd), env=env,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024, start_new_session=True,
        )
        self._procs[ctx.thread_id] = proc
        res = TurnResult()
        tools: dict[str, str] = {}
        try:
            assert proc.stdout
            async for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._map(msg, ctx, res, tools)
            await proc.wait()
            if proc.returncode not in (0, None) and not res.stop_reason:
                err = (await proc.stderr.read()).decode("utf-8", "replace")[-2000:] if proc.stderr else ""
                res.error = err or f"claude exited {proc.returncode}"
        finally:
            self._procs.pop(ctx.thread_id, None)
            kill_group(proc)  # the turn was cancelled mid-stream: leave nothing running
        return res

    def _map(self, msg: dict, ctx: TurnContext, res: TurnResult, tools: dict[str, str]) -> None:
        t = msg.get("type")
        if msg.get("session_id"):
            res.session_id = msg["session_id"]
        if t == "assistant":
            m = msg.get("message", {})
            for block in m.get("content", []) or []:
                bt = block.get("type")
                if bt == "text" and block.get("text", "").strip():
                    ctx.emit("assistant.text", {"text": block["text"]})
                elif bt == "tool_use":
                    tools[block["id"]] = block.get("name", "?")
                    ctx.emit("tool.started", {"tool_use_id": block["id"], "tool": block.get("name"),
                                              "summary": _summarize_input(block.get("name", ""), block.get("input", {})),
                                              "input_preview": json.dumps(block.get("input", {}))[:1500]})
        elif t == "user":
            m = msg.get("message", {})
            content = m.get("content") if isinstance(m, dict) else None
            for block in content or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tid = block.get("tool_use_id", "")
                    text = _tool_result_text(block.get("content"))
                    ctx.emit("tool.completed", {"tool_use_id": tid, "tool": tools.get(tid, "?"),
                                                "ok": not block.get("is_error", False), "output_preview": text[-4000:]})
        elif t == "result":
            res.stop_reason = msg.get("stop_reason") or msg.get("terminal_reason") or msg.get("subtype")
            res.text = msg.get("result") or ""
            u = msg.get("usage") or {}
            res.usage = {"input_tokens": int(u.get("input_tokens", 0) or 0),
                         "output_tokens": int(u.get("output_tokens", 0) or 0)}
            if msg.get("is_error") and not res.error:
                res.error = res.text or msg.get("subtype")
        elif t == "system" and msg.get("subtype") == "api_retry":
            status = "rate_limited" if msg.get("error") == "rate_limit" else "retrying"
            ctx.emit("provider.status", {"provider": "claude", "status": status,
                                         "message": f"retry {msg.get('attempt')}/{msg.get('max_retries')}"})
        elif t == "rate_limit_event" or (t == "system" and msg.get("subtype") == "rate_limit"):
            info = msg.get("rate_limit_info") or msg
            if info.get("status") == "rejected":
                resets = info.get("resets_at") or info.get("resetsAt")
                try:
                    res.rate_limited_until = float(resets) if resets else time.time() + 3600
                except (TypeError, ValueError):
                    res.rate_limited_until = time.time() + 3600
                ctx.emit("provider.status", {"provider": "claude", "status": "rate_limited",
                                             "resets_at": res.rate_limited_until,
                                             "message": "Claude usage window exhausted"})


def tool_kind(tool: str) -> str:
    if tool in READ_TOOLS:
        return "reads"
    if tool in EDIT_TOOLS:
        return "edits"
    if tool == "Bash":
        return "commands"
    return "other"
