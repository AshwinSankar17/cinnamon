"""What each provider itself reports about remaining usage. Never estimated from Brain's own token counts.

Sources verified 2026-10-15 (spec §7.3): Claude's `get_usage` control request (no model turn), the rate limits in
Codex's own rollout files, and `agy -p /usage` (documented as no turn, no quota spent). Each probe runs in Brain's
own provider homes, with a timeout, and a failing probe reports itself without blocking the others.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

TTL_S, REFRESH_MIN_S, PROBE_TIMEOUT_S = 300, 60, 40  # agy /usage takes ~12 s cold
_cache: dict = {}
_lock = threading.Lock()


def _ts(v) -> float | None:
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def parse_claude(o: dict) -> list[dict]:
    if not o or o.get("rate_limits_available") is False:
        return []
    src = o.get("rate_limits") or o
    out = []
    for key, name in (("five_hour", "5 h"), ("seven_day", "week")):
        w = src.get(key) or {}
        if w.get("utilization") is not None:
            out.append({"name": name, "used_pct": w["utilization"], "resets_at": _ts(w.get("resets_at"))})
    return out


def parse_codex(o: dict) -> list[dict]:
    out = []
    for key in ("primary", "secondary"):
        w = (o or {}).get(key) or {}
        pct = w.get("used_percent", w.get("usedPercent"))
        if pct is None:
            continue
        mins = w.get("window_minutes") or w.get("windowDurationMins")
        out.append({"name": "5 h" if mins == 300 else "week" if mins == 10080 else f"{mins} min",
                    "used_pct": pct, "resets_at": _ts(w.get("resets_at") or w.get("resetsAt"))})
    return out


def parse_agy(o: dict) -> list[dict]:
    out = []
    for g in (o or {}).get("groups") or []:
        for b in g.get("buckets") or []:
            if b.get("remaining_fraction") is None:
                continue
            win = {"weekly": "week", "5h": "5 h"}.get(b.get("window"), b.get("window") or "")
            out.append({"name": f"{g.get('name')} · {win}", "used_pct": round((1 - b["remaining_fraction"]) * 100, 1),
                        "resets_at": _ts(b.get("reset_time"))})
    return out


def _run(args: list[str], env: dict, stdin: str | None = None) -> str:
    r = subprocess.run(args, input=stdin, capture_output=True, text=True, timeout=PROBE_TIMEOUT_S,
                       env={**os.environ, **env})
    return r.stdout


def _json_lines(out: str):
    for line in out.splitlines():
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _probe(brain, name: str) -> dict:
    binary = getattr(brain.providers.get(name), "binary", name)
    root = Path(brain.p.root)
    if name == "claude":
        req = json.dumps({"type": "control_request", "request_id": "q1",
                          "request": {"subtype": "get_usage", "skip_behaviors": True}})
        out = _run([binary, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"],
                   {"CLAUDE_CONFIG_DIR": str(brain.p.claude_config_dir)}, req + "\n")
        for m in _json_lines(out):
            if m.get("type") == "control_response":
                resp = m.get("response") or {}
                body = resp.get("response") if isinstance(resp.get("response"), dict) else resp
                return {"windows": parse_claude(body), "plan": body.get("subscription_type"), "source": "Claude's usage report"}
        return {"windows": [], "plan": None, "source": "Claude's usage report"}
    if name == "codex":
        files = sorted((root / "codex" / "sessions").glob("*/*/*/rollout-*.jsonl"), key=lambda x: x.stat().st_mtime,
                       reverse=True)
        for f in files[:5]:
            for line in reversed(f.read_text(errors="replace").splitlines()):
                if '"token_count"' in line and '"rate_limits"' in line:
                    rl = ((json.loads(line).get("payload") or {}).get("rate_limits")) or {}
                    when = time.strftime("%a %H:%M", time.localtime(f.stat().st_mtime))
                    return {"windows": parse_codex(rl), "plan": rl.get("plan_type"), "source": f"last Codex turn, {when}"}
        return {"windows": [], "plan": None, "source": "no Codex turn yet"}
    if name == "agy":
        out = _run([binary, "-p", "/usage", "--output-format", "stream-json", f"--app_data_dir={os.path.relpath(root / 'agy', Path.home() / '.gemini')}"], {})
        for m in _json_lines(out):
            body = (m.get("command") or {}).get("data") or (m.get("result") if isinstance(m.get("result"), dict) else m)
            if isinstance(body, dict) and body.get("groups"):
                return {"windows": parse_agy(body), "plan": None, "source": "agy /usage"}
        return {"windows": [], "plan": None, "source": "agy /usage"}
    return {"windows": [], "plan": None, "source": "not reported"}


def _one(brain, name: str) -> dict:
    try:
        r = _probe(brain, name)
        return {"name": name, "plan": r.get("plan"), "windows": r["windows"], "source": r["source"], "error": None}
    except Exception as e:  # a hung or missing CLI reports itself; it never blocks the others
        return {"name": name, "plan": None, "windows": [], "source": "", "error": str(e)[:200]}


def snapshot(brain, force: bool = False) -> dict:
    with _lock:
        age = time.time() - _cache.get("as_of", 0)
        if age < (REFRESH_MIN_S if force else TTL_S):  # "Ask again" probes at most once a minute (spec §5)
            return _cache["value"]
        names = [n for n in brain.providers if n in ("claude", "codex", "agy")] or list(brain.providers)
        with ThreadPoolExecutor(max_workers=max(1, len(names))) as ex:
            provs = list(ex.map(lambda n: _one(brain, n), names))
        value = {"as_of": time.time(), "providers": provs}
        _cache.update(as_of=value["as_of"], value=value)
        return value
