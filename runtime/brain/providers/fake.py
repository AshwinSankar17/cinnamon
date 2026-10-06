"""A scripted provider for tests and demos. Reacts to keywords in the prompt.

  "edit"    -> writes notes.md in the worktree
  "submit"  -> runs `brain submit` equivalent through ctx.extra["submit"] with a tiny script
  "rm -rf X"-> asks the policy hook about `rm -rf X`; an always-ask class defers the turn
  anything  -> replies with one assistant.text
"""
from __future__ import annotations

import asyncio
import re
import uuid

from .base import TurnContext, TurnResult


class FakeProvider:
    """Keeps an in-memory "native session" per session id so transcript ingest and hydration can be tested.
    defer=False behaves like Codex/agy (a pending approval is a deny); FakeReplayProvider has no hydrate (agy)."""

    def __init__(self, name: str = "fake", defer: bool = True) -> None:
        self.name, self.supports_defer = name, defer
        self._pending: dict[str, dict] = {}  # thread_id -> deferred tool call
        self.native: dict[str, list[dict]] = {}
        self.prompts: list[str | None] = []
        self.hydrated: list[list[dict]] = []

    def native_items(self, ctx: TurnContext, session_id: str) -> list[dict] | None:
        return list(self.native[session_id]) if session_id in self.native else None

    async def hydrate(self, ctx: TurnContext, items: list[dict]) -> str:
        sid = ctx.resume_session or f"{self.name}-{uuid.uuid4().hex[:8]}"
        self.native.setdefault(sid, []).extend(items)
        self.hydrated.append(list(items))
        return sid

    async def interrupt(self, thread_id: str) -> None:
        return None

    async def _tool(self, ctx: TurnContext, tool: str, tool_input: dict, run) -> str:
        tid = f"toolu_{uuid.uuid4().hex[:12]}"
        decision = await ctx.pretool(tool, tool_input, tid)
        d = decision.get("decision")
        if d == "defer" and not self.supports_defer:
            d = "deny"  # what brain-hook tells Codex/agy
        if d == "defer":
            self._pending[ctx.thread_id] = {"tool": tool, "input": tool_input, "run": run, "id": tid}
            return "deferred"
        ctx.emit("tool.started", {"tool_use_id": tid, "tool": tool, "summary": tool_input.get("command", tool),
                                  "input_preview": str(tool_input)[:300]})
        if d == "deny":
            ctx.emit("tool.completed", {"tool_use_id": tid, "tool": tool, "ok": False,
                                        "output_preview": f"denied: {decision.get('reason', '')}"})
            return "denied"
        out = await run()
        ctx.emit("tool.completed", {"tool_use_id": tid, "tool": tool, "ok": True, "exit_code": 0,
                                    "output_preview": out})
        self._log(ctx, {"k": "call", "id": tid, "name": tool, "input": tool_input, "shell": tool_input.get("command")},
                  {"k": "result", "id": tid, "output": out, "error": False})
        return "ok"

    def _log(self, ctx: TurnContext, *items: dict) -> None:
        self.native.setdefault(ctx.extra["_sid"], []).extend(items)

    async def run_turn(self, ctx: TurnContext) -> TurnResult:
        sid = ctx.resume_session or f"{self.name}-{uuid.uuid4().hex[:8]}"
        ctx.extra["_sid"] = sid
        self.prompts.append(ctx.prompt)
        if ctx.prompt is not None:
            self._log(ctx, {"k": "user", "text": ctx.prompt})
        await asyncio.sleep(0)
        if ctx.prompt is None:  # resume a deferred call
            p = self._pending.pop(ctx.thread_id, None)
            if not p:
                return TurnResult(session_id=sid, stop_reason="end_turn", text="Nothing to resume.")
            r = await self._tool(ctx, p["tool"], p["input"], p["run"])
            text = "Done — the approved action ran." if r == "ok" else "Understood; I skipped that step."
            ctx.emit("assistant.text", {"text": text})
            self._log(ctx, {"k": "assistant", "text": text})
            return TurnResult(session_id=sid, stop_reason="end_turn", text=text,
                              usage={"input_tokens": 10, "output_tokens": 5})

        prompt = ctx.prompt
        if "edit" in prompt:
            async def run_edit():
                (ctx.cwd / "notes.md").write_text(f"# Notes\n\n{prompt}\n")
                return "wrote notes.md"
            await self._tool(ctx, "Write", {"file_path": str(ctx.cwd / "notes.md")}, run_edit)
        if "submit" in prompt and "submit" in ctx.extra:
            async def run_submit():
                job = await ctx.extra["submit"]("echo training; sleep 1; exit 0\n")
                return f"Submitted batch job {job}"
            await self._tool(ctx, "Bash", {"command": "brain submit train.sh"}, run_submit)
        m = re.search(r"(rm -rf \S+)", prompt)
        if m:
            target = m.group(1)

            async def run_rm():
                return f"removed {target.split()[-1]}"
            r = await self._tool(ctx, "Bash", {"command": target}, run_rm)
            if r == "deferred":
                return TurnResult(session_id=sid, stop_reason="tool_deferred", text="")
        text = f"Done: {prompt[:80]}"
        ctx.emit("assistant.text", {"text": text})
        self._log(ctx, {"k": "assistant", "text": text})
        return TurnResult(session_id=sid, stop_reason="end_turn", text=text,
                          usage={"input_tokens": 100, "output_tokens": 20})


class FakeReplayProvider(FakeProvider):
    hydrate = None  # like agy: Brain must replay missing history in the prompt
