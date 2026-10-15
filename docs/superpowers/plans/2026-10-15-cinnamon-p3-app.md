# Cinnamon P3: the app redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Cinnamon redesign in the real app, on both PWA and native:
- a standup home with the cinnamon roll, its swirl drawn from the thread's real history;
- five worlds (Bakery, Forest, Rustic, Space, Tide), each with day and night, its own scene, thread presenters, waiting card, companion, type and motion;
- a ☰ menu in the bottom tray: World, Companion, Quota, Rules, Quiet hours, Connection, This phone (sign out), plus Threads, Jobs, Import and New;
- the unrolled-history sheet.

**Architecture:**
- **New screens and components** read a `useWorld()` context: tokens, fonts, scene, companion. Existing deep screens (chat, approval, listening, files) keep their current look and are reached from the standup and menu.
- **Navigation:** the tab bar goes away. `/` is the standup. Threads and Jobs become stack screens. The old Inbox list moves to `/inbox` for "N more waiting".
- **Backend additions** (pytest-driven): activity points for the roll, provider quota snapshots, a rules list with a quiet-hours switch, and self sign-out.
- **Motion:**
  - RN `Animated` with transform/opacity only (native driver on native), on `Animated.View` wrappers around small `react-native-svg` pieces. There are no SVG filters and no web-only CSS.
  - All loops stop when Brain is unreachable, when the app is backgrounded, when a sheet covers the scene, and under reduced motion.

**Tech Stack:**
- Expo SDK 57 / RN 0.86 / RN Web, expo-router, react-native-svg 15, `@expo-google-fonts/*`.
- Python FastAPI + pytest for the backend.

**Spec:** `docs/superpowers/specs/2026-10-15-cinnamon-design.md` §7 (screens, the roll, worlds, companions, menu), §8 (safety), §5 (API).
**Design reference:** `docs/design/cinnamon/Cinnamon.dc.html` (the interactive mockup: scene SVGs, pets, roll, menu, copy) and `Characters.dc.html`.

## Global Constraints

- **Run everything from the worktree root** `.worktrees/cinnamon`.
  - Python tests: `PYTHONPATH=$PWD/runtime /projects/data/ttsteam/ashwin/cluster-brain/runtime/.venv/bin/pytest -q runtime` (green before each task: 116 passed).
  - Typecheck: `node mobile/node_modules/.bin/tsc --noEmit -p mobile`.
  - npm: `node /projects/data/ttsteam/miniconda3/envs/gamified_life/lib/node_modules/npm/bin/npm-cli.js` (npm is not on PATH).
- **Numbers are verified only.** Every number on screen comes from Brain. A missing quota field reads "not reported". There are no findings yet, so there are no sugar crystals and Proud never shows. The headline is built from thread and job state, never invented.
- **Approvals:** the approval tray, hold-to-allow and signing are untouched. Dismissing a sheet never decides anything.
- **Motion:**
  - Transform/opacity only.
  - At most three loops on screen: the scene ambient, the companion idle, and the live tip.
  - Every loop is gated by `useLive()`, which is false when not connected, backgrounded, a sheet is open, or reduced motion is on.
- **No third-party scripts.** Fonts are bundled through `@expo-google-fonts` packages, so they are self-hosted. Only the active world's fonts load at start.
- **Accessibility:**
  - Every control has an `accessibilityLabel`.
  - Text is ≥ 12 px except mono captions (≥ 10.5).
  - Scene labels inside SVG are decorative duplicates: the same thread list is exposed as accessible buttons.
- **Never test on the owner's phone or live Brain.** Screens are verified with Playwright on the exported web build, with API calls mocked (Task 12).
- **Commits:** end each with the two attribution lines:
  - `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`
  - `Claude-Session: https://claude.ai/code/session_0137qnrn1B2jvqAHCTFRm4c4`

## Review Focus

1. **Brain unreachable at open.** The standup shows "as of N min ago" from the cached last state. The roll is asleep and nothing animates. → Task 7 cache + Task 12 shot.
2. **A brand-new install with no threads.** The scene renders with an empty presenter, the roll is idle, and the composer starts a new thread. → Task 7 + Task 12 shot.
3. **A provider CLI that hangs during the quota probe.** It times out at 15 s per provider and never blocks other requests. → Task 2 test.
4. **Sign out.** Server revoke fails (offline): local keys are still wiped and the app returns to pairing. The server revokes when reachable. → Task 4 test + Task 9.
5. **Switching worlds rapidly.** It must not stack wipes or leave the overlay stuck. → Task 9: ignore picks while a wipe runs.

## File map

| File | Responsibility |
|---|---|
| `runtime/brain/core.py` | `activity(thread_id)`; `rules()`; quiet-hours switch |
| `runtime/brain/quota.py` (new) | provider usage probes, parsing, snapshot cache |
| `runtime/brain/api.py` | `/v1/threads/{tid}/activity`, `/v1/quota`, `/v1/rules`, `PATCH /v1/settings`, `/v1/devices/self`, `POST /v1/devices/self/revoke` |
| `runtime/tests/test_p3.py` (new) | backend tests |
| `mobile/src/worlds.ts` (new) | world registry: tokens (day/night), fonts, copy, pet, presenter kind |
| `mobile/src/world.tsx` (new) | `WorldProvider`, `useWorld()`, persistence, lazy font loading, `useLive()` |
| `mobile/src/roll.ts` (new) | swirl and ribbon geometry from activity points (pure) |
| `mobile/components/cinnamon/Roll.tsx` | the roll: body, swirl, face (mood), accessory, steam, landing |
| `mobile/components/cinnamon/scenes/*.tsx` | `Bakery`, `Forest`, `Rustic`, `Space`, `Tide`: backdrop plus thread presenter |
| `mobile/components/cinnamon/Pets.tsx` | the five companions plus idle loops plus status bubble |
| `mobile/components/cinnamon/Ambient.tsx` | particles per world (motes, fireflies, stars, bubbles) |
| `mobile/components/cinnamon/WaitingCard.tsx` | ticket / tag / pinned note / transmission / bottle |
| `mobile/components/cinnamon/Menu.tsx` | bottom-tray sheet with steps |
| `mobile/components/cinnamon/Unrolled.tsx` | history sheet (ribbon) |
| `mobile/app/index.tsx` (new, replaces `(tabs)`) | the standup |
| `mobile/app/threads.tsx`, `runs.tsx`, `inbox.tsx` | moved from `(tabs)`, with `BackHeader` |

---

### Task 1: Activity points for the roll

**Files:** `runtime/brain/core.py`, `runtime/brain/api.py`, `runtime/tests/test_p3.py`

**Interfaces:**
- Produces: `Brain.activity(thread_id) -> {"start": float, "now": float, "turns": [[ts, weight]], "jobs": [[start, end, failed:0|1]], "left_at": float|None}`, with timestamps in epoch seconds.
  - `weight` = output tokens / 1000, capped at 30.
  - `jobs` uses `submitted_at`, plus `updated_at` once the job is terminal (now otherwise).
  - `failed` is 1 for FAILED, TIMEOUT, OUT_OF_MEMORY and NODE_FAIL.
- Produces: `GET /v1/threads/{tid}/activity?left_at=<epoch s>` (404 for an unknown thread).

- [ ] **Step 1: Failing test** (`runtime/tests/test_p3.py`):

```python
"""Cinnamon P3 backend: activity for the roll, quota snapshots, rules + quiet hours switch, self sign-out."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from brain.api import create_app


def test_activity_has_turns_and_jobs(brain):
    t = brain.create_thread("hi-tts-v3", "EDIT TTS", "worktree")
    now = time.time()
    brain.db.x("INSERT INTO turns(turn_id, thread_id, provider, started_seq, started_at, ended_at, output_tokens)"
               " VALUES ('tu1', ?, 'fake', 1, ?, ?, 4000)", (t["id"], now - 3600, now - 3500))
    brain.db.x("INSERT INTO jobs(job_id, thread_id, run_id, name, sentinel, state, submitted_at, updated_at, label)"
               " VALUES ('j1', ?, 'r', 'n', '/s', 'FAILED', ?, ?, 'v3_gate')", (t["id"], now - 3000, now - 2000))
    a = brain.activity(t["id"])
    assert a["turns"] == [[pytest.approx(now - 3600), 4.0]]
    assert a["jobs"] == [[pytest.approx(now - 3000), pytest.approx(now - 2000), 1]]
    assert a["start"] <= now - 3600 and a["now"] >= now
    with TestClient(create_app(brain, dev_no_auth=True)) as c:
        assert c.get(f"/v1/threads/{t['id']}/activity?left_at={now - 100}").json()["left_at"] == pytest.approx(now - 100)
        assert c.get("/v1/threads/nope/activity").status_code == 404
```

- [ ] **Step 2: Run it.** Expected: FAIL with `AttributeError: activity`.

  If the `turns` INSERT fails on a NOT NULL column, read `grep -n "CREATE TABLE IF NOT EXISTS turns" -A16 runtime/brain/db.py` and add the missing columns to the INSERT.

- [ ] **Step 3: Implement.** In `core.py`, near `jobs()`:

```python
    FAILED_JOB_STATES = ("FAILED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL")

    def activity(self, thread_id: str, left_at: float | None = None) -> dict:
        """What the roll draws: when the agent worked (turns, weighted by output) and when jobs ran or failed."""
        t = self.thread(thread_id)
        if not t:
            raise KeyError(thread_id)
        turns = [[r["started_at"], min(30.0, (r["output_tokens"] or 0) / 1000)] for r in
                 self.db.q("SELECT started_at, output_tokens FROM turns WHERE thread_id=? ORDER BY started_at", (thread_id,))]
        n = now()
        jobs = [[r["submitted_at"], r["updated_at"] if r["state"] in slurm.TERMINAL else n,
                 1 if r["state"] in self.FAILED_JOB_STATES else 0]
                for r in self.db.q("SELECT submitted_at, updated_at, state FROM jobs WHERE thread_id=? ORDER BY submitted_at",
                                   (thread_id,))]
        start = min([t["created_at"] if "created_at" in t.keys() else n, *(x[0] for x in turns), *(j[0] for j in jobs)])
        return {"start": start, "now": n, "turns": turns, "jobs": jobs, "left_at": left_at}
```

In `api.py`, next to the other thread routes:

```python
    @app.get("/v1/threads/{tid}/activity")
    def thread_activity(tid: str, left_at: float | None = None, device: str = Depends(auth)) -> dict:
        try:
            return brain.activity(tid, left_at)
        except KeyError:
            raise HTTPException(404, "no such thread")
```

- [ ] **Step 4: Run the tests.** Expected: PASS, and the suite is green.
- [ ] **Step 5: Commit** with the message "Activity for the roll: turns and jobs per thread".

---

### Task 2: Provider quota snapshots

**Files:** create `runtime/brain/quota.py`; modify `runtime/brain/api.py`, `runtime/tests/test_p3.py`

**Interfaces:**
- Produces:
  - `quota.parse_claude(obj) -> list[window]`
  - `quota.parse_codex(obj) -> list[window]`
  - `quota.parse_agy(obj) -> list[window]`
  - `window = {"name": str, "used_pct": float|None, "resets_at": float|None}`
  - `quota.snapshot(brain, force=False) -> {"as_of": float, "providers": [{"name", "plan", "windows": [...], "credits": str|None, "source": str, "error": str|None}]}`. It is cached for 300 s, and each probe has a 15 s timeout.
  - `GET /v1/quota?refresh=0|1`.
- The probes are verified sources (spec §7.3):
  - **claude:** a `control_request` `get_usage` on `claude -p --input-format stream-json --output-format stream-json --verbose`, in Brain's `CLAUDE_CONFIG_DIR`.
  - **codex:** the newest rollout under Brain's `CODEX_HOME/sessions` with a `token_count` event, reading `rate_limits`.
  - **agy:** `agy -p /usage --output-format stream-json`, with Brain's `--app_data_dir`.

- [ ] **Step 1: Failing tests:**

```python
from brain import quota

CLAUDE_USAGE = {"subscription_type": "team", "rate_limits_available": True,
                "five_hour": {"utilization": 4, "resets_at": "2026-10-15T07:09:00Z"},
                "seven_day": {"utilization": 21, "resets_at": "2026-10-17T18:59:00Z"}}
CODEX_LIMITS = {"primary": {"used_percent": 85.0, "window_minutes": 300, "resets_at": 1792040000},
                "secondary": {"used_percent": 44.0, "window_minutes": 10080, "resets_at": 1792400000},
                "plan_type": "plus", "credits": {"has_credits": False}}
AGY_USAGE = {"groups": [{"name": "Gemini Models", "buckets": [{"window": "weekly", "remaining_fraction": 0.75,
                                                               "reset_time": "2026-10-20T00:00:00Z"}]}]}


def test_parsers_read_only_what_providers_report():
    c = quota.parse_claude(CLAUDE_USAGE)
    assert [(w["name"], w["used_pct"]) for w in c] == [("5 h", 4), ("week", 21)] and c[0]["resets_at"] > 0
    x = quota.parse_codex(CODEX_LIMITS)
    assert [(w["name"], w["used_pct"]) for w in x] == [("5 h", 85.0), ("week", 44.0)]
    a = quota.parse_agy(AGY_USAGE)
    assert a == [{"name": "Gemini Models · week", "used_pct": 25.0, "resets_at": pytest.approx(1792454400, abs=86400)}]
    assert quota.parse_claude({"rate_limits_available": False}) == []


def test_snapshot_caches_and_survives_a_hanging_probe(brain, monkeypatch):
    calls = []

    def slow(brain_, name):
        calls.append(name)
        raise TimeoutError("probe timed out after 15 s")
    monkeypatch.setattr(quota, "_probe", slow)
    s = quota.snapshot(brain, force=True)
    assert all(p["error"] and p["windows"] == [] for p in s["providers"])
    n = len(calls)
    quota.snapshot(brain)  # cached
    assert len(calls) == n
    with TestClient(create_app(brain, dev_no_auth=True)) as cl:
        assert "providers" in cl.get("/v1/quota").json()
```

- [ ] **Step 2: Run it.** Expected: FAIL with `ImportError`.
- [ ] **Step 3: Implement** `runtime/brain/quota.py`:

```python
"""What each provider itself reports about remaining usage. Never estimated from Brain's own token counts.
Sources verified 2026-10-15 (spec §7.3): Claude get_usage control request, Codex rollout rate_limits, agy /usage."""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import threading
import time
from pathlib import Path

TTL_S, PROBE_TIMEOUT_S = 300, 15
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
    out = []
    for key, name in (("five_hour", "5 h"), ("seven_day", "week")):
        w = o.get(key) or {}
        if w.get("utilization") is not None:
            out.append({"name": name, "used_pct": w["utilization"], "resets_at": _ts(w.get("resets_at"))})
    return out


def parse_codex(o: dict) -> list[dict]:
    out = []
    for key in ("primary", "secondary"):
        w = (o or {}).get(key) or {}
        if w.get("used_percent") is None:
            continue
        mins = w.get("window_minutes") or w.get("windowDurationMins")
        out.append({"name": "5 h" if mins == 300 else "week" if mins == 10080 else f"{mins} min",
                    "used_pct": w["used_percent"], "resets_at": _ts(w.get("resets_at") or w.get("resetsAt"))})
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
    r = subprocess.run(args, input=stdin, capture_output=True, text=True, timeout=PROBE_TIMEOUT_S, env={**os.environ, **env})
    return r.stdout


def _probe(brain, name: str) -> dict:
    p = brain.providers.get(name)
    binary = getattr(p, "binary", name)
    if name == "claude":
        req = json.dumps({"type": "control_request", "request_id": "q1", "request": {"subtype": "get_usage", "skip_behaviors": True}})
        out = _run([binary, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"],
                   {"CLAUDE_CONFIG_DIR": str(brain.p.claude_config_dir)}, req + "\n")
        for line in out.splitlines():
            try:
                m = json.loads(line)
            except json.JSONDecodeError:
                continue
            if m.get("type") == "control_response":
                body = (m.get("response") or {}).get("response") or m.get("response") or {}
                return {"windows": parse_claude(body), "plan": body.get("subscription_type"), "source": "Claude usage report"}
        return {"windows": [], "plan": None, "source": "Claude usage report"}
    if name == "codex":
        home = Path(brain.p.root) / "codex" / "sessions"
        for f in sorted(home.glob("*/*/*/rollout-*.jsonl"), key=lambda x: x.stat().st_mtime, reverse=True)[:5]:
            for line in reversed(f.read_text(errors="replace").splitlines()):
                if '"token_count"' in line and '"rate_limits"' in line:
                    rl = (json.loads(line).get("payload") or {}).get("rate_limits") or {}
                    return {"windows": parse_codex(rl), "plan": rl.get("plan_type"),
                            "source": f"last Codex turn ({time.strftime('%a %H:%M', time.localtime(f.stat().st_mtime))})"}
        return {"windows": [], "plan": None, "source": "no Codex turn yet"}
    if name == "agy":
        out = _run([binary, "-p", "/usage", "--output-format", "stream-json", f"--app_data_dir={Path(brain.p.root) / 'agy'}"], {})
        for line in out.splitlines():
            try:
                m = json.loads(line)
            except json.JSONDecodeError:
                continue
            body = m.get("result") if isinstance(m.get("result"), dict) else m
            if isinstance(body, dict) and body.get("groups"):
                return {"windows": parse_agy(body), "plan": None, "source": "agy /usage"}
        return {"windows": [], "plan": None, "source": "agy /usage"}
    return {"windows": [], "plan": None, "source": "not reported"}


def snapshot(brain, force: bool = False) -> dict:
    with _lock:
        if not force and _cache.get("as_of", 0) > time.time() - TTL_S:
            return _cache["value"]
        provs = []
        for name in [n for n in brain.providers if n in ("claude", "codex", "agy")] or list(brain.providers):
            try:
                r = _probe(brain, name)
                provs.append({"name": name, "plan": r.get("plan"), "windows": r["windows"], "source": r["source"], "error": None})
            except Exception as e:  # a hung or missing CLI reports itself; it never blocks the others
                provs.append({"name": name, "plan": None, "windows": [], "source": "", "error": str(e)[:200]})
        value = {"as_of": time.time(), "providers": provs}
        _cache.update(as_of=value["as_of"], value=value)
        return value
```

In `api.py`, add `quota` to the imports, then:

```python
    @app.get("/v1/quota")
    def get_quota(refresh: int = 0, device: str = Depends(auth)) -> dict:
        return quota.snapshot(brain, force=bool(refresh))
```

- [ ] **Step 4: Run the tests.** Expected: PASS.

  Then do one real-probe check on a throwaway root. It reads only usage and runs no model turn:

  ```bash
  PYTHONPATH=$PWD/runtime python -c "from brain import quota; print(quota.parse_claude({}))"
  ```

  The live probe is checked in Task 12, against the real `claude` with `CLAUDE_CONFIG_DIR=~/.brain/claude` read-only.
- [ ] **Step 5: Commit** with the message "Quota: what each provider reports, cached, never estimated".

---

### Task 3: Rules list and the quiet-hours switch

**Files:** `runtime/brain/core.py`, `runtime/brain/api.py`, `runtime/tests/test_p3.py`

**Interfaces:**
- Produces: `GET /v1/rules`, which returns `{"groups": [{"head": str, "items": [{"text": str, "chip": "asks you"|"blocked"}]}], "quiet_hours": {"enabled": bool, "from": "23:00", "to": "07:00"}}`.
- Produces: `PATCH /v1/settings {"quiet_hours_enabled": bool}`, which returns the same `quiet_hours` block. It accepts that key only (anything else → 422) and persists via `settings.save`.

- [ ] **Step 1: Failing test:**

```python
def test_rules_and_quiet_hours_switch(brain):
    with TestClient(create_app(brain, dev_no_auth=True)) as c:
        r = c.get("/v1/rules").json()
        heads = [g["head"] for g in r["groups"]]
        assert heads == ["Brain asks you first", "Never on the login node"]
        assert r["quiet_hours"]["from"] == "23:00"
        assert c.patch("/v1/settings", json={"quiet_hours_enabled": True}).json()["enabled"] is True
        assert brain.settings.raw["quiet_hours_enabled"] is True
        assert c.patch("/v1/settings", json={"default_provider": "x"}).status_code == 422
```

- [ ] **Step 2: Run it.** Expected: FAIL with 404.
- [ ] **Step 3: Implement.** In `core.py`:

```python
    def rules(self) -> dict:
        """What Brain enforces (policy.py), in the owner's words, plus the one switch the phone may flip."""
        return {"groups": [
            {"head": "Brain asks you first", "items": [
                {"text": "Writing outside the repo and its vault", "chip": "asks you"},
                {"text": "Pushing to protected branches (main, master)", "chip": "asks you"},
                {"text": "Deleting vaults or checkpoints", "chip": "asks you"},
                {"text": "Global installs, git config, crontab", "chip": "asks you"}]},
            {"head": "Never on the login node", "items": [
                {"text": "Detached processes, tmux, sleep and squeue loops", "chip": "blocked"},
                {"text": "Reading Brain's keys and tokens", "chip": "blocked"}]}],
            "quiet_hours": self.quiet_hours()}

    def quiet_hours(self) -> dict:
        qh = list(self.settings.quiet_hours) + ["", ""]
        return {"enabled": bool(self.settings.raw.get("quiet_hours_enabled", True)), "from": str(qh[0]), "to": str(qh[1])}

    def set_quiet_hours(self, enabled: bool) -> dict:
        self.settings.raw["quiet_hours_enabled"] = bool(enabled)
        self.settings.save(self.p)
        return self.quiet_hours()
```

In `api.py`:

```python
    class SettingsPatch(BaseModel):
        model_config = {"extra": "forbid"}
        quiet_hours_enabled: bool

    @app.get("/v1/rules")
    def get_rules(device: str = Depends(auth)) -> dict:
        return brain.rules()

    @app.patch("/v1/settings")
    def patch_settings(b: SettingsPatch, device: str = Depends(auth)) -> dict:
        return brain.set_quiet_hours(b.quiet_hours_enabled)
```

Define `SettingsPatch` at module level, next to the other models; the indented block above only shows its shape.

- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Commit** with the message "Rules list and quiet-hours switch for the menu".

---

### Task 4: This phone: name and sign out

**Files:** `runtime/brain/api.py`, `runtime/tests/test_p3.py`

**Interfaces:**
- Produces: `GET /v1/devices/self`, which returns `{"id", "name", "created_at", "last_seen", "web_push": bool, "revoked_others": int}`.
- Produces: `POST /v1/devices/self/revoke`, which returns `{"revoked": true}`. It revokes the calling device through `brain.pairing.revoke(device_id)`.

- [ ] **Step 1: Failing test.**

  Read how `test_security.py` authenticates a real device before writing this (`grep -n "def _paired\|Authorization\|def auth" runtime/tests/test_security.py runtime/brain/api.py | head`). Reuse its helper to get a device id and token. The `dev_no_auth` app reports device `"dev"`, which is not a real device row, so this test must use a real pairing.

```python
def test_self_device_and_sign_out(paired_client):  # fixture built from test_security's pairing helper
    c, device_id = paired_client
    me = c.get("/v1/devices/self").json()
    assert me["id"] == device_id
    assert c.post("/v1/devices/self/revoke").json() == {"revoked": True}
    assert c.get("/v1/devices/self").status_code in (401, 403)
```

- [ ] **Step 2: Run it.** Expected: FAIL with 404.
- [ ] **Step 3: Implement** in `api.py`:

```python
    @app.get("/v1/devices/self")
    def device_self(device: str = Depends(auth)) -> dict:
        d = brain.db.one("SELECT * FROM devices WHERE id=?", (device,))
        if not d:
            raise HTTPException(404, "unknown device")
        others = brain.db.one("SELECT COUNT(*) AS n FROM devices WHERE revoked=1")["n"]
        return {"id": d["id"], "name": d["name"], "created_at": d["created_at"], "last_seen": d["last_seen"],
                "web_push": bool(d["web_push"]), "revoked_others": others}

    @app.post("/v1/devices/self/revoke")
    def device_self_revoke(device: str = Depends(auth)) -> dict:
        brain.pairing.revoke(device)
        brain.ir.log("device.signout", device=device)
        return {"revoked": True}
```

- [ ] **Step 4: Run the tests.** Expected: PASS, and the suite is green.
- [ ] **Step 5: Commit** with the message "This phone: who am I, and sign out".

---

### Task 5: Worlds, fonts and the world context

**Files:** `mobile/package.json` (fonts), create `mobile/src/worlds.ts` and `mobile/src/world.tsx`, modify `mobile/app/_layout.tsx`

**Interfaces:**
- Produces:
  - `WORLD_IDS = ['bakery','forest','rustic','space','tide'] as const`
  - `type WorldId`
  - `type Tokens = {ink, ink2, ink3, raise, bg2, line, accent, needs, finding, sky1, sky2, ...scene keys}`
  - `WORLDS: Record<WorldId, World>`, where `World = {id, name, where, line, fonts: {display, ui, mono, hand}, fontFiles: Record<string, number>, day: Tokens, night: Tokens, pet: {name, kind}, need: string, presenter: 'tickets'|'tags'|'cards'|'satellites'|'bubbles'}`
- Produces: `WorldProvider`, plus `useWorld()` returning `{world, id, dark, mode: 'day'|'night'|'auto', tokens, setWorld(id), setMode(m), pet: boolean, setPet(b), showcase: boolean, setShowcase(b), fontsReady: boolean}`. Prefs persist with `getMeta`/`setMeta` keys `world`, `world_mode`, `pet`, `showcase`.
- Produces: `useLive(extra?: boolean)`, which is true only when the connection status is `live`, the app is active, reduced motion is off, and `extra !== false`.

- [ ] **Step 1: Install fonts.** Run from `mobile/`:

```bash
node /projects/data/ttsteam/miniconda3/envs/gamified_life/lib/node_modules/npm/bin/npm-cli.js install \
  @expo-google-fonts/fraunces @expo-google-fonts/instrument-sans @expo-google-fonts/dm-mono \
  @expo-google-fonts/young-serif @expo-google-fonts/hanken-grotesk @expo-google-fonts/alegreya \
  @expo-google-fonts/alegreya-sans @expo-google-fonts/caveat @expo-google-fonts/courier-prime \
  @expo-google-fonts/unbounded @expo-google-fonts/sora @expo-google-fonts/martian-mono \
  @expo-google-fonts/fredoka @expo-google-fonts/figtree
```

Expected: `package.json` and the lock gain 14 dependencies.

- [ ] **Step 2: Write `src/worlds.ts`.**
  - Port the five worlds' tokens from `docs/design/cinnamon/Cinnamon.dc.html` (the `.cn`, `.cn.dark` and `.cn.w-*` CSS variables, day and night) into `day`/`night` objects. Keep the exact hex values; CSS names map to camelCase keys (`--bk-wall` → `bkWall`).
  - Port each world's `name`, `where`, `line`, `pet`, `need` and `lines` from the mockup's `WORLDS` constant.
  - `fontFiles` maps family keys to `require`d TTFs from the installed packages, at the weights listed in the spec table:
    - Bakery: Fraunces 600, Instrument Sans 400/600, DM Mono 400
    - Forest: Young Serif 400, Hanken Grotesk 400/600, IBM Plex Mono 400 (already loaded)
    - Rustic: Alegreya 800, Alegreya Sans 400/700, Caveat 600, Courier Prime 400
    - Space: Unbounded 500, Sora 400/600, Martian Mono 400
    - Tide: Fredoka 600, Figtree 400/600, DM Mono 400
- [ ] **Step 3: Write `src/world.tsx`:**
  - The provider loads prefs.
  - It resolves `dark` from the mode, using the system scheme (`useColorScheme`) for `auto`.
  - It loads the active world's fonts with `Font.loadAsync(world.fontFiles)`; until they load, `fontsReady=false` and screens fall back to the current fonts.
  - It exposes setters.
  - `useLive` combines `useConnection()` + `statusOf`, `AppState`, and `AccessibilityInfo.isReduceMotionEnabled()` with its change listener.
- [ ] **Step 4: Wrap the app.** In `app/_layout.tsx`, wrap `<Stack>` in `<WorldProvider>`.
- [ ] **Step 5: Typecheck.** Expected: exit 0.
- [ ] **Step 6: Commit** with the message "Worlds: tokens, fonts and a persisted world context".

---

### Task 6: The roll

**Files:** create `mobile/src/roll.ts` and `mobile/components/cinnamon/Roll.tsx`

**Interfaces:**
- Produces:
  - `activityWidths(a: Activity, n = 150): number[]`
  - `swirlPoints(n): [number, number][]` (3 turns, top face centre (100, 62), y squash 0.5)
  - `ribbonPoints(n)`
  - `outline(points, widths, a, b): string`
  - `line(points, a, b): string`
  - `sinceIndex(a, n)`

  These are pure ports of `base()`, `edges`, `outline` and `line` from the mockup script; the roll's swirl tail ends at `thMax + 0.15` rad, beside the face.
- Produces: `<Roll mood={'needs'|'working'|'asleep'|'proud'} activity={Activity|null} accessory={'icing'|'leaf'|'helmet'|null} live={boolean} size={number} />`, drawn in a 200×170 viewBox. It renders the body, the side layers, the top, the swirl (cinnamon), the fresh trace since `left_at`, the tip glow, the face per mood (the four face paths from the mockup), cheeks, the accessory, and steam when `working|needs` and the accessory isn't the helmet.
  - **Breathing:** an `Animated.View` scale loop (4.2 s) when `live`.
  - **Landing:** `land()` via ref, which drops 260 px, squashes and settles (950 ms).
- **Acceptance:**
  - With `activity=null`, the swirl is a plain uniform spiral (no data yet).
  - The swirl never shows sugar crystals, because there are no findings until P2.
  - The typecheck is clean.

- [ ] **Step 1: Write `src/roll.ts`** as a straight TypeScript port of the mockup functions named above. Input is the `/v1/threads/{tid}/activity` JSON, with times scaled to `[start, now]`.
- [ ] **Step 2: A runnable check** of the geometry, run through `node` on the compiled output:

  ```bash
  node mobile/node_modules/.bin/tsc --outDir /tmp/x mobile/src/roll.ts && node -e "..."
  ```

  The script asserts 150 points, widths in [0.9, 8], and the tip at the outer end (r ≈ 66).
- [ ] **Step 3: Write `Roll.tsx`** with react-native-svg (`Svg`, `Path`, `Ellipse`, `Circle`, `G`) and `Animated.View` wrappers for breathe, steam and landing.
- [ ] **Step 4: Typecheck, then commit** with the message "The roll: swirl from real activity, moods, accessories".

---

### Task 7: Standup data

**Files:** create `mobile/src/standup.ts`, modify `mobile/src/api.ts` and `mobile/src/types.ts`

**Interfaces:**
- Produces: `loadStandup(leftAt: number|null): Promise<Standup>`, where `Standup = {focus: Thread|null, threads: Thread[] (≤3 for the scene), waiting: Waiting|null, waitingCount: number, runningJobs: Job[], since: Since, activity: Activity|null, asOf: number}`.
  - `focus` = the thread needing you, else the running one, else the most recently updated.
  - `waiting` = the first approval, then listening test, then needs-you thread.
- Produces: `headline(s, showcase): string` and `subline(s, showcase): string`. Both are built only from state:
  - "EDIT TTS needs you."
  - "EDIT TTS is running v3_eval_fullb200k."
  - "EDIT TTS is waiting on 2 jobs."
  - "EDIT TTS is resting."
  - "Nothing is running." (no threads)

  The subline is the since-you-left sentence (from `/v1/pending?since`) plus "N more waiting" when `waitingCount > 1`.
- Produces: `petLine(world, s, showcase): string`, per world, using the counts only.
- Cache: the last `Standup` is saved with `setMeta('standup_cache', JSON)`. On a network error the cache is returned with its `asOf`, and the screen shows "as of N min ago".
- API client:
  - `api.activity(tid, leftAt)`, `api.quota(refresh)`, `api.rules()`, `api.setQuietHours(b)`, `api.deviceSelf()`, `api.signOutSelf()`;
  - their types are added to `types.ts`.

- [ ] **Step 1: Write the code above.**
- [ ] **Step 2: A runnable check** for `headline` with fixture objects, run via the `tsc` → `node` route as in Task 6. It covers needs-you, running and empty.
- [ ] **Step 3: Typecheck, then commit** with the message "Standup data: focus thread, waiting item, sentences from state, offline cache".

---

### Task 8: Scenes, waiting cards, companions, ambient

**Files:** `mobile/components/cinnamon/scenes/{Bakery,Forest,Rustic,Space,Tide}.tsx`, `WaitingCard.tsx`, `Pets.tsx`, `Ambient.tsx`, plus an `index.ts` mapping `WorldId` to its scene

**Interfaces:**
- Produces: each scene takes `{tokens, dark, width, height, threads: Thread[], onThread(id), live}` and renders the full-bleed `Svg` backdrop plus its thread presenter. The presenters are tickets on the rail, tags swinging from the branch, recipe cards on cork, satellites on the orbit, and bubbles.
  - The SVG is a faithful port of the mockup's scene markup (`<!-- ============ BAKERY ============ -->` … `TIDE`), with coordinates in the 390×844 design space scaled to the screen width.
  - Thread names are ellipsized to the presenter's width.
  - Each presenter item is also a `Pressable` (absolute, transparent) with `accessibilityLabel` `"<title>, <state>"`, opening `/thread/<id>`.
- Produces: `<WaitingCard world tokens waiting onPress />`, the shape per world (ticket / tag / pinned note / transmission / bottle). It shows the label (`ORDER UP`…), title and one detail line:
  - "Listen: <title>" for listening;
  - the approval's verb and thread title for an approval;
  - "<thread> needs you" otherwise.
- Produces: `<Pet world dark live count onPress />`:
  - Crumb hops; Hazel's tail flicks, holding one acorn per finding (0 now) and 1 at her feet as decoration; Nutmeg purrs, ears up when needed; Anise's arms sway with `min(8, runningJobs)` tips lit; Saffron swims a 16 s lap.
  - Each pet is its own `Svg` inside an `Animated.View`.
- Produces: `<Ambient kind tokens dark live />`: motes, fireflies, pollen, stars twinkling (1 in 4), bubbles, rustic flame flicker and snow. There are at most ~16 animated views, and nothing animates when `!live`.
- **Acceptance:** each scene matches its mockup render in Task 12's screenshots (layout, colours, presenters), in day and night.

- [ ] **Step 1: Bakery and its waiting card.** Port them, then typecheck.
- [ ] **Step 2: Forest, Rustic, Space, Tide.** Port them, then typecheck.
- [ ] **Step 3: Pets and Ambient.** Typecheck.
- [ ] **Step 4: Commit** with the message "Worlds: five scenes, waiting cards, companions, ambient life".

---

### Task 9: Menu (bottom tray)

**Files:** create `mobile/components/cinnamon/Menu.tsx`

**Interfaces:**
- Produces: `<Menu open onClose standup />`. It is a bottom sheet whose height animates per step: root 560, world 470, quota 520, rules 560, phone 420, scaled to the screen. Steps slide in the direction of travel (420 ms, `Easing.bezier(.32,.72,0,1)`).
- **Root:**
  - World (detail: name · day/night/matches phone)
  - Companion switch ("Hazel the squirrel")
  - Quota (detail: the highest used % across providers)
  - Rules (count)
  - This phone
  - Quiet hours switch (via `PATCH /v1/settings`)
  - Connection (status + endpoint host)
  - Navigation rows: Threads, Jobs, Import sessions, Start something
  - Sign out (two taps)
- **World:**
  - Horizontal cards with a mini scene and a mini roll, the name in the world's own font (when loaded), and `where · pet`.
  - Day / Night / Match phone.
  - Showcase.
  - **Picking a world:**
    - A wipe circle in the new world's sky colour grows from the tray (600 ms scale), then the world switches, the roll `land()`s and the overlay fades.
    - Picks are ignored while a wipe runs.
    - On native, `Haptics.impactAsync(Light)` fires; on web, `navigator.vibrate?.(15)`.
- **Quota:** per provider, two arc gauges (react-native-svg, sweeping on mount), the reset times, plan, source and "as of N min ago". Missing data reads "not reported". There is a Refresh action.
- **Rules:** groups and chips from `/v1/rules`; the footnote "Enforced by Brain; changed only from the terminal."
- **This phone:**
  - Name, paired date, notifications status, earlier pairings count.
  - Sign out (two taps): `api.signOutSelf()` (errors ignored), then `forgetPairing()` + `wipeLocal()`, then `router.replace('/pair')`.

- [ ] **Step 1: Write `Menu.tsx`.**
- [ ] **Step 2: Typecheck, then commit** with the message "Menu in the bottom tray: world, companion, quota, rules, quiet hours, connection, this phone".

---

### Task 10: Unrolled history sheet

**Files:** create `mobile/components/cinnamon/Unrolled.tsx`

**Interfaces:**
- Produces: `<Unrolled open onClose activity title />`. It draws the ribbon (`ribbonPoints`) with widths from activity, the fresh section since `left_at`, failed-job flecks, day labels (local weekday + date across `[start, now]`) and the now tip.
- The text explanation is in the owner's words: "Thicker where the agent was busy, flecked where jobs failed, darker since you left."
- An empty activity shows "No history yet."

- [ ] **Step 1: Write it; typecheck; commit** with the message "Unrolled: the roll's history laid flat".

---

### Task 11: The standup screen and navigation

**Files:**
- Delete: `mobile/app/(tabs)/_layout.tsx`, and move `(tabs)/index.tsx` → `app/inbox.tsx`.
- Move: `(tabs)/threads.tsx` → `app/threads.tsx`, `(tabs)/runs.tsx` → `app/runs.tsx`.
- Create: `mobile/app/index.tsx`.
- Modify: `app/_layout.tsx` (stack screens, `initialRouteName`, the unpaired redirect).

**Interfaces:**
- **`/` (standup):** the world scene, full-bleed, with:
  - the header ("Cinnamon" in the world display font, plus the live dot/text);
  - headline and subline;
  - the roll, placed per world as in the mockup's `hero` table (tap → Unrolled);
  - the pet (tap → bubble with `petLine`);
  - the waiting card (tap → `/approval/<id>` | `/listen/<id>` | `/thread/<id>`);
  - the dock (☰ → Menu; the composer sends to the focus thread via `api.sendMessage`, with no focus thread → `/new`);
  - pull to refresh.
  - The data comes from `loadStandup` on focus, and on WS events (`brainSocket`) throttled to 2 s.
  - Showcase swaps names and numbers for generic copy.
- **Redirect:** unpaired → `/pair`, as `(tabs)/_layout` did.
- **`/threads`, `/runs`, `/inbox`:** replace `TabHeader` with `BackHeader` and keep their content. Push notification routes (`/runs`, `/thread/x`, `/approval/x`, `/listen/x`) keep working.

- [ ] **Step 1: Move the files** and fix imports, then typecheck.
- [ ] **Step 2: Write `app/index.tsx`,** then typecheck.
- [ ] **Step 3: Commit** with the message "Standup home: the roll in its world, waiting card, companion, dock; tabs retired".

---

### Task 12: Verify on the exported web build, then ship

**Files:** none committed, except fixes found here.

- [ ] **Step 1: Export.** From `mobile/`, run:

  ```bash
  node node_modules/.bin/expo export --platform web --output-dir dist.p3
  ```

  Expected: "Exported".
- [ ] **Step 2: Screenshot pass** with a scratch Playwright script (outside the repo):
  - It serves `dist.p3` from a local static server with SPA fallback.
  - It sets the pairing in localStorage the way `secure.web.ts` stores it.
  - It mocks `/v1/*` with fixtures built from the owner's real shapes (threads, pending with one listening test, jobs, activity, quota, rules, devices/self).
  - It captures, at 390×844:
    - the standup in each world × day/night;
    - menu root, world, quota, rules and phone;
    - unrolled;
    - offline ("as of"), with the mock returning 502;
    - empty (no threads).
  - Fix everything the shots show in one batch, then re-shoot once.
- [ ] **Step 3: Real quota probe.** Run `quota.snapshot` against a throwaway Brain root with `CLAUDE_CONFIG_DIR` pointing at a copy of `~/.brain/claude` credentials. It is read-only and runs no turn. Expected: the Claude windows are present.
- [ ] **Step 4: Run the full suite and typecheck.** Expected: green.
- [ ] **Step 5: Final whole-branch review** with a fresh reviewer, then a fix pass test-first, as in the previous plans.
- [ ] **Step 6: Merge to main.**
  - Swap `mobile/dist` for the new export, keeping the old one as `dist.prev-<date>`.
  - Restart Brain via keep-alive, after checking that no turn or job is running.
  - Tell the owner to fully close and reopen the PWA.

## Not in this plan

- Re-theming the deep screens (chat, approval, listening, files).
- The repo session, code versions and change requests (P1).
- `brain status`/`finding` and sugar crystals (P2).
- Rustic/Space/Tide polish beyond the mockup.
