"""brain-hook: the PreToolUse command hook every provider process runs before a tool call.

Reads the hook JSON from stdin, asks Brain over its Unix socket, prints the decision as Claude Code hook
output (Claude/Codex hookSpecificOutput, or agy's {decision, reason, overwrite}). If Brain is unreachable it falls back to the local policy and blocks always-ask actions (it cannot
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


PENDING = ("Waiting for the owner's approval on their phone. Do not retry and do not work around this; "
           "end your turn now and say what you were about to do.")
AGY_EDITS = {"write_to_file", "replace_file_content", "multi_replace_file_content", "sed_file", "notebook_edit"}


def _normalize(data: dict) -> tuple[str, dict, str | None, bool]:
    """(tool_name, tool_input, tool_use_id, is_agy) in the Claude vocabulary the policy understands."""
    if "toolCall" not in data:  # Claude Code and Codex share this contract
        tool, inp = data.get("tool_name") or "", data.get("tool_input") or {}
        if tool == "Bash" and isinstance(inp.get("command"), list):
            inp = {**inp, "command": " ".join(inp["command"])}
        return ("Edit" if tool == "apply_patch" else tool), inp, data.get("tool_use_id"), False
    tc = data.get("toolCall") or {}
    name, args = tc.get("name") or "", tc.get("args") or {}
    if name == "run_command":
        return "Bash", {"command": str(args.get("CommandLine", ""))}, None, True
    if name in AGY_EDITS:
        path = args.get("TargetFile") or args.get("AbsolutePath") or args.get("FilePath") or args.get("Path") or ""
        return "Write", {"file_path": str(path)}, None, True
    return name, args, None, True


def _out(decision: str, reason: str = "", updated_input: dict | None = None, agy: bool = False) -> None:
    if decision == "defer" and os.environ.get("BRAIN_PROVIDER", "claude") in ("codex", "agy"):
        decision, reason = "deny", PENDING  # no defer in these CLIs: Brain resumes with a prompt once decided
    if decision in ("", "none", "allow_default"):
        return  # no opinion: the provider's own permission settings decide (agy reads even `{}` as deny)
    if agy:
        out: dict = {"decision": decision}
        if reason:
            out["reason"] = reason
        if updated_input is not None and "command" in updated_input:
            out["overwrite"] = {"CommandLine": updated_input["command"]}
        sys.stdout.write(json.dumps(out))
        return
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
    tool, inp, tool_use_id, agy = _normalize(data)
    cwd = data.get("cwd") or next(iter(data.get("workspacePaths") or []), None)
    body = json.dumps({"thread_id": thread_id, "tool_name": tool, "tool_input": inp, "tool_use_id": tool_use_id,
                       "session_id": data.get("session_id") or data.get("conversationId"), "cwd": cwd})
    try:
        conn = _UnixHTTPConnection(sock)
        conn.request("POST", "/internal/hook", body=body,
                     headers={"Content-Type": "application/json", "X-Brain-Hook-Token": token})
        resp = conn.getresponse()
        payload = json.loads(resp.read() or b"{}")
        if resp.status != 200:
            raise RuntimeError(payload)
        _out(payload.get("decision", ""), payload.get("reason", ""), payload.get("updated_input"), agy)
        return 0
    except Exception as e:  # Brain down: decide locally, never defer
        try:
            from brain.policy import PolicyContext, classify
            d = classify(tool, inp, PolicyContext(profile=os.environ.get("BRAIN_PROFILE", "auto"), worktree=cwd))
            if d.verdict == "deny":
                _out("deny", d.reason, agy=agy)
            elif d.verdict == "ask":
                _out("deny", f"Brain is unreachable ({type(e).__name__}); '{d.reason}' needs the owner's approval. "
                             "Do not retry; end the turn and say what you wanted to do.", agy=agy)
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
