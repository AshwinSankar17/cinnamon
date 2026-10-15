"""Provider adapter contract. A provider runs one turn and reports normalized events through ctx.emit."""
from __future__ import annotations

import asyncio
import os
import signal
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

Emit = Callable[[str, dict[str, Any]], None]
PreTool = Callable[[str, dict[str, Any], str], Awaitable[dict[str, Any]]]


@dataclass
class TurnContext:
    thread_id: str
    cwd: Path
    prompt: str | None  # None = resume a deferred tool call without a new message
    resume_session: str | None
    env: dict[str, str]
    emit: Emit
    pretool: PreTool  # same decision path the Claude command hook uses (used by in-process providers)
    system_append: str = ""
    permission_mode: str = "auto"
    readonly: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class TurnResult:
    session_id: str | None = None
    stop_reason: str | None = None
    text: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    error: str | None = None
    rate_limited_until: float | None = None


class Provider(Protocol):
    """Required: name, run_turn, interrupt. Optional (looked up with getattr by Brain):

    supports_defer: bool             False -> a pending approval is a deny; Brain resumes with a prompt once decided
    native_items(ctx, session_id)    -> list of transcript items read from the native session (brain/transcript.py)
    hydrate(ctx, items)              -> session_id after appending items to ctx.resume_session (seeding one if None);
                                        absent -> Brain replays missing history verbatim in the next prompt
    on_approved(ctx, tool_name, tool_input)  called before resuming an approved call (e.g. add a permission rule)
    steer(thread_id, text) -> bool    deliver a message into the running turn, as the CLI itself does with a
                                        message typed mid-turn; absent or False -> Brain queues it for the next turn
    """
    name: str

    async def run_turn(self, ctx: TurnContext) -> TurnResult: ...

    async def interrupt(self, thread_id: str) -> None: ...


STOP_GRACE = 15.0  # seconds a CLI gets to honour its own interrupt before it is terminated


async def stop_process(proc: asyncio.subprocess.Process | None, grace: float | None = None) -> None:
    """Stop a provider CLI the way it stops itself: SIGINT, as Ctrl+C, so it aborts the turn and records it in its own
    session. Only if it ignores that (hung) is its whole process group, tool commands included, terminated."""
    if not proc or proc.returncode is not None:
        return
    proc.send_signal(signal.SIGINT)
    for sig, wait in ((None, STOP_GRACE if grace is None else grace), (signal.SIGTERM, 5.0), (signal.SIGKILL, 5.0)):
        if sig:
            kill_group(proc, sig)
        try:
            await asyncio.wait_for(proc.wait(), wait)
            return
        except asyncio.TimeoutError:
            continue


def kill_group(proc: asyncio.subprocess.Process, sig: int = signal.SIGKILL) -> None:
    """Signal the CLI and everything it started (providers are spawned with start_new_session=True)."""
    if proc.returncode is None:
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            pass
