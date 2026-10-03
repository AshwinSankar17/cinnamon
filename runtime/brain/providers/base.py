"""Provider adapter contract. A provider runs one turn and reports normalized events through ctx.emit."""
from __future__ import annotations

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
    name: str

    async def run_turn(self, ctx: TurnContext) -> TurnResult: ...

    async def interrupt(self, thread_id: str) -> None: ...
