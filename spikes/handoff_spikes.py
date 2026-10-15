#!/usr/bin/env python3
"""Cluster Brain handoff spikes: can a thread move between Claude Code, Codex and Antigravity (agy) with its
full active context, and can Brain gate/reroute tool calls on each?

    spikes/.venv/bin/python spikes/handoff_spikes.py                 # everything
    spikes/.venv/bin/python spikes/handoff_spikes.py --only X1,X5    # a subset

Uses your logged-in profiles (default `ash`: ~/.claude-profiles/ash, ~/.codex-profiles/ash,
~/.gemini/antigravity-cli-profiles/ash) only for their credentials, via symlinks into Brain-owned homes:
~/.brain/providers/{claude,codex}-spike and ~/.gemini/antigravity-cli-profiles/brain-spike. Your own
sessions and settings are never written. Workspaces are throwaway git repos in $TMPDIR.

Context tests plant a needle in a file, have the source CLI `cat` it, delete the file, move the history into
the target CLI, and ask for the needle with tools forbidden. The needle never appears in any prompt.

X1  Claude -> Codex   native: append Claude's history to a seeded Codex rollout, `codex exec resume`
X2  Codex  -> Claude  native: append Codex's history to a seeded Claude session, `claude -p --resume`
X3a Claude -> agy     replay: agy has no writable session format, so the verbatim transcript is the first prompt
X3b agy    -> Codex   native: read agy's transcript_full.jsonl, append to a seeded Codex rollout
X4  Codex hooks       reroute via updatedInput; deny-while-pending; approve -> resume runs it
X5  agy, approach (b) settings.json permissions.allow + hook (DEFAULT): reroute, hook-deny beats allow,
                      approval = add an allow rule + resume
X6  agy, approach (a) --dangerously-skip-permissions with the hook as the only gate (FALLBACK)

Writes raw output per run and handoff-out/report.json next to this file.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

HOME = Path.home()
OUT = Path(__file__).resolve().parent / "handoff-out"
PROV = HOME / ".brain" / "providers"
AGY_ROOT = HOME / ".gemini" / "antigravity-cli-profiles"
AGY_APP = "antigravity-cli-profiles/brain-spike"  # agy resolves --app_data_dir relative to ~/.gemini
REROUTED = "echo REROUTED-BY-BRAIN; hostname"
RECALL = ("Do not run any tools or read any files. Earlier in this conversation a file named secret.txt was "
          "printed. Reply with its exact contents and nothing else.")

HOOK = r'''#!/usr/bin/env python3
"""Spike stand-in for brain-hook: speaks both the Codex (Claude-style) and agy PreToolUse contracts."""
import json, os, sys
d = json.load(sys.stdin)
here = os.path.dirname(os.path.abspath(__file__))
open(os.path.join(here, "hook.log"), "a").write(json.dumps(d) + "\n")
agy = "toolCall" in d
if agy:
    cmd = str(((d.get("toolCall") or {}).get("args") or {}).get("CommandLine", ""))
else:
    cmd = (d.get("tool_input") or {}).get("command", "")
    cmd = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
state = os.path.join(here, ".decision")
approved = os.path.exists(state) and open(state).read().strip() == "allow"
PENDING = "Waiting for the owner's approval on their phone. Do not retry or work around this; end your turn now."
if "denied.flag" in cmd or ("approved.flag" in cmd and not approved):
    out = {"decision": "deny", "reason": PENDING} if agy else {"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": PENDING}}
elif "approved.flag" in cmd:
    out = {"decision": "allow", "reason": "approved on phone"} if agy else {"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "allow", "permissionDecisionReason": "approved on phone"}}
elif "gpu_probe.py" in cmd:
    new = "''' + REROUTED + r'''"
    out = {"decision": "allow", "overwrite": {"CommandLine": new}} if agy else {"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "allow",
        "updatedInput": {**(d.get("tool_input") or {}), "command": new}}}
else:
    out = None  # no opinion: print nothing (agy reads a bare {} as deny)
if out is not None:
    print(json.dumps(out))
'''


# ---- plumbing ------------------------------------------------------------------------------------------
def run(name: str, cmd: list[str], cwd: Path, env: dict | None = None, timeout: int = 360) -> dict:
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=cwd, env={**os.environ, **(env or {})}, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=timeout)
        rc, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        rc, out, err = "timeout", (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""), \
            (e.stderr or b"").decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
    (OUT / f"{name}.out").write_text(out)
    (OUT / f"{name}.err").write_text(err)
    msgs = []
    for line in out.splitlines():
        try:
            msgs.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return {"rc": rc, "out": out, "err": err[-1500:], "msgs": msgs, "secs": round(time.time() - t0, 1)}


def workspace(tag: str, secret: str | None = None) -> Path:
    ws = Path(tempfile.mkdtemp(prefix=f"brain-handoff-{tag}-"))
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    if secret:
        (ws / "secret.txt").write_text(secret + "\n")
    return ws


def needle() -> str:
    return f"NEEDLE-{random.randint(10000, 99999)}-{random.choice(['OTTER', 'MAPLE', 'COBALT', 'FJORD'])}"


def link(src: Path, dst: Path) -> None:
    if not src.exists():
        raise SystemExit(f"missing credential file {src}: log that profile in first")
    if dst.is_symlink() or dst.exists():
        dst.unlink()
    dst.symlink_to(src)


# ---- provider runners ----------------------------------------------------------------------------------
class Claude:
    def __init__(self, profile: str):
        self.home = PROV / "claude-spike"
        self.home.mkdir(parents=True, exist_ok=True)
        link(HOME / ".claude-profiles" / profile / ".credentials.json", self.home / ".credentials.json")
        self.env = {"CLAUDE_CONFIG_DIR": str(self.home)}
        self.version = subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.split()[0]

    def turn(self, name, ws, prompt, resume=None, allow=None) -> dict:
        cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose"]
        cmd += ["--resume", resume] if resume else []
        cmd += ["--allowedTools", allow] if allow else []
        r = run(name, cmd, ws, self.env)
        res = next((m for m in reversed(r["msgs"]) if m.get("type") == "result"), {})
        r["session_id"] = res.get("session_id")
        r["text"] = res.get("result") or ""
        r["tool_uses"] = sum(1 for m in r["msgs"] if m.get("type") == "assistant"
                             for b in (m.get("message") or {}).get("content") or [] if b.get("type") == "tool_use")
        return r

    def session_file(self, sid: str) -> Path:
        return next((self.home / "projects").glob(f"*/{sid}.jsonl"))


class Codex:
    def __init__(self, profile: str):
        self.home = PROV / "codex-spike"
        self.home.mkdir(parents=True, exist_ok=True)
        link(HOME / ".codex-profiles" / profile / "auth.json", self.home / "auth.json")
        self.env = {"CODEX_HOME": str(self.home)}

    def turn(self, name, ws, prompt, resume=None, hooks=False) -> dict:
        cmd = ["codex", "exec", "--json", "--skip-git-repo-check", "-s", "workspace-write"]
        cmd += ["--dangerously-bypass-hook-trust"] if hooks else []
        cmd += ["resume", resume, prompt] if resume else [prompt]
        r = run(name, cmd, ws, self.env)
        r["thread_id"] = resume or next((m.get("thread_id") for m in r["msgs"] if m.get("type") == "thread.started"), None)
        items = [m.get("item") or {} for m in r["msgs"] if m.get("type") == "item.completed"]
        r["text"] = "\n".join(i.get("text", "") for i in items if i.get("type") == "agent_message")
        r["commands"] = [{"command": i.get("command"), "output": i.get("aggregated_output"), "exit": i.get("exit_code")}
                         for i in items if i.get("type") == "command_execution"]
        return r

    def rollout(self, tid: str) -> Path:
        return next((self.home / "sessions").rglob(f"*{tid}.jsonl"))


class Agy:
    def __init__(self, profile: str):
        self.home = AGY_ROOT / "brain-spike"
        self.home.mkdir(parents=True, exist_ok=True)
        self.home.chmod(0o700)
        link(AGY_ROOT / profile / "antigravity-oauth-token", self.home / "antigravity-oauth-token")

    def settings(self, allow: list[str], trusted: list[Path]) -> None:
        (self.home / "settings.json").write_text(json.dumps(
            {"trustedWorkspaces": [str(p) for p in trusted], "permissions": {"allow": allow}}, indent=2))

    def turn(self, name, ws, prompt, resume=None, skip=False) -> dict:
        cmd = ["agy", f"--app_data_dir={AGY_APP}", "-p", prompt, "--output-format", "stream-json", "--print-timeout", "5m"]
        cmd += ["--conversation", resume] if resume else []
        cmd += ["--dangerously-skip-permissions"] if skip else []
        r = run(name, cmd, ws)
        res = next((m["result"] for m in reversed(r["msgs"]) if m.get("event") == "result"), {})
        r["conversation_id"] = res.get("conversation_id") or resume or next(
            (m.get("conversation_id") for m in r["msgs"] if m.get("event") == "init"), None)
        r["text"] = res.get("response") or ""
        r["status"], r["denied"] = res.get("status"), res.get("denied_actions")
        r["tool_steps"] = [(m["step_update"].get("tool_name"), m["step_update"].get("state")) for m in r["msgs"]
                           if m.get("event") == "step_update" and m["step_update"].get("step_type") == "tool"
                           and m["step_update"].get("state") != "ACTIVE"]
        tp = self.transcript(r["conversation_id"]) if r["conversation_id"] else None
        r["transcript"] = tp.read_text() if tp and tp.exists() else ""
        return r

    @staticmethod
    def steps(out: Path) -> list[str]:
        rows = []
        for line in out.read_text().splitlines():
            try:
                s = json.loads(line).get("step_update") or {}
            except json.JSONDecodeError:
                continue
            if s.get("step_type") == "tool" and s.get("state") != "ACTIVE":
                ti = s.get("tool_info") or {}
                p = ti.get("parameters") or {}
                rows.append(f"{s.get('tool_name')} {s.get('state')} {p.get('CommandLine') or p.get('AbsolutePath') or ''} "
                            f"{(ti.get('error') or {}).get('message', '')[:80]}".strip())
        return rows

    def transcript(self, cid: str) -> Path:
        return self.home / "brain" / cid / ".system_generated" / "logs" / "transcript_full.jsonl"


# ---- neutral transcript: [{"k": user|assistant, "text"} | {"k": call, id, name, input, shell} | {"k": result, id, output, error}]
def read_claude(path: Path) -> tuple[list[dict], int]:
    items, dropped = [], 0
    for line in path.read_text().splitlines():
        rec = json.loads(line)
        t, msg = rec.get("type"), rec.get("message") or {}
        content = msg.get("content")
        if t == "user" and not rec.get("isMeta"):
            if isinstance(content, str):
                items.append({"k": "user", "text": content})
                continue
            for b in content or []:
                if b.get("type") == "tool_result":
                    c = b.get("content")
                    text = c if isinstance(c, str) else "\n".join(x.get("text", "") for x in c or [] if isinstance(x, dict))
                    items.append({"k": "result", "id": b["tool_use_id"], "output": text, "error": bool(b.get("is_error"))})
                elif b.get("type") == "text":
                    items.append({"k": "user", "text": b["text"]})
        elif t == "assistant":
            for b in content or []:
                if b.get("type") == "text" and b["text"].strip():
                    items.append({"k": "assistant", "text": b["text"]})
                elif b.get("type") == "tool_use":
                    inp = b.get("input") or {}
                    items.append({"k": "call", "id": b["id"], "name": b["name"], "input": inp,
                                  "shell": inp.get("command") if b["name"] == "Bash" else None})
                elif b.get("type") in ("thinking", "redacted_thinking"):
                    dropped += 1  # signed to Anthropic; not portable
    return items, dropped


def read_codex(path: Path) -> tuple[list[dict], int]:
    items, dropped, execs = [], 0, []
    for line in path.read_text().splitlines():
        rec = json.loads(line)
        p = rec.get("payload") or {}
        pt = p.get("type")
        if rec.get("type") == "response_item" and pt == "message":
            text = "\n".join(c.get("text", "") for c in p.get("content") or [])
            kinds = (p.get("internal_chat_message_metadata_passthrough") or {}).get("content_item_kinds")
            if p.get("role") == "user" and (kinds == ["user.text"] or (kinds is None and not text.startswith("<"))):
                items.append({"k": "user", "text": text})
            elif p.get("role") == "assistant" and text.strip():
                items.append({"k": "assistant", "text": text})
        elif rec.get("type") == "response_item" and pt == "function_call":
            try:
                args = json.loads(p.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"raw": p.get("arguments")}
            sh = args.get("command") if isinstance(args, dict) else None
            items.append({"k": "call", "id": p["call_id"], "name": p.get("name"), "input": args,
                          "shell": " ".join(sh) if isinstance(sh, list) else sh})
        elif rec.get("type") == "response_item" and pt == "custom_tool_call":
            call = {"k": "call", "id": p["call_id"], "name": p.get("name"), "input": {"input": p.get("input")}, "shell": None}
            items.append(call)
            execs.append(call)
        elif rec.get("type") == "response_item" and pt in ("function_call_output", "custom_tool_call_output"):
            o = p.get("output")
            text = o if isinstance(o, str) else "\n".join(x.get("text", "") for x in o or [] if isinstance(x, dict))
            call = next((c for c in items if c["k"] == "call" and c["id"] == p["call_id"]), None)
            items.append({"k": "result", "id": p["call_id"], "output": (call or {}).get("shell_output") or text, "error": False})
        elif pt == "item_completed" and (p.get("item") or {}).get("type") == "CommandExecution":
            it = p["item"]
            call = next((c for c in reversed(execs) if c["shell"] is None), None)
            if call:  # Codex's `exec` tool wraps shell commands in JS; recover the plain command and its output
                call["shell"] = " ".join(x.get("cmd", "") for x in it.get("parsed_cmd") or []) or " ".join(it.get("command") or [])
                call["shell_output"] = it.get("aggregated_output")
        elif rec.get("type") == "response_item" and pt == "reasoning":
            dropped += 1  # encrypted for OpenAI; not portable
    for c in items:
        c.pop("shell_output", None)
    return items, dropped


def read_agy(path: Path) -> tuple[list[dict], int]:
    items, dropped, pending = [], 0, []
    for n, line in enumerate(path.read_text().splitlines()):
        st = json.loads(line)
        typ, content = st.get("type", ""), st.get("content")
        if typ == "USER_INPUT":
            m = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", content or "", re.S)
            items.append({"k": "user", "text": m.group(1) if m else (content or "")})
        elif typ == "PLANNER_RESPONSE":  # tool outputs are GENERIC steps, also marked source=MODEL
            if st.get("thinking"):
                dropped += 1
            if isinstance(content, str) and content.strip():
                items.append({"k": "assistant", "text": content})
            for i, tc in enumerate(st.get("tool_calls") or []):
                args = tc.get("args") or {}
                call = {"k": "call", "id": f"agy_{st.get('step_index', n)}_{i}", "name": tc.get("name"), "input": args,
                        "shell": args.get("CommandLine") if tc.get("name") == "run_command" else None}
                items.append(call)
                pending.append(call)
        elif pending:  # a tool step: attach its output to the oldest unanswered call
            call = pending.pop(0)
            out = next((st[k] for k in ("output", "content", "result", "error") if st.get(k)), None)
            items.append({"k": "result", "id": call["id"], "output": out if isinstance(out, str) else json.dumps(st),
                          "error": st.get("status") not in (None, "DONE")})
    return items, dropped


def _tool_id(x: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", x)


def write_codex(rollout: Path, items: list[dict]) -> int:
    lines = rollout.read_text().splitlines()
    n = max(json.loads(l).get("ordinal", 0) for l in lines) + 1
    ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    open_calls, out = set(), []

    def emit(p):
        nonlocal n
        out.append(json.dumps({"timestamp": ts, "ordinal": n, "type": "response_item", "payload": p}))
        n += 1

    for it in items:
        k = it["k"]
        if k == "user":
            emit({"type": "message", "role": "user", "content": [{"type": "input_text", "text": it["text"]}]})
        elif k == "assistant":
            emit({"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": it["text"]}]})
        elif k == "call":
            args = {"command": it["shell"]} if it.get("shell") else it["input"]
            emit({"type": "function_call", "name": "Bash" if it.get("shell") else it["name"],
                  "arguments": json.dumps(args), "call_id": it["id"]})
            open_calls.add(it["id"])
        elif k == "result" and it["id"] in open_calls:
            emit({"type": "function_call_output", "call_id": it["id"], "output": it["output"]})
            open_calls.discard(it["id"])
    for cid in open_calls:
        emit({"type": "function_call_output", "call_id": cid, "output": "(no output recorded)"})
    with rollout.open("a") as f:
        f.write("\n".join(out) + "\n")
    return len(out)


def write_claude(session: Path, items: list[dict], version: str) -> int:
    recs = [json.loads(l) for l in session.read_text().splitlines() if l.strip()]
    leaf = [r for r in recs if r.get("uuid")][-1]
    msgs: list[tuple[str, list]] = []

    def add(role, block):
        if msgs and msgs[-1][0] == role:
            msgs[-1][1].append(block)
        else:
            msgs.append((role, [block]))

    open_calls: set[str] = set()
    for it in items:
        k = it["k"]
        if k in ("user", "assistant"):
            add(k, {"type": "text", "text": it["text"]})
        elif k == "call" and it.get("shell"):
            add("assistant", {"type": "tool_use", "id": _tool_id(it["id"]), "name": "Bash", "input": {"command": it["shell"]}})
            open_calls.add(it["id"])
        elif k == "call":  # no Claude equivalent: keep it verbatim as text rather than invent a tool
            add("assistant", {"type": "text", "text": f"[ran tool {it['name']} with input {json.dumps(it['input'])}]"})
        elif k == "result" and it["id"] in open_calls:
            add("user", {"type": "tool_result", "tool_use_id": _tool_id(it["id"]), "content": it["output"],
                         "is_error": it.get("error", False)})
            open_calls.discard(it["id"])
        elif k == "result":
            add("user", {"type": "text", "text": f"[tool output]\n{it['output']}"})
    if msgs and msgs[-1][0] == "user":
        add("assistant", {"type": "text", "text": "(history imported by Cluster Brain)"})
    parent, ts, lines = leaf["uuid"], time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()), []
    for role, blocks in msgs:
        u = str(uuid.uuid4())
        rec = {"parentUuid": parent, "isSidechain": False, "type": role, "uuid": u, "timestamp": ts,
               "userType": "external", "entrypoint": "sdk-cli", "cwd": leaf["cwd"], "sessionId": leaf["sessionId"],
               "version": version}
        if role == "user":
            rec["message"] = {"role": "user", "content": blocks}
        else:
            rec["message"] = {"id": f"msg_brain_{u.replace('-', '')}", "type": "message", "role": "assistant",
                              "model": "<synthetic>", "content": blocks,
                              "stop_reason": "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn",
                              "stop_sequence": None, "usage": {"input_tokens": 0, "output_tokens": 0}}
        lines.append(json.dumps(rec))
        parent = u
    with session.open("a") as f:
        f.write("\n".join(lines) + "\n")
    return len(lines)


def render_text(items: list[dict]) -> str:
    parts = []
    for it in items:
        k = it["k"]
        if k == "user":
            parts.append(f"USER:\n{it['text']}")
        elif k == "assistant":
            parts.append(f"ASSISTANT:\n{it['text']}")
        elif k == "call":
            parts.append(f"ASSISTANT TOOL CALL ({it['name']}): " + (f"$ {it['shell']}" if it.get("shell") else json.dumps(it["input"])))
        elif k == "result":
            parts.append(f"TOOL RESULT:\n{it['output']}")
    return "\n\n".join(parts)


def has_needle(items, n) -> bool:
    return any(n in (it.get("output") or "") for it in items if it["k"] == "result")


# ---- tests ---------------------------------------------------------------------------------------------
class Spikes:
    def __init__(self, profile: str):
        self.claude, self.codex, self.agy = Claude(profile), Codex(profile), Agy(profile)
        self.cache: dict = {}

    def claude_history(self) -> dict:
        if "claude" not in self.cache:
            n, ws = needle(), workspace("claude-src", None)
            (ws / "secret.txt").write_text(n + "\n")
            r = self.claude.turn("claude-src", ws, "Use the Bash tool to run: cat secret.txt  Then reply with just: noted",
                                 allow="Bash(cat secret.txt)")
            (ws / "secret.txt").unlink()
            items, dropped = read_claude(self.claude.session_file(r["session_id"]))
            self.cache["claude"] = {"needle": n, "ws": ws, "items": items, "dropped": dropped, "src_tool_uses": r["tool_uses"]}
        return self.cache["claude"]

    def recall_codex(self, tag, ws, items, n) -> dict:
        seed = self.codex.turn(f"{tag}-codex-seed", ws, "Reply with exactly: ready")
        rollout = self.codex.rollout(seed["thread_id"])
        appended = write_codex(rollout, items)
        r = self.codex.turn(f"{tag}-codex-recall", ws, RECALL, resume=seed["thread_id"])
        return {"pass": n in r["text"] and not r["commands"], "answer": r["text"][-300:], "tools_used": len(r["commands"]),
                "appended_items": appended, "rollout": str(rollout), "rc": r["rc"], "stderr": r["err"][-400:]}

    def X1(self):
        h = self.claude_history()
        return {"needle_in_history": has_needle(h["items"], h["needle"]), "history_items": len(h["items"]),
                "non_portable_dropped": h["dropped"], **self.recall_codex("X1", h["ws"], h["items"], h["needle"])}

    def X2(self):
        n, ws = needle(), workspace("codex-src")
        (ws / "secret.txt").write_text(n + "\n")
        src = self.codex.turn("X2-codex-src", ws, "Run the shell command: cat secret.txt  Then reply with just: noted")
        (ws / "secret.txt").unlink()
        items, dropped = read_codex(self.codex.rollout(src["thread_id"]))
        seed = self.claude.turn("X2-claude-seed", ws, "Reply with exactly: ready")
        sess = self.claude.session_file(seed["session_id"])
        appended = write_claude(sess, items, self.claude.version)
        r = self.claude.turn("X2-claude-recall", ws, RECALL, resume=seed["session_id"])
        return {"pass": n in r["text"] and r["tool_uses"] == 0, "needle_in_history": has_needle(items, n),
                "history_items": len(items), "non_portable_dropped": dropped, "appended_records": appended,
                "answer": r["text"][-300:], "tools_used": r["tool_uses"], "session": str(sess), "stderr": r["err"][-400:]}

    def X3a(self):
        h = self.claude_history()
        self.agy.settings([], [h["ws"]])
        intro = ("You are taking over this thread from another coding agent. Below is the verbatim history so far, "
                 "oldest first. Treat it as your own prior conversation.\n\n<history>\n"
                 + render_text(h["items"]) + "\n</history>\n\nReply with just: loaded")
        a = self.agy.turn("X3a-agy-load", h["ws"], intro)
        r = self.agy.turn("X3a-agy-recall", h["ws"], RECALL, resume=a["conversation_id"])
        return {"pass": h["needle"] in r["text"] and not r["tool_steps"], "answer": r["text"][-300:],
                "tools_used": r["tool_steps"], "load_status": a["status"], "conversation_id": a["conversation_id"],
                "stderr": r["err"][-400:]}

    def X3b(self):
        n, ws = needle(), workspace("agy-src")
        (ws / "secret.txt").write_text(n + "\n")
        self.agy.settings(["command(cat secret.txt)"], [ws])  # approach (b): a plain allow rule
        src = self.agy.turn("X3b-agy-src", ws, "Use the run_command tool to run: cat secret.txt  Then reply with just: noted")
        (ws / "secret.txt").unlink()
        tp = self.agy.transcript(src["conversation_id"])
        if tp.exists():
            shutil.copy(tp, OUT / "X3b-agy-transcript_full.jsonl")
        items, dropped = read_agy(tp) if tp.exists() else ([], 0)
        res = {"source_ran_cat": n in src["transcript"] or n in src["out"], "source_denied": src["denied"],
               "needle_in_history": has_needle(items, n), "history_items": len(items), "non_portable_dropped": dropped}
        if not items:
            return {"pass": False, "note": "no agy transcript to read", **res}
        return {**res, **self.recall_codex("X3b", ws, items, n)}

    def _hook_ws(self, tag, codex=False, agy=False) -> Path:
        ws = workspace(tag)
        (ws / "hook.py").write_text(HOOK)
        hook = {"type": "command", "command": f"{sys.executable} {ws / 'hook.py'}", "timeout": 60}
        if codex:
            (ws / ".codex").mkdir()
            (ws / ".codex" / "hooks.json").write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [hook]}]}}))
        if agy:
            (ws / ".agents").mkdir()
            (ws / ".agents" / "hooks.json").write_text(json.dumps({"brain": {"PreToolUse": [{"matcher": "*", "hooks": [hook]}]}}))
        return ws

    def X4(self):
        ws = self._hook_ws("codex-hooks", codex=True)
        rr = self.codex.turn("X4-reroute", ws, "Use the shell to run exactly: python gpu_probe.py", hooks=True)
        rerouted = any("REROUTED-BY-BRAIN" in (c["output"] or "") for c in rr["commands"])
        pend = self.codex.turn("X4-pending", ws, "Use the shell to run exactly: touch approved.flag", hooks=True)
        held = not (ws / "approved.flag").exists()
        (ws / ".decision").write_text("allow")
        ap = self.codex.turn("X4-approved", ws, "The owner approved the command on their phone. Run it now.",
                             resume=pend["thread_id"], hooks=True)
        ran = (ws / "approved.flag").exists()
        calls = len((ws / "hook.log").read_text().splitlines()) if (ws / "hook.log").exists() else 0
        return {"pass": rerouted and held and ran, "reroute": rerouted, "held_while_pending": held,
                "ran_after_approval": ran, "hook_calls": calls, "reroute_cmds": rr["commands"][-2:],
                "approved_reply": ap["text"][-300:], "stderr": ap["err"][-400:]}

    def _agy_reroute_and_gates(self, tag, skip: bool):
        ws = self._hook_ws(tag, agy=True)
        # (b) needs a base allow list mirroring Brain's auto policy: in headless mode any unlisted tool call ends the turn
        base = [] if skip else ["read_file(/)", "command(ls)", "command(pwd)", "command(cat)",
                                "command(python gpu_probe.py)", "command(touch denied.flag)"]
        self.agy.settings(base, [ws])
        res = {}
        rr = self.agy.turn(f"{tag}-reroute", ws, "Use the run_command tool to run exactly: python gpu_probe.py", skip=skip)
        res["reroute"] = "REROUTED-BY-BRAIN" in rr["transcript"] or "REROUTED-BY-BRAIN" in rr["out"]
        res["reroute_denied"] = rr["denied"]
        if not res["reroute"] and not skip:  # which rule does the rewritten (compound) command need?
            for label, extra in (("exact_string", [f"command({REROUTED})"]), ("each_subcommand", ["command(echo)", "command(hostname)"])):
                self.agy.settings(base + extra, [ws])
                r2 = self.agy.turn(f"{tag}-reroute-{label}", ws, "Use the run_command tool to run exactly: python gpu_probe.py")
                res[f"reroute_with_rule_{label}"] = "REROUTED-BY-BRAIN" in r2["transcript"] or "REROUTED-BY-BRAIN" in r2["out"]
            self.agy.settings(base, [ws])
        self.agy.turn(f"{tag}-hookdeny", ws, "Use the run_command tool to run exactly: touch denied.flag", skip=skip)
        res["hook_deny_held"] = not (ws / "denied.flag").exists()
        pend = self.agy.turn(f"{tag}-pending", ws, "Use the run_command tool to run exactly: touch approved.flag", skip=skip)
        res["held_while_pending"] = not (ws / "approved.flag").exists()
        (ws / ".decision").write_text("allow")
        if not skip:
            self.agy.settings(base + ["command(touch approved.flag)"], [ws])  # Brain records the approval as a rule
        ap = self.agy.turn(f"{tag}-approved", ws, "The owner approved the command on their phone. Run it now.",
                           resume=pend["conversation_id"], skip=skip)
        res["ran_after_approval"] = (ws / "approved.flag").exists()
        res["approved_denied"] = ap["denied"]
        res["hook_calls"] = len((ws / "hook.log").read_text().splitlines()) if (ws / "hook.log").exists() else 0
        res["tool_steps"] = {p.stem: Agy.steps(p) for p in sorted(OUT.glob(f"{tag}-*.out"))}
        res["no_opinion_denied_by_hook"] =sum(p.read_text().count("denied by pre-tool hook") for p in OUT.glob(f"{tag}-*.out"))
        reroute_ok = res["reroute"] or any(v for k, v in res.items() if k.startswith("reroute_with_rule_"))
        res["pass"] = reroute_ok and res["hook_deny_held"] and res["held_while_pending"] and res["ran_after_approval"]
        return res

    def X5(self):
        return self._agy_reroute_and_gates("X5-agy-b", skip=False)

    def X6(self):
        return self._agy_reroute_and_gates("X6-agy-a", skip=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="ash")
    ap.add_argument("--only", help="comma list, e.g. X1,X3b,X5")
    a = ap.parse_args()
    for b in ("claude", "codex", "agy"):
        if not shutil.which(b):
            print(f"{b} not on PATH")
            return 2
    OUT.mkdir(exist_ok=True)
    s = Spikes(a.profile)
    names = a.only.split(",") if a.only else ["X1", "X2", "X3a", "X3b", "X4", "X5", "X6"]
    report = {"profile": a.profile, "claude": s.claude.version, "tests": {}}
    for n in names:
        print(f"  running {n} ...", flush=True)
        try:
            report["tests"][n] = getattr(s, n)()
        except Exception as e:  # keep going; one broken test must not hide the others
            report["tests"][n] = {"pass": False, "error": f"{type(e).__name__}: {e}"}
        (OUT / "report.json").write_text(json.dumps(report, indent=2, default=str))
    print()
    for n, v in report["tests"].items():
        extra = v.get("error") or v.get("answer", "")
        print(f"  {'PASS' if v.get('pass') else 'FAIL'}  {n}  {str(extra)[:90]!r}")
    print(f"\nRaw output in {OUT}")
    return 0 if all(v.get("pass") for v in report["tests"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
