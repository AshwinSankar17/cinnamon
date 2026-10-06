"""Provider-neutral, lossless thread transcript, and readers/writers for each CLI's native session format.

Items are plain dicts:
  {"k": "user" | "assistant", "text"}
  {"k": "call", "id", "name", "input", "shell"}      shell = the plain shell command when the call ran one
  {"k": "result", "id", "output", "error"}

Brain records user prompts itself and ingests everything else from the provider's native session file after
each turn. Switching provider hydrates the target's native session with the items it has not seen (Claude
and Codex: append to their JSONL session and resume natively; agy: replayed verbatim, see render_text).
Hidden reasoning (Claude thinking, OpenAI/Gemini reasoning) is provider-bound and is counted, not carried.

Formats verified against claude 2.1.289, codex-cli 0.153.4 and agy (spikes/handoff_spikes.py).
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(x.get("text", "") for x in content or [] if isinstance(x, dict))


def read_claude(path: Path) -> tuple[list[dict], int]:
    items, dropped = [], 0
    for line in path.read_text().splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        t, content = rec.get("type"), (rec.get("message") or {}).get("content")
        if t == "user" and not rec.get("isMeta"):
            if isinstance(content, str):
                items.append({"k": "user", "text": content})
                continue
            for b in content or []:
                if b.get("type") == "tool_result":
                    items.append({"k": "result", "id": b["tool_use_id"], "output": _text(b.get("content")),
                                  "error": bool(b.get("is_error"))})
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
                    dropped += 1
    return items, dropped


def read_codex(path: Path) -> tuple[list[dict], int]:
    items, dropped, execs = [], 0, []
    for line in path.read_text().splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        p = rec.get("payload") or {}
        pt, ri = p.get("type"), rec.get("type") == "response_item"
        if ri and pt == "message":
            text = _text(p.get("content"))
            kinds = (p.get("internal_chat_message_metadata_passthrough") or {}).get("content_item_kinds")
            if p.get("role") == "user" and (kinds == ["user.text"] or (kinds is None and not text.startswith("<"))):
                items.append({"k": "user", "text": text})
            elif p.get("role") == "assistant" and text.strip():
                items.append({"k": "assistant", "text": text})
        elif ri and pt == "function_call":
            try:
                args = json.loads(p.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"raw": p.get("arguments")}
            sh = args.get("command") if isinstance(args, dict) else None
            items.append({"k": "call", "id": p["call_id"], "name": p.get("name"), "input": args,
                          "shell": " ".join(sh) if isinstance(sh, list) else sh})
        elif ri and pt == "custom_tool_call":
            call = {"k": "call", "id": p["call_id"], "name": p.get("name"), "input": {"input": p.get("input")}, "shell": None}
            items.append(call)
            execs.append(call)
        elif ri and pt in ("function_call_output", "custom_tool_call_output"):
            o = p.get("output")
            call = next((c for c in items if c["k"] == "call" and c["id"] == p["call_id"]), None)
            items.append({"k": "result", "id": p["call_id"], "error": False,
                          "output": (call or {}).get("_shell_output") or (o if isinstance(o, str) else _text(o))})
        elif pt == "item_completed" and (p.get("item") or {}).get("type") == "CommandExecution":
            it = p["item"]
            call = next((c for c in reversed(execs) if c["shell"] is None), None)
            if call:  # Codex's `exec` tool wraps shell commands in JS; keep the plain command that ran and its output
                call["shell"] = " ".join(x.get("cmd", "") for x in it.get("parsed_cmd") or []) or " ".join(it.get("command") or [])
                call["_shell_output"] = it.get("aggregated_output")
        elif ri and pt == "reasoning":
            dropped += 1
    for c in items:
        c.pop("_shell_output", None)
    return items, dropped


def read_agy(path: Path) -> tuple[list[dict], int]:
    items, dropped, pending = [], 0, []
    for n, line in enumerate(path.read_text().splitlines()):
        try:
            st = json.loads(line)
        except json.JSONDecodeError:
            continue
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
        elif pending:
            call = pending.pop(0)
            out = next((st[k] for k in ("output", "content", "result", "error") if st.get(k)), None)
            items.append({"k": "result", "id": call["id"], "output": out if isinstance(out, str) else json.dumps(st),
                          "error": st.get("status") not in (None, "DONE")})
    return items, dropped


def _tool_id(x: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", x)


def _ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())


def write_codex(rollout: Path, items: list[dict]) -> int:
    """Append items to an existing Codex rollout as response_items. Returns lines written."""
    lines = rollout.read_text().splitlines()
    n = max((json.loads(l).get("ordinal", 0) for l in lines if l.strip()), default=0) + 1
    ts, open_calls, out = _ts(), set(), []

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
        elif k == "result":
            emit({"type": "message", "role": "user", "content": [{"type": "input_text", "text": f"[tool output]\n{it['output']}"}]})
    for cid in open_calls:
        emit({"type": "function_call_output", "call_id": cid, "output": "(no output recorded)"})
    if out:
        with rollout.open("a") as f:
            f.write("\n".join(out) + "\n")
    return len(out)


def write_claude(session: Path, items: list[dict], version: str) -> int:
    """Append items to an existing Claude Code session, chained from its last record. Returns records written."""
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
    for cid in open_calls:  # every tool_use needs a result before the next prompt
        add("user", {"type": "tool_result", "tool_use_id": _tool_id(cid), "content": "(no output recorded)", "is_error": False})
    if msgs and msgs[-1][0] == "user":
        add("assistant", {"type": "text", "text": "(history imported by Cluster Brain)"})
    parent, ts, lines = leaf["uuid"], _ts(), []
    for role, blocks in msgs:
        u = str(uuid.uuid4())
        rec = {"parentUuid": parent, "isSidechain": False, "type": role, "uuid": u, "timestamp": ts,
               "userType": "external", "entrypoint": "sdk-cli", "cwd": leaf.get("cwd"), "sessionId": leaf.get("sessionId"),
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
    if lines:
        with session.open("a") as f:
            f.write("\n".join(lines) + "\n")
    return len(lines)


REPLAY_PREAMBLE = ("You are taking over this thread from another coding agent. Below is the verbatim history you "
                   "have not seen yet, oldest first. Treat it as your own prior conversation.")


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
            parts.append(f"TOOL RESULT{' (error)' if it.get('error') else ''}:\n{it['output']}")
    return "\n\n".join(parts)


def replay_prompt(items: list[dict], prompt: str | None) -> str:
    return (f"{REPLAY_PREAMBLE}\n\n<history>\n{render_text(items)}\n</history>\n\n"
            f"{prompt if prompt is not None else 'Continue where the history leaves off.'}")
