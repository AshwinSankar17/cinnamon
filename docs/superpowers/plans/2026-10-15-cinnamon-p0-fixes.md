# Cinnamon P0: fixes that matter now — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the P0 quality-of-life fixes from the Cinnamon spec:
- pushes the owner actually needs, and silence when they're asleep;
- live work never auto-archived;
- an honest "since you left";
- no false scope approvals;
- named devices and the right default provider;
- health checks;
- a stable relay.

**Architecture:** Mostly small, local changes to `runtime/brain/core.py`, `policy.py`, `config.py`, `db.py` and `api.py`, each driven by a pytest. There are three thin mobile changes (Inbox "since you left", device name, default provider) and one keep-alive change for a named Cloudflare tunnel. There are no new dependencies and no schema changes except one additive table (`digest`).

**Tech Stack:** Python 3.10, FastAPI, SQLite (additive schema), pytest + pytest-asyncio (in `runtime/.venv`), Expo / React Native Web (TypeScript, `npm run typecheck`), bash.

**Spec:** `docs/superpowers/specs/2026-10-15-cinnamon-design.md` (§9 rows marked P0, §10 "P0", §8 for push content).

## Global Constraints

- **Tests:** run them with `cd runtime && . .venv/bin/activate && pytest -q`. The suite is green at 66 passed before this plan starts. Every task leaves it green.
- **Mobile:** check it with `cd mobile && npm run typecheck`. There is no JS unit-test runner, so don't add one.
- **Never test on the owner's phone or live Brain:** not `~/.brain`, not the running quick tunnel. Tests use the `brain` fixture in `runtime/tests/conftest.py`.
- **Schema changes are additive only:** `CREATE TABLE IF NOT EXISTS` in `db.SCHEMA` or a row in `db.MIGRATIONS`.
- **Never rename** `brain`, `BRAIN_*`, `~/.brain`, hook names, `refs/brain` or signature domains.
- **Numbers are verified only.** A push or Inbox line states counts Brain read from its own tables, never estimates.
- **Approvals always ring,** including in quiet hours.
- **Commits:** end each commit message with the two attribution lines this session uses:
  - `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`
  - `Claude-Session: https://claude.ai/code/session_0137qnrn1B2jvqAHCTFRm4c4`
- **Deferred to later plans, on purpose:**
  - `/status` in the composer moves to the P2 standup, which shows job state without a turn.
  - Showing the auto-archive TTL in the app and the device rename UI move to the P3 menu (This phone).

## Review Focus

1. **Quiet hours that wrap past midnight (23:00–07:00).** A push at 03:40 must be held. 07:00 exactly is outside the window, and so is 22:59. → Task 4 boundary test.
2. **The owner muted a thread.** A held digest must not resurrect pushes from it. → Task 4 test: muted thread's push is neither sent nor digested.
3. **A job-woken turn that was stopped by the owner, or that errored.** It must not also send a "finished" push. An errored turn sends exactly one "failed" push. → Task 1 test.
4. **`left_at` from a phone with a skewed clock, or in the future.** The server must clamp it so it never returns negative windows or crashes on bad input. → Task 5 tests `since` in the future and `since=abc`.
5. **Owner's existing `settings.yaml` still contains `archive_policy: full` under repos.** Loading must not crash after the field is removed. → Task 9 test.

---

## File map

| File | Change |
|---|---|
| `runtime/brain/core.py` | turn-end pushes; failed-job label; expiry rules; quiet hours + digest; `pending(since)`; `set_endpoint` web push; `_push_all` helper; health checks helper |
| `runtime/brain/policy.py` | heredoc stripping; implausible-target filter |
| `runtime/brain/config.py` | drop dead settings; tolerate unknown repo keys; `quiet_hours_enabled` |
| `runtime/brain/db.py` | `digest` table |
| `runtime/brain/api.py` | `GET /v1/pending?since=`; `/v1/providers` marks default; `/v1/health` checks |
| `runtime/tests/test_p0.py` | new: all P0 tests |
| `mobile/src/api.ts`, `mobile/src/types.ts`, `mobile/app/(tabs)/index.tsx` | send `left_at`; render server counts |
| `mobile/src/format.ts`, `mobile/app/pair.tsx` | UA-derived device name on web |
| `mobile/app/new.tsx` | default provider from Brain |
| `deploy/keepalive.sh`, `deploy/relay.md` | keep a named tunnel alive |

---

### Task 1: Push when a job wakes the agent, and when a turn fails

The owner sent 12 status pings in 47 messages because nothing told them a woken turn had reported back. A 10-hour "`claude` not found" outage was also silent.

**Files:**
- Modify: `runtime/brain/core.py` (end of `_run_turn`, around lines 636–640)
- Test: `runtime/tests/test_p0.py` (create)

**Interfaces:**
- Consumes: `Brain.wake(thread_id, reason)`, `Brain.send_message`, `Brain._notify(thread_id, kind, title, body, data)`.
- Produces: pushes of kind `done` (title `"<thread title>: <job label> finished"`) and `failed` (title `"<thread title>: turn failed"`).

- [ ] **Step 1: Write the failing tests**

Create `runtime/tests/test_p0.py`:

```python
"""Cinnamon P0: pushes, quiet hours, expiry, since-you-left, policy false positives, health, settings."""
import time

import pytest
from fastapi.testclient import TestClient

from brain.api import create_app
from test_flows import _settle


@pytest.fixture
def pushes(brain, monkeypatch):
    sent = []
    monkeypatch.setattr(brain, "_notify", lambda tid, kind, title, body, data: sent.append((kind, title, body)))
    return sent


def _job(brain, tid, job_id="9001", label="v3_eval_fullb200k", state="COMPLETED"):
    brain.db.x("INSERT INTO jobs(job_id, thread_id, run_id, name, sentinel, state, submitted_at, updated_at, label)"
               " VALUES (?,?,?,?,?,?,?,?,?)", (job_id, tid, "run_x", "brain.x.y", "/tmp/s", state, time.time(), time.time(), label))


async def test_a_job_woken_turn_pushes_its_first_line(brain, pushes):
    t = brain.create_thread("hi-tts-v3", "EDIT TTS", "worktree")
    _job(brain, t["id"])
    await brain.wake(t["id"], "[Brain] SLURM job 9001 finished: COMPLETED")
    await _settle(brain, t["id"])
    done = [p for p in pushes if p[0] == "done"]
    assert len(done) == 1
    assert done[0][1] == "EDIT TTS: v3_eval_fullb200k finished"
    assert done[0][2]  # the agent's first line, not empty


async def test_an_owner_message_turn_does_not_push_done(brain, pushes):
    t = brain.create_thread("hi-tts-v3", "EDIT TTS", "worktree")
    await brain.send_message(t["id"], "hello")
    await _settle(brain, t["id"])
    assert [p for p in pushes if p[0] == "done"] == []


async def test_a_failed_turn_pushes_once_and_never_done(brain, pushes):
    t = brain.create_thread("hi-tts-v3", "EDIT TTS", "worktree")
    _job(brain, t["id"])
    await brain.wake(t["id"], "please simulate failure")
    await _settle(brain, t["id"])
    kinds = [p[0] for p in pushes]
    assert kinds.count("failed") == 1 and "done" not in kinds
    assert pushes[kinds.index("failed")][1] == "EDIT TTS: turn failed"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd runtime && . .venv/bin/activate && pytest tests/test_p0.py -q`
Expected: FAIL. There are no `done` or `failed` pushes yet; the existing `done` push needs a turn longer than 600 s.

- [ ] **Step 3: Implement**

In `runtime/brain/core.py`, replace the block

```python
        done_after = float(self.settings.raw.get("notify_done_after_s", 600))  # quiet by default: long turns only
        if dur > done_after * 1000 and not res.error and self._resting_state(thread_id) == "idle":
            self._notify(thread_id, "done", f"{t['title']}: turn finished",
                         (res.text or "Done.")[:200], {"thread_id": thread_id})
```

with

```python
        first_line = next((ln.strip() for ln in (res.text or "").splitlines() if ln.strip()), "Done.")[:200]
        if res.stop_reason == "error":  # the owner hears about failures, including a provider that will not start
            self._notify(thread_id, "failed", f"{t['title']}: turn failed", (res.error or "")[-200:],
                         {"thread_id": thread_id, "turn_id": turn_id})
        elif res.stop_reason != "cancelled" and wake_reason == "wait fired":  # a job came back: say what the agent made of it
            j = self.db.one("SELECT label, name FROM jobs WHERE thread_id=? ORDER BY updated_at DESC LIMIT 1", (thread_id,))
            what = (j["label"] or j["name"]) if j else "job"
            self._notify(thread_id, "done", f"{t['title']}: {what} finished", first_line, {"thread_id": thread_id})
        else:
            done_after = float(self.settings.raw.get("notify_done_after_s", 600))  # owner-started turns: long ones only
            if dur > done_after * 1000 and not res.error and self._resting_state(thread_id) == "idle":
                self._notify(thread_id, "done", f"{t['title']}: turn finished", first_line, {"thread_id": thread_id})
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_p0.py -q && pytest -q`
Expected: the 3 new tests PASS and the full suite is green.

- [ ] **Step 5: Commit**

```bash
git add runtime/brain/core.py runtime/tests/test_p0.py
git commit -m "Push when a job-woken turn reports back and when a turn fails"
```

---

### Task 2: Failed-job push names the job by its label

**Files:**
- Modify: `runtime/brain/core.py:930` (in `tick`)
- Test: `runtime/tests/test_p0.py`

**Interfaces:**
- Consumes: `jobs.label` column.
- Produces: the failed-job push title `"<label> failed"` (lower-case state).

- [ ] **Step 1: Write the failing test** (append to `test_p0.py`)

```python
async def test_failed_job_push_uses_the_label(brain, pushes, monkeypatch):
    from brain import slurm
    t = brain.create_thread("hi-tts-v3", "EDIT TTS", "worktree")
    _job(brain, t["id"], job_id="9002", label="v3_gate", state="RUNNING")
    monkeypatch.setattr(brain.watcher, "poll", lambda jobs, force: [slurm.JobUpdate("9002", "FAILED", 1, "")])
    await brain.tick()
    assert ("failed", "v3_gate failed") in [(k, ti) for k, ti, _ in pushes]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_p0.py::test_failed_job_push_uses_the_label -q`
Expected: FAIL. The title is `brain.x.y failed`. (`slurm.JobUpdate(job_id, state, exit_code, reason)` is the same object `watcher.poll` returns.)

- [ ] **Step 3: Implement.** In `tick`, change

```python
                    self._notify(j["thread_id"], "failed", f"{j['name']} {u.state.lower()}",
```

to

```python
                    self._notify(j["thread_id"], "failed", f"{j['label'] or j['name']} {u.state.lower()}",
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_p0.py -q && pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/brain/core.py runtime/tests/test_p0.py
git commit -m "Failed-job push names the job by its script label"
```

---

### Task 3: Never auto-archive live work

`expire_threads` would archive EDIT TTS while it has 9 unrated listening tests.

**Files:**
- Modify: `runtime/brain/core.py` (`expire_threads`, around lines 889–911)
- Test: `runtime/tests/test_p0.py`

**Interfaces:**
- Produces: a thread is spared from expiry while it has a pending listening test, or a job that is not terminal or that changed within the TTL.

- [ ] **Step 1: Write the failing test**

```python
def test_expiry_spares_pending_listening_tests_and_recent_jobs(brain):
    brain.settings.raw["thread_ttl_days"] = 3
    listen = brain.create_thread("hi-tts-v3", "listen", "worktree")
    job = brain.create_thread("hi-tts-v3", "job", "worktree")
    plain = brain.create_thread("hi-tts-v3", "plain", "worktree")
    brain.db.x("INSERT INTO listening_tests(id, thread_id, path, title, n_items, n_systems, created_at)"
               " VALUES ('lt1', ?, '/x', 't', 5, 2, 0)", (listen["id"],))
    _job(brain, job["id"], job_id="9003")  # finished just now
    old = time.time() - 5 * 86400
    for x in (listen, job, plain):
        brain.db.x("UPDATE threads SET updated_at=? WHERE id=?", (old, x["id"]))
    assert brain.expire_threads() == [plain["id"]]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_p0.py::test_expiry_spares_pending_listening_tests_and_recent_jobs -q`
Expected: FAIL. All three threads are archived.

- [ ] **Step 3: Implement.** In `expire_threads`, change the query to

```python
        cutoff = now() - ttl * 86400
        rows = self.db.q(
            "SELECT id FROM threads t WHERE archived=0 AND pinned=0 AND state='idle' AND updated_at < ?"
            " AND NOT EXISTS (SELECT 1 FROM approvals a WHERE a.thread_id=t.id AND a.state='pending')"
            " AND NOT EXISTS (SELECT 1 FROM waits w WHERE w.thread_id=t.id AND w.state='open')"
            " AND NOT EXISTS (SELECT 1 FROM listening_tests l WHERE l.thread_id=t.id AND l.state='pending')"
            " AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.thread_id=t.id AND (j.updated_at >= ? OR j.state NOT IN (%s)))"
            % ",".join("?" * len(slurm.TERMINAL)),
            (cutoff, cutoff, *slurm.TERMINAL))
```

Update the docstring: "…and threads with an open wait, a pending approval or listening test, or a job active within the TTL never expire."

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_p0.py tests/test_qol.py -q && pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/brain/core.py runtime/tests/test_p0.py
git commit -m "Expiry spares threads with pending listening tests or recent jobs"
```

---

### Task 4: Quiet hours with a morning digest

`quiet_hours` exists in config but nothing applies it. A listening push went out at 03:40 IST.

**Files:**
- Modify: `runtime/brain/db.py` (SCHEMA: add `digest`)
- Modify: `runtime/brain/core.py` (`_notify` → `_push_all`; `_quiet_now`; `_flush_digest`; call in `tick`)
- Test: `runtime/tests/test_p0.py`

**Interfaces:**
- Produces:
  - `Brain._quiet_now(at: time.struct_time | None = None) -> bool`
  - `Brain._push_all(kind, title, body, data) -> None` (sends to every device; no mute or quiet checks)
  - `Brain._flush_digest() -> int`, which returns the number of held items sent as one push of kind `digest`.
  - The settings key `quiet_hours_enabled` (default `True`).

- [ ] **Step 1: Write the failing tests**

```python
def _st(hh, mm):
    return time.struct_time((2026, 10, 15, hh, mm, 0, 3, 288, -1))


def test_quiet_window_wraps_midnight(brain):
    brain.settings.quiet_hours = ("23:00", "07:00")
    assert brain._quiet_now(_st(3, 40)) and brain._quiet_now(_st(23, 0))
    assert not brain._quiet_now(_st(7, 0)) and not brain._quiet_now(_st(22, 59))
    brain.settings.raw["quiet_hours_enabled"] = False
    assert not brain._quiet_now(_st(3, 40))


def test_quiet_hours_hold_everything_but_approvals_then_send_one_digest(brain, monkeypatch):
    sent = []
    monkeypatch.setattr(brain, "_push_all", lambda kind, title, body, data: sent.append((kind, title)))
    t = brain.create_thread("hi-tts-v3", "EDIT TTS", "worktree")
    muted = brain.create_thread("hi-tts-v3", "muted", "worktree")
    brain.patch_thread(muted["id"], muted=True)
    monkeypatch.setattr(brain, "_quiet_now", lambda at=None: True)
    brain._notify(t["id"], "needs_you", "Ready to listen: V3 vs V1", "5 items", {"thread_id": t["id"]})
    brain._notify(t["id"], "approval", "Push to main", "git push", {"thread_id": t["id"]})
    brain._notify(muted["id"], "done", "muted thing", "x", {"thread_id": muted["id"]})
    assert sent == [("approval", "Push to main")]
    monkeypatch.setattr(brain, "_quiet_now", lambda at=None: False)
    assert brain._flush_digest() == 1
    assert sent[-1][0] == "digest" and "1 update" in sent[-1][1]
    assert brain._flush_digest() == 0  # sent once
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_p0.py -k quiet -q`
Expected: FAIL with `AttributeError: 'Brain' object has no attribute '_quiet_now'`.

- [ ] **Step 3: Add the table.** In `runtime/brain/db.py`, append to `SCHEMA`, just after the `ledger` table:

```sql
CREATE TABLE IF NOT EXISTS digest (
  ts REAL NOT NULL,
  thread_id TEXT,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL
);
```

- [ ] **Step 4: Implement in `core.py`.** Replace `_notify` with:

```python
    def _notify(self, thread_id: str, kind: str, title: str, body: str, data: dict) -> None:
        t = self.thread(thread_id)
        if t and t["muted"] and kind not in ("approval", "needs_you"):
            return
        if kind != "approval" and self._quiet_now():  # approvals always ring; the rest waits for the morning
            self.db.x("INSERT INTO digest(ts, thread_id, kind, title, body) VALUES (?,?,?,?,?)",
                      (now(), thread_id, kind, title, body))
            return
        self._push_all(kind, title, body, data)

    def _push_all(self, kind: str, title: str, body: str, data: dict) -> None:
        for d in self.db.q("SELECT * FROM devices WHERE revoked=0"):
            if d["fcm_token"] or not d["web_push"]:  # native app (or the dry-run outbox when nothing is registered)
                self.push.send(d["fcm_token"], kind, title, body, data)
            if d["web_push"]:  # PWA
                self._send_web(d["id"], json.loads(d["web_push"]), kind, title, body, data)

    def _quiet_now(self, at: time.struct_time | None = None) -> bool:
        if not self.settings.raw.get("quiet_hours_enabled", True) or len(self.settings.quiet_hours) != 2:
            return False
        lt = at or time.localtime()
        cur = lt.tm_hour * 60 + lt.tm_min
        start, end = ((int(h) * 60 + int(m)) for h, m in (x.split(":") for x in self.settings.quiet_hours))
        return start <= cur < end if start < end else (cur >= start or cur < end)

    def _flush_digest(self) -> int:
        """After quiet hours: one push summarising what was held. Returns how many items it covered."""
        if self._quiet_now():
            return 0
        rows = self.db.q("SELECT title FROM digest ORDER BY ts")
        if not rows:
            return 0
        n = len(rows)
        self._push_all("digest", f"While you were away: {n} update{'s' if n != 1 else ''}",
                       " · ".join(r["title"] for r in rows[:3])[:240], {})
        self.db.x("DELETE FROM digest")
        return n
```

Then, in `tick`, add `self._flush_digest()` as the line after `self.expire_threads()`.

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_p0.py -q && pytest -q`
Expected: PASS. The earlier tests in this file patch `_notify` itself, so they are unaffected.

- [ ] **Step 6: Commit**

```bash
git add runtime/brain/db.py runtime/brain/core.py runtime/tests/test_p0.py
git commit -m "Quiet hours: hold non-approval pushes and send one morning digest"
```

---

### Task 5: An honest "since you left"

The server always used now−12 h. The app's finished/failed counts came from `pending.jobs`, which holds only running jobs, so those counts never showed.

**Files:**
- Modify: `runtime/brain/core.py` (`pending`)
- Modify: `runtime/brain/api.py` (`/v1/pending`)
- Modify: `mobile/src/api.ts`, `mobile/src/types.ts`, `mobile/app/(tabs)/index.tsx`
- Test: `runtime/tests/test_p0.py`

**Interfaces:**
- Produces:
  - `Brain.pending(since: float | None = None) -> dict`, where `since.ts` is the window start in epoch seconds. `since` is clamped to the range from 7 days ago to now; `None` means now−12 h.
  - `GET /v1/pending?since=<epoch seconds>`, which answers 422 on non-numbers. That is FastAPI's default for a `float` query parameter.
  - `api.pending(sinceMs?: number)` in the app.

- [ ] **Step 1: Write the failing test**

```python
def test_pending_uses_the_clients_left_at_and_clamps_it(brain):
    t = brain.create_thread("hi-tts-v3", "EDIT TTS", "worktree")
    _job(brain, t["id"], job_id="9004", state="COMPLETED")
    _job(brain, t["id"], job_id="9005", state="FAILED")
    left = time.time() - 60
    s = brain.pending(left)["since"]
    assert abs(s["ts"] - left) < 1 and s["jobs_finished"] == 1 and s["jobs_failed"] == 1
    assert brain.pending(time.time() + 3600)["since"]["ts"] <= time.time()  # a phone clock in the future
    assert brain.pending(0)["since"]["ts"] >= time.time() - 7 * 86400 - 1  # at most a week back
    with TestClient(create_app(brain, dev_no_auth=True)) as c:
        assert c.get(f"/v1/pending?since={left}").json()["since"]["jobs_finished"] == 1
        assert c.get("/v1/pending?since=abc").status_code == 422
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_p0.py::test_pending_uses_the_clients_left_at_and_clamps_it -q`
Expected: FAIL with `TypeError: pending() takes 1 positional argument`.

- [ ] **Step 3: Implement the server side.**
  - In `core.py`, change the signature to `def pending(self, since: float | None = None) -> dict:` and replace `since = now() - 12 * 3600` with:

    ```python
        since = now() - 12 * 3600 if since is None else min(max(float(since), now() - 7 * 86400), now())
    ```

  - In `api.py`, replace the `pending` route body:

    ```python
    @app.get("/v1/pending")
    def pending(since: float | None = None, device: str = Depends(auth)) -> dict:
        return brain.pending(since)
    ```

- [ ] **Step 4: Run the server tests**

Run: `pytest tests/test_p0.py -q && pytest -q`
Expected: PASS.

- [ ] **Step 5: Make the app use it.**

  In `mobile/src/api.ts`, change the `pending` line to:

  ```ts
  pending: (sinceMs?: number | null) =>
    authed<Pending>('GET', sinceMs ? `/v1/pending?since=${Math.floor(sinceMs / 1000)}` : '/v1/pending'),
  ```

  In `mobile/src/types.ts`, inside `Since`, replace `files_changed?: number;` with:

  ```ts
  edits?: number;
  ```

  In `mobile/app/(tabs)/index.tsx`, make three changes:

  1. Load `left_at` first and pass it:

  ```ts
  async function loadInbox(): Promise<InboxData> {
    const leftAt = await getMeta('left_at');
    const [pending, running] = await Promise.all([
      api.pending(leftAt ? Number(leftAt) : null),
      api.threads({ state: 'running', archived: 0 }),
    ]);
  ```

  Keep the rest of the function. Its return already includes `leftAt`.

  2. In `sinceProse`, replace the three lines that compute `done`, `failed` and `finished` from `d.pending.jobs` with server counts:

  ```ts
  const finished = s.jobs_finished ?? 0;
  const failedN = s.jobs_failed ?? 0;
  ```

  and update the three `parts.push(...)` job lines to use `failedN` where they used `failed.length`.

  3. Replace the `files_changed` line with:

  ```ts
  if (typeof s.edits === 'number' && s.edits > 0) parts.push(`${cap(word(s.edits))} ${s.edits === 1 ? 'edit' : 'edits'}.`);
  ```

  Keep the `ACTIVE` import: the job list further down (`runningJobs`, `doneJobs`) still uses it.

- [ ] **Step 6: Typecheck**

Run: `cd mobile && npm run typecheck`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add runtime/brain/core.py runtime/brain/api.py runtime/tests/test_p0.py mobile/src/api.ts mobile/src/types.ts "mobile/app/(tabs)/index.tsx"
git commit -m "Since you left: the server counts from the phone's left_at; the Inbox shows real job counts"
```

---

### Task 6: No false "write outside scope" approvals

All 3 such approvals so far were garbage, with targets `/'`, `~, ~` and `/, /wer.json.`. Heredoc bodies and quoted sed scripts were parsed as paths.

**Files:**
- Modify: `runtime/brain/policy.py` (`_parse`, `_write_targets`)
- Test: `runtime/tests/test_p0.py`

**Interfaces:**
- Produces: `policy._strip_heredocs(cmd: str) -> str`. Implausible targets are dropped: `/`, `~`, anything containing quotes or `<`/`>`.

- [ ] **Step 1: Write the failing test** with the three real commands, verbatim from Brain's DB:

```python
from brain.policy import PolicyContext, _out_of_scope_writes

CTX = PolicyContext(worktree="/w/t1", vault_roots=("/vaults",), scratch_roots=("/tmp",))
REAL_FALSE_POSITIVES = [
    'ls ~/.local/bin | grep -i -E "brain|cb"; env | grep -i brain | sed \'s/=.*TOKEN.*/=<redacted>/\' | head; ps -ef | grep -i brain | grep -v grep | head -5',
    "sed -i 's/x0_max_ratio: float = 1.0      # random x0 length ~ U{0..ratio\\*L1}/x0_max_ratio: float = 1.5      # random x0 length ~ U{0..ratio*L1}; >1 gives deletions/' edit_tts/edits.py && sed -i 's/x0_max_ratio: 1.0/x0_max_ratio: 1.5/' configs/base.yaml",
    "W=$PWD && cat > slurm/eval_sweep.sh <<'EOF'\n#!/bin/bash\n# Needs RUN (run dir) and STEP; writes $RUN/eval_<step>/<setting>/wer.json.\nset -eo pipefail\nEOF\necho done",
]


@pytest.mark.parametrize("cmd", REAL_FALSE_POSITIVES)
def test_real_false_positives_are_in_scope(cmd):
    assert _out_of_scope_writes(cmd, CTX) == []


def test_real_out_of_scope_writes_are_still_caught():
    assert _out_of_scope_writes("echo hi > /etc/motd", CTX) == ["/etc/motd"]
    assert _out_of_scope_writes("cat > /home/other/x.sh <<'EOF'\nhi\nEOF", CTX) == ["/home/other/x.sh"]
    assert _out_of_scope_writes("rm -rf ~/.config/foo", CTX) == ["~/.config/foo"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_p0.py -k "false_positives or still_caught" -q`
Expected: the false-positive cases FAIL and the still-caught cases PASS.

- [ ] **Step 3: Implement in `policy.py`.**

Add above `_parse`:

```python
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1([^\n]*)\n.*?\n\s*\2\s*(?=\n|$)", re.S)


def _strip_heredocs(cmd: str) -> str:
    """`cat > f <<'EOF' ... EOF`: keep `cat > f` (a real write), drop the body (text, not commands)."""
    return HEREDOC.sub(lambda m: m.group(3), cmd)


def _plausible(target: str) -> bool:
    return target not in ("/", "~") and not any(c in target for c in "'\"`<>")
```

In `_parse`, change the first line of the body to lex the stripped command:

```python
    lex = shlex.shlex(_strip_heredocs(cmd), posix=True, punctuation_chars=True)
```

and the fallback to `toks = _strip_heredocs(cmd).split()`.

In `_write_targets`, change the return to:

```python
    return [t for t in targets if not t.startswith(("/dev/", "&")) and _plausible(t)]
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_p0.py -q && pytest tests/test_spine.py tests/test_security.py -q && pytest -q`
Expected: PASS. The existing policy tests are unchanged.

- [ ] **Step 5: Commit**

```bash
git add runtime/brain/policy.py runtime/tests/test_p0.py
git commit -m "Policy: heredoc bodies and quoted sed scripts are not write targets"
```

---

### Task 7: The app picks Brain's default provider

**Files:**
- Modify: `runtime/brain/api.py` (`/v1/providers`)
- Modify: `mobile/src/types.ts` (`ProviderInfo`), `mobile/app/new.tsx`
- Test: `runtime/tests/test_p0.py`

**Interfaces:**
- Produces: each `/v1/providers` item gains `"default": bool`, true for `settings.default_provider`.

- [ ] **Step 1: Write the failing test**

```python
def test_providers_mark_the_default(brain):
    with TestClient(create_app(brain, dev_no_auth=True)) as c:
        ps = c.get("/v1/providers").json()
    assert [p["name"] for p in ps if p["default"]] == ["fake"]
```

- [ ] **Step 2: Run it.** Expected: FAIL with `KeyError: 'default'`.

- [ ] **Step 3: Implement.** In `api.py`:

```python
    @app.get("/v1/providers")
    def providers(device: str = Depends(auth)) -> list[dict]:
        return [{"name": n, "native_handoff": getattr(p, "hydrate", None) is not None,
                 "defer": getattr(p, "supports_defer", True), "default": n == brain.settings.default_provider}
                for n, p in brain.providers.items()]
```

In `mobile/src/types.ts`:

```ts
export type ProviderInfo = { name: Provider; native_handoff: boolean; defer: boolean; default?: boolean };
```

In `mobile/app/new.tsx`, replace

```ts
        if (usable.length && !usable.some((x) => x.name === provider)) setProvider(usable[0].name);
```

with

```ts
        const pick = usable.find((x) => x.default) ?? usable[0];
        if (pick) setProvider(pick.name);
```

- [ ] **Step 4: Run the tests and typecheck**

Run: `pytest tests/test_p0.py -q && pytest -q && cd ../mobile && npm run typecheck`
Expected: PASS, with no type errors.

- [ ] **Step 5: Commit**

```bash
git add runtime/brain/api.py runtime/tests/test_p0.py mobile/src/types.ts mobile/app/new.tsx
git commit -m "New thread starts on Brain's default provider"
```

---

### Task 8: Health reports missing provider binaries and sbatch

A 10-hour outage was "`claude` not found".

**Files:**
- Modify: `runtime/brain/core.py` (new `checks()` method), `runtime/brain/api.py` (`/v1/health`)
- Test: `runtime/tests/test_p0.py`

**Interfaces:**
- Produces: `Brain.checks() -> dict`, shaped `{"providers": {name: bool}, "sbatch": bool}`. `/v1/health` gains `"checks"`. A provider without a `binary` attribute, such as the fake provider, counts as present.

- [ ] **Step 1: Write the failing test**

```python
def test_health_reports_missing_binaries(brain, monkeypatch):
    class P:
        binary = "/nonexistent/claude"
    brain.providers["claude"] = P()
    c = brain.checks()
    assert c["providers"] == {"fake": True, "claude": False}
    assert c["sbatch"] is True  # tests put fakeslurm on PATH
    with TestClient(create_app(brain, dev_no_auth=True)) as cl:
        assert cl.get("/v1/health").json()["checks"]["providers"]["claude"] is False
```

- [ ] **Step 2: Run it.** Expected: FAIL with `AttributeError: checks`.

- [ ] **Step 3: Implement.**

In `core.py`, add `import shutil` to the stdlib imports at the top (after `import re`). `Path` is already imported. Then, near `ledger_today`, add:

```python
    def checks(self) -> dict:
        """What Brain needs on PATH. A missing CLI otherwise shows up only as failed turns."""
        def present(b: str | None) -> bool:
            return b is None or bool(shutil.which(b)) or Path(b).is_file()
        return {"providers": {n: present(getattr(p, "binary", None)) for n, p in self.providers.items()},
                "sbatch": bool(shutil.which("sbatch"))}
```

In `api.py`, in `health()`, add `"checks": brain.checks()` to the returned dict.

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_p0.py -q && pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/brain/core.py runtime/brain/api.py runtime/tests/test_p0.py
git commit -m "Health reports missing provider CLIs and sbatch"
```

---

### Task 9: Remove dead settings; tolerate old settings files

`max_hot_providers`, `idle_exit_seconds` and `archive_policy` are read but never acted on. The owner's `settings.yaml` contains `archive_policy: full` under each repo.

**Files:**
- Modify: `runtime/brain/config.py`, `runtime/brain/core.py:241` (`repo_view`)
- Test: `runtime/tests/test_p0.py`

**Interfaces:**
- Produces:
  - `Settings` no longer has `max_hot_providers` or `idle_exit_seconds`.
  - `RepoConfig` no longer has `archive_policy`.
  - Unknown repo keys are ignored on load and dropped on save.

- [ ] **Step 1: Write the failing test**

```python
def test_old_settings_files_still_load(tmp_path):
    import yaml
    from brain.config import Paths, Settings, ensure_root
    p = ensure_root(Paths(tmp_path / ".brain"))
    p.settings.write_text(yaml.safe_dump({
        "default_provider": "claude", "max_hot_providers": 4, "idle_exit_seconds": 300,
        "repos": [{"id": "edit-tts", "path": "/x", "archive_policy": "full", "vault_root": "/v"}]}))
    s = Settings.load(p)
    assert s.repos[0].id == "edit-tts" and s.repos[0].vault_root == "/v"
    s.save(p)
    saved = yaml.safe_load(p.settings.read_text())
    assert "archive_policy" not in saved["repos"][0]
    assert "max_hot_providers" not in saved and "idle_exit_seconds" not in saved
```

- [ ] **Step 2: Run it.** Expected: the save assertions FAIL. If `archive_policy` has already been removed from `RepoConfig`, the load fails with `TypeError: unexpected keyword`.

- [ ] **Step 3: Implement in `config.py`.**
  - Delete the `archive_policy` field from `RepoConfig`.
  - Delete the `max_hot_providers` and `idle_exit_seconds` fields from `Settings`, and their lines in `load` and `save`.
  - In `load`, build repos tolerantly:

    ```python
        known = RepoConfig.__dataclass_fields__
        repos = [RepoConfig(**{k: v for k, v in r.items() if k in known}) for r in data.get("repos", [])]
    ```

  - In `save`, right after `data = dict(self.raw)`, add:

    ```python
        for dead in ("max_hot_providers", "idle_exit_seconds"):
            data.pop(dead, None)
    ```

  In `core.py` `repo_view`, remove `"archive_policy": r.archive_policy,`.

- [ ] **Step 4: Run the tests and typecheck.** The app might read `archive_policy`; `grep -rn archive_policy mobile/` finds nothing today.

Run: `pytest tests/test_p0.py -q && pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/brain/config.py runtime/brain/core.py runtime/tests/test_p0.py
git commit -m "Drop settings nothing reads; old settings files keep loading"
```

---

### Task 10: Named PWA devices; "Brain moved" reaches the PWA

All 9 PWA devices are named "phone". `set_endpoint` sends only FCM, and the owner's device has `web_push` only.

**Files:**
- Modify: `mobile/src/format.ts`, `mobile/app/pair.tsx`
- Modify: `runtime/brain/core.py` (`set_endpoint`)
- Test: `runtime/tests/test_p0.py`

**Interfaces:**
- Produces:
  - `webDeviceName(ua: string): string` in `mobile/src/format.ts`, e.g. `"Chrome · Android"`.
  - `set_endpoint` sends a visible web push of kind `moved` to every device with `web_push`.

- [ ] **Step 1: Write the failing server test**

```python
def test_endpoint_change_reaches_pwa_devices(brain, monkeypatch):
    import json as _json
    brain.db.x("INSERT INTO devices(id, name, public_key, revoked, created_at, last_seen, web_push, approved)"
               " VALUES ('d1','K','pk',0,0,0,?,1)", (_json.dumps({"endpoint": "https://push.example/x", "keys": {}}),))
    web = []
    monkeypatch.setattr(brain, "_send_web", lambda did, sub, kind, title, body, data: web.append((kind, body)))
    brain.set_endpoint("https://brain.example.org")
    assert web == [("moved", "https://brain.example.org")]
```

- [ ] **Step 2: Run it.** Expected: FAIL, because `web == []`.

- [ ] **Step 3: Implement.** In `set_endpoint`'s device loop, after `self.push.send(...)`, add:

```python
            if d["web_push"]:  # the PWA has no silent transport channel: tell the owner where Brain went
                self._send_web(d["id"], json.loads(d["web_push"]), "moved", "Brain moved", endpoint, {"endpoint": endpoint})
```

- [ ] **Step 4: Name PWA devices.**

In `mobile/src/format.ts`, add:

```ts
/** "Chrome · Android" from a user agent, for the pairing name on the web (Device.deviceName is null there). */
export function webDeviceName(ua: string): string {
  const browser = /Edg\//.test(ua) ? 'Edge' : /Firefox\//.test(ua) ? 'Firefox' : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : 'Browser';
  const os = /Android/.test(ua) ? 'Android' : /iPhone|iPad/.test(ua) ? 'iOS' : /Windows/.test(ua) ? 'Windows' : /Mac OS X/.test(ua) ? 'macOS' : /Linux/.test(ua) ? 'Linux' : 'web';
  return `${browser} · ${os}`;
}
```

In `mobile/app/pair.tsx`:
- Import `Platform` from `react-native` if it isn't imported already.
- Import `webDeviceName` from `@/src/format`.
- Change the name line to:

```ts
      const name = Device.deviceName ?? Device.modelName ?? (Platform.OS === 'web' ? webDeviceName(navigator.userAgent) : 'phone');
```

- [ ] **Step 5: Run the tests and typecheck**

Run: `cd runtime && pytest tests/test_p0.py -q && pytest -q && cd ../mobile && npm run typecheck`
Expected: PASS, with no type errors.

Check the name function by hand once in Chrome devtools: paste `webDeviceName(navigator.userAgent)` into the console of the dev build (`BRAIN_DEV_NO_AUTH=1 brain serve --port 8765` on a throwaway `BRAIN_ROOT`). Expect `Chrome · Linux` on the workstation.

- [ ] **Step 6: Commit**

```bash
git add runtime/brain/core.py runtime/tests/test_p0.py mobile/src/format.ts mobile/app/pair.tsx
git commit -m "PWA devices get a real name; an endpoint change reaches PWA devices as a visible push"
```

---

### Task 11: Keep a named Cloudflare tunnel alive

This task needs the owner. Creating a named tunnel needs their Cloudflare login and a domain (`deploy/relay.md` §2). Once that exists, keep-alive keeps it running and the PWA origin stops changing, so re-pairing ends.

**Files:**
- Modify: `deploy/keepalive.sh`, `deploy/relay.md`

**Interfaces:**
- Produces: when `$BRAIN_ROOT/tunnel.yml` exists, keep-alive starts `cloudflared tunnel --config $BRAIN_ROOT/tunnel.yml run` if it is not already running. The check runs on every cron tick, before the lease check, so the tunnel restarts even while Brain is healthy.

- [ ] **Step 1: Add to `deploy/keepalive.sh`**, right after the `DISABLED` check:

```bash
TUNNEL_CFG="$BRAIN_ROOT/tunnel.yml"
if [ -f "$TUNNEL_CFG" ] && command -v cloudflared >/dev/null; then
  if ! pgrep -u "$(id -u)" -f "cloudflared tunnel --config $TUNNEL_CFG run" >/dev/null; then
    echo "$(date -Is) starting named tunnel"
    setsid cloudflared tunnel --no-autoupdate --config "$TUNNEL_CFG" run >> "$BRAIN_ROOT/cloudflared.log" 2>&1 < /dev/null &
  fi
fi
```

The `pgrep` pattern must match the command line, including `--no-autoupdate`. Use `pgrep -u "$(id -u)" -f "cloudflared tunnel .*--config $TUNNEL_CFG run"` so it does.

- [ ] **Step 2: Syntax check and a stubbed run**

Run:

```bash
bash -n deploy/keepalive.sh
tmp=$(mktemp -d); mkdir -p $tmp/bin $tmp/root
printf '#!/bin/bash\nsleep 30\n' > $tmp/bin/cloudflared; chmod +x $tmp/bin/cloudflared
touch $tmp/root/tunnel.yml $tmp/root/DISABLED
BRAIN_ROOT=$tmp/root PATH=$tmp/bin:$PATH bash deploy/keepalive.sh; echo "exit $?"
```

Expected: `disabled` is printed, because the `DISABLED` check comes first and must still win. Then remove `DISABLED`:

```bash
rm $tmp/root/DISABLED
echo '{"heartbeat": 9999999999, "host": "x"}' > $tmp/root/brain.lease
BRAIN_ROOT=$tmp/root PATH=$tmp/bin:$PATH bash deploy/keepalive.sh
```

The fresh lease means Brain is not started. Expect `starting named tunnel` and a `cloudflared` stub process (`pgrep -f "$tmp/root/tunnel.yml"`). A second run prints nothing more. Finish with `pkill -f "$tmp/root/tunnel.yml"; rm -rf $tmp`.

- [ ] **Step 3: Document the owner's one-time steps.** Append to `deploy/relay.md` §2:

```markdown
**Once, on the login node (needs your Cloudflare account and a domain):**
1. `cloudflared tunnel login`
2. `cloudflared tunnel create brain`
3. `cloudflared tunnel route dns brain brain.<your-domain>`
4. Write `~/.brain/tunnel.yml`:
   ```yaml
   tunnel: brain
   credentials-file: /home/<you>/.cloudflared/<tunnel-id>.json
   ingress:
     - hostname: brain.<your-domain>
       service: http://127.0.0.1:8765
     - service: http_status:404
   ```
5. `brain endpoint set https://brain.<your-domain>`. The phone gets a "Brain moved" push. Pair once more at the new address; it never changes again.

Keep-alive (cron) restarts the tunnel if it dies. Stop the quick tunnel once the named one works.
```

- [ ] **Step 4: Commit**

```bash
git add deploy/keepalive.sh deploy/relay.md
git commit -m "Keep-alive keeps a named Cloudflare tunnel running when configured"
```

- [ ] **Step 5: Hand-off (the owner does this).** Ask the owner to run the five steps above. Do not touch the running quick tunnel or `~/.brain` yourself.

---

## Done when

- `pytest -q` is green, with all `test_p0.py` tests passing, and `npm run typecheck` is clean.
- On a throwaway Brain (`BRAIN_ROOT=$(mktemp -d)`):
  - a woken turn produces one `done` line in `push-outbox.jsonl`;
  - a failed turn produces one `failed` line;
  - with `quiet_hours` set around the current time, non-approval pushes land in `digest` and are sent as one push afterwards.
- The owner has a stable `https://brain.<domain>` (Task 11 hand-off). This is the only step that needs them.

## Later plans (written after this one lands, one per phase, from the same spec)

1. `2026-10-08` Codex spec **Part 2**: durable queue, startup recovery, one `_finish_turn`. This is a prerequisite for P1–P3.
2. **P1, structure:** repo sessions, code versions, change requests, work threads on read-only checkouts, vault layout, provenance, EDIT TTS migration, listening dismiss, job progress and logs.
3. **P2, truth layer:** `brain status` / `brain finding`, `results.json`, progress tail, failure classifier, `GET /v1/standup` + WebSocket, and `/status`.
4. **P3, Cinnamon shell:**
   - the roll, the world engine, Bakery and Forest;
   - standup, project screen, trays;
   - the menu: World, Companion, Quota, Rules, Quiet hours, Connection, This phone (rename, TTL);
   - `<Live>` gate, CSP, fonts.
5. **P4, the rest:** Rustic, Space and Tide; search; usage; history paging; rename and edit-resend; cleanup actions.
