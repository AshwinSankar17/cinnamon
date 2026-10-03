"""brain-hook: the PreToolUse command hook every provider process runs before a tool call.

Reads the hook JSON from stdin, asks Brain over its Unix socket, prints the decision as Claude Code hook
output. If Brain is unreachable it falls back to the local policy and blocks always-ask actions (it cannot
record an approval, so it must not defer).
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import sys


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float = 140):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self._path)
        self.sock = s


def _out(decision: str, reason: str = "", updated_input: dict | None = None) -> None:
    if decision in ("", "none", "allow_default"):
        return  # no opinion: Claude's own permission mode decides
    hso: dict = {"hookEventName": "PreToolUse", "permissionDecision": decision}
    if reason:
        hso["permissionDecisionReason"] = reason
    if updated_input is not None:
        hso["updatedInput"] = updated_input
    sys.stdout.write(json.dumps({"hookSpecificOutput": hso}))


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    if data.get("hook_event_name", "PreToolUse") != "PreToolUse":
        return 0
    sock = os.environ.get("BRAIN_HOOK_SOCK", "")
    token = os.environ.get("BRAIN_HOOK_TOKEN", "")
    thread_id = os.environ.get("BRAIN_THREAD_ID", "")
    body = json.dumps({"thread_id": thread_id, "tool_name": data.get("tool_name"),
                       "tool_input": data.get("tool_input", {}), "tool_use_id": data.get("tool_use_id"),
                       "session_id": data.get("session_id"), "cwd": data.get("cwd")})
    try:
        conn = _UnixHTTPConnection(sock)
        conn.request("POST", "/internal/hook", body=body,
                     headers={"Content-Type": "application/json", "X-Brain-Hook-Token": token})
        resp = conn.getresponse()
        payload = json.loads(resp.read() or b"{}")
        if resp.status != 200:
            raise RuntimeError(payload)
        _out(payload.get("decision", ""), payload.get("reason", ""), payload.get("updated_input"))
        return 0
    except Exception as e:  # Brain down: decide locally, never defer
        try:
            from brain.policy import PolicyContext, classify
            d = classify(data.get("tool_name", ""), data.get("tool_input", {}),
                         PolicyContext(profile=os.environ.get("BRAIN_PROFILE", "auto"),
                                       worktree=data.get("cwd")))
            if d.verdict == "deny":
                _out("deny", d.reason)
            elif d.verdict == "ask":
                _out("deny", f"Brain is unreachable ({type(e).__name__}); '{d.reason}' needs the owner's approval. "
                             "Do not retry; end the turn and say what you wanted to do.")
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
