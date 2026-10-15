# Codex over one shared app-server, and turn state that survives restarts

Date: 2026-10-08 · Status: design, awaiting owner review

## 1. Goal

Mirror each model's own default for a message sent while it works, and never lose work or messages:

- **Codex steers.** A message sent mid-turn reaches the running Codex turn at its next step, as in the Codex TUI. Brain
  drives Codex through **one shared `codex app-server`** for all threads (owner's choice), replacing `codex exec`.
- **Nothing is silently lost.** Queued messages, running turns and their processes survive or are cleanly closed across
  Codex server crashes, stalls, Brain restarts (planned 12 h exits and crashes).
- **Safety does not regress.** Brain's PreToolUse hook (repo/vault scope, approvals) gates every Codex tool call exactly
  as today; anything that cannot be attributed to a thread is denied.

Owner decisions: one shared app-server; keep `codex exec` as a switchable fallback; a turn cut off by a restart is marked
and offered Retry (no automatic re-run); Stop = Codex's native interrupt **and** kill that thread's still-running
commands; OmniRoute not used (it routes model API calls; it cannot steer, stop or supervise a CLI).

Non-goals: changing Claude Code / Antigravity adapters beyond process-group bookkeeping; UI redesign; OmniRoute.

## 2. Verified facts (codex-cli 0.153.4, real app-server, scratch CODEX_HOME)

| # | Behaviour | Observed | Design consequence |
|---|---|---|---|
| F1 | `turn/steer` mid-command and mid-text | delivered at the next step / immediately; same turn changes its answer | steering is native |
| F2 | steer with stale `expectedTurnId`, or after the turn ended | JSON-RPC error `-32600` ("expected active turn id…", "no active turn to steer") | on error, queue the message |
| F3 | `turn/start` while a turn is active | **silently steers** the active turn (returns the *same* turn id, no new turn) | never trust `turn/start` to start a turn; compare returned id |
| F4 | `turn/interrupt` | turn ends `interrupted` in ~1 s; **running commands keep running** (Codex tells the model "may still be running in the background") | Brain kills the thread's commands after the interrupt |
| F5 | liveness request (`thread/loaded/list`) during a running command | answers in < 10 ms | liveness probe cannot false-alarm on long commands |
| F6 | 4 concurrent threads on one server | every `item/*` and `turn/*` notification carries `threadId` | route by `threadId` |
| F7 | per-thread env via `config.shell_environment_policy.set` at `thread/start` and `thread/resume` | commands of each thread see their own `BRAIN_THREAD_ID` | `brain submit` attribution keeps working |
| F8 | hook process env | hooks run in the **server's** env (no per-thread vars); hook stdin has `session_id` (= Codex thread id) and `turn_id` | Brain maps `session_id` → thread; unknown → deny |
| F9 | hook trust | hooks run only if `[hooks.state."<key>"] trusted_hash = "<sha256>"` is in `$CODEX_HOME/config.toml`; `-c` overrides do not work; trust is **re-read live** (removing it un-trusts a running server); untrusted hooks are skipped silently | write trust for Brain's own hook only; verify before every turn; fail closed |
| F10 | hook deny under app-server | command never starts; turn continues | policy semantics unchanged |
| F11 | `kill -9` of the server mid-turn; resume on a new server | resume works; that turn reads `interrupted`; env re-applied on resume | crash recovery = resume + mark turn interrupted |
| F12 | `thread/resume` of an already-loaded thread | succeeds (idempotent) | always resume before a turn; no "loaded" tracking needed |
| F13 | `thread/inject_items` on a loaded thread | model sees the injected history | hydration uses `inject_items`, not rollout-file appends |
| F14 | SIGTERM to server, or SIGKILL of its process group | server exits, no orphaned commands (sandboxed commands run under `bwrap --die-with-parent`) | stop the server by its group |
| F15 | server spawned with `PR_SET_PDEATHSIG=SIGTERM`, parent `kill -9`'d | server and its command die within seconds | no orphaned, ungated server after a Brain crash |
| F16 | per-thread command kill by `/proc/<pid>/environ` containing exactly `BRAIN_THREAD_ID=<id>` among the server's descendants | kills that thread's command even after `cd /tmp`; leaves a thread whose id is a prefix-sibling untouched; all environs readable | Stop kills commands by env match (substring/cwd matching was shown to be wrong) |
| F17 | `turn/completed` error info | typed `codexErrorInfo` (`usageLimitExceeded`, `rateLimitExceeded`, …) | usage-limit detection without regex |

## 3. Architecture

```
Brain ── CodexServer (one per Brain) ── stdio JSON-RPC ── codex app-server (own process group, PDEATHSIG)
  │         ├─ reader task: responses → futures; notifications → per-thread router
  │         ├─ supervisor: start/ready/liveness/restart/circuit breaker
  │         └─ trust keeper: Brain's hook trusted, verified pre-turn
  └─ CodexAppServerProvider (provider API: run_turn, steer, interrupt, native_items, hydrate)
        └─ per thread: lock, active turn record, event mapping → ctx.emit
```

- `runtime/brain/providers/codex_server.py` — `CodexServer`: process, JSON-RPC, supervision, trust. No Brain
  knowledge beyond a callback for notifications and the paths it needs.
- `runtime/brain/providers/codex_appserver.py` — `CodexAppServerProvider`: the provider contract on top of one
  `CodexServer`; event mapping lifted from today's `codex.py` (`item/*` instead of `exec --json`).
- `runtime/brain/providers/codex.py` — unchanged `exec` adapter, selected by `codex_transport: exec`.
- `runtime/brain/core.py` — durable queue, turn finalisation, startup recovery, Stop kill, hook thread resolution.
- `runtime/brain/procs.py` (new, small) — process-tree helpers: descendants, env-match kill, pdeathsig spawn.

### 3.1 One turn

1. Under the thread's lock: `CodexServer.ensure_ready()` (§4).
2. `trust_ok()` (F9) — `hooks/list` for the thread's cwd must show Brain's hook `trusted` with the expected command;
   otherwise the turn **does not start**: `turn.ended(stop_reason=error)`, notice "Codex safety hook is not trusted;
   not running unguarded", push to owner.
3. `thread/resume` (or `thread/start` for a new thread) with `cwd`, `approvalPolicy: never`, `developerInstructions`,
   `config.shell_environment_policy.set` = the thread's `BRAIN_*` env (F7, F12). New thread id → `provider_sessions`
   **before** the first turn starts (the hook needs the mapping, F8).
4. Missing history (handoff): `thread/inject_items` (F13), then `turn/start` with per-turn `sandboxPolicy`
   (workspace-write over the thread's scope roots + network; dangerFullAccess for an approved elevated turn).
5. **F3 guard:** if the returned turn id equals a turn Brain already has as active for this thread, Brain treats the
   call as a steer of that turn (it is) and does not create a second turn record. If Brain had no active turn but the
   server returns an in-progress turn it did not start (state drift), Brain interrupts it, kills its commands, and
   retries `turn/start` once.
6. Notifications for the thread's codex id feed `ctx.emit`; `turn/completed` resolves the turn: status
   `completed` → `end_turn`; `interrupted` → `cancelled` when the owner pressed Stop, `stalled` when the stall
   watchdog stopped it (§4), `interrupted` when the server died; `failed` → `error`, and
   `codexErrorInfo` `usageLimitExceeded`/`rateLimitExceeded` → existing quota-pause flow with reset time from the
   latest `account/rateLimits/updated` (fallback +1 h).

### 3.2 Steer
`steer(thread, text)`: under the lock, if an active turn id exists → `turn/steer(expectedTurnId)`. Success → True
(Brain records it as "sent into the running turn"). Error `-32600` or no active turn → False → durable queue (F2).

### 3.3 Stop (owner decision: native + kill)
1. `turn/interrupt` (Codex's own stop, F4). Wait ≤ 15 s for `turn/completed`.
2. Kill every process group among the server's descendants whose environ contains exactly
   `BRAIN_THREAD_ID=<thread>` (F16), excluding the server's own group. Repeat once after 2 s (commands that were
   starting).
3. If the interrupt was not honoured in 15 s → the server is unhealthy (§4): kill its group, which ends every Codex
   turn (others are marked `interrupted`, Retry offered).

### 3.4 Hook attribution (all providers)
`/internal/hook` resolves the thread as: explicit `BRAIN_THREAD_ID` (Claude/agy/exec, unchanged) → else
`provider_sessions` lookup by `(provider='codex', native_session_id=session_id)`. **No thread → deny** with reason
"Brain could not attribute this call to a thread". (Today an empty thread id returns `none`, i.e. allow — fixed for
everyone.)

## 4. Stall handling and server supervision

States: `stopped → starting → ready ⇄ unhealthy → dead`, plus `circuit_open`.

| Failure | Detection | Action |
|---|---|---|
| Hang at start (the 2026-10-06 stall) | `initialize` + trust check not done in 30 s | kill group; restart with backoff 5 s, 30 s, 2 min |
| Server hung later | liveness `thread/loaded/list` every 30 s; 2 consecutive timeouts (10 s) (F5) | kill group → `dead` |
| Single turn silent | no notification for the turn for 10 min while no command item is in progress; a command item may run up to 60 min (`codex_command_max_minutes`) | Stop path (§3.3) with notice "Codex stopped responding"; turn `stalled`; push; Retry |
| Interrupt ignored | no `turn/completed` 15 s after `turn/interrupt` | treat as hung server |
| Server exits / crashes | reader EOF / process exit | every active Codex turn → `interrupted`; server restarts lazily on next need |
| JSON-RPC call never answers | per-call timeout 30 s (`turn/start` 60 s) | call fails; caller ends the turn with `error`; counts toward liveness |
| Flapping | 3 failed starts or 3 restarts in 15 min | `circuit_open`: Codex turns use `exec` (no steering), notice + push; probe the app-server again after 30 min |
| Brain crash | — | PDEATHSIG kills the server (F15); startup recovery (§5.3) handles the rest |

When the server dies, all of its threads lose their active turns at once; each is finalised individually (§5.2), so one
stall never leaves another thread stuck on "running".

## 5. State management

### 5.1 In memory (rebuilt per server generation)
- `CodexServer`: `generation`, process, status, `last_ok_at`, restart timestamps, `pending: {rpc_id: Future}`,
  `rate_limits` (latest notification).
- Per thread (`CodexAppServerProvider`): `asyncio.Lock`; `active = {brain_turn_id, codex_turn_id, generation,
  last_event_at, open_command_items}`; nothing else — resume is idempotent (F12), so "loaded" is never tracked.

### 5.2 Durable (SQLite, additive migrations)
- `turns`: + `codex_turn_id TEXT`, `transport TEXT`, `server_gen INTEGER`, `pgid INTEGER` (per-turn CLIs).
- `queued_messages(id, thread_id, kind 'user'|'wake', text, created_at)` — replaces the in-memory `_queued`; same
  semantics as today (drain after the turn; handed back on Stop, wakes kept; kept while paused; drained on resume).
- `meta`: `codex_server_pid`, `codex_server_pgid`, `codex_server_started_at` — to kill an orphan after a hard crash.
- `provider_sessions`: unchanged; written before the first Codex turn of a thread.

### 5.3 Invariants
1. At most one active turn per thread (thread lock + F3 guard).
2. Every Brain turn gets exactly one `turn.ended` (completed, error, cancelled, stalled, interrupted) — enforced by a
   single `_finish_turn` used by every path, including startup recovery.
3. A steer is sent only with the active codex turn id; anything else is queued durably.
4. A queued message is deleted only after the turn that carries it has **started** (`turn.started` committed), so a
   crash between drain and start re-drains it; duplicates are prevented by deleting rows in the same transaction that
   writes the `turns` row.
5. No Codex tool call runs unattributed or unhooked: trust verified before every turn; unknown session → deny.
6. Brain's hook trust is written only to Brain's own `CODEX_HOME/config.toml`, only for the hook entry whose command is
   exactly Brain's hook command; the owner's `~/.codex` is never touched.

### 5.4 Startup recovery (all providers)
1. Kill orphans: `meta.codex_server_pgid` if alive and its cmdline is `codex app-server`; each open turn's `pgid` if
   alive and its cmdline matches the provider binary (pid reuse guard).
2. Close every turn with `ended_at IS NULL` as `interrupted`: `turn.ended`, notice "Brain restarted during this turn",
   thread state to rest. Retry accepts `error`, `cancelled`, `stalled` and `interrupted` (all providers).
3. Drain each thread's `queued_messages` (unless paused).

### 5.5 Planned exits
- 12 h `--max-hours`: once the deadline passes, Brain exits at the first moment no turn is running (checked every
  30 s); after a further 2 h it exits anyway and startup recovery (§5.4) handles what was running.
- SIGTERM/SIGINT: native stop of every running turn (§3.3 / provider interrupt), then exit; queue rows stay.

## 6. Edge cases → handling → test

| Edge case | Handling | Test |
|---|---|---|
| Steer races turn end | F2 error → queue | fake server returns -32600 |
| Message arrives while `turn/start` in flight | thread lock; steer waits for the active id | concurrent send during start |
| `turn/start` hits a still-active turn (drift) | F3 guard | fake returns existing turn id |
| Stop during `thread/resume`/`turn/start` | lock; stop after start completes, then interrupt | stop issued mid-start |
| Stop + steer at once | lock order; steer after interrupt → queued | concurrent |
| Interrupt leaves commands | env-match kill (F16) | live smoke + fake process tree |
| Thread id prefix siblings | exact `KEY=value` match | `thr_A` vs `thr_A2` |
| Agent `cd`s away | env match, not cwd | `cd /tmp && sleep` |
| Server hangs at start / later / mid-turn | §4 | fake server that sleeps / stops reading |
| Server crash with 3 active threads | all finalised `interrupted` | fake exits |
| Flapping → circuit → exec | §4 | 3 forced failures; exec used; recovers after window |
| Trust missing / hash changed (venv moved) / removed while running | pre-turn check; re-trust only Brain's exact command; else refuse | edit config / hooks.json between turns |
| Hook call with unknown `session_id` | deny | direct `/internal/hook` call |
| Brain `kill -9` | PDEATHSIG + startup recovery | spawn/kill test |
| Brain restart with open turns and queued rows | §5.4 | seeded DB, fresh Brain |
| pid reuse after reboot | cmdline check before kill | fake meta pid → unrelated process |
| Crash between drain and turn start | invariant 4 | inject failure after drain |
| Model switch while messages queued | queue rows are thread-scoped; drained to the new provider | switch then drain |
| Pause during server restart | queue kept; no drain while paused | pause → restart → resume |
| Queued rows for an archived thread | drain unarchives (same as a new message) | archived thread |
| Usage limit | typed `codexErrorInfo` → quota wait with real reset time | fake `turn/completed` failed |
| Server-to-client approval requests (should not happen with `approvalPolicy: never`) | respond decline, log, notice | fake server request |
| Very large notifications | reader limit 16 MB; oversize line → server unhealthy, not a Brain crash | fake 20 MB line |

## 7. Testing

- **Unit/integration (pytest, no network):** `tests/fakecodexserver` — a scripted JSON-RPC app-server stand-in
  (Python) covering every row of §6, including process-tree cases with real child `sleep`s.
- **Restart tests:** seed `turns`, `queued_messages`, `meta` and orphan processes; start a fresh `Brain`; assert §5.4.
- **Live smoke (opt-in, `tests/smoke_codex_appserver.py`):** real `codex app-server` in a scratch `CODEX_HOME` copy
  (auth + config only): two concurrent threads, steer, stop-with-kill, hook deny, crash + resume, inject_items.
- Existing suites keep passing with `codex_transport: exec` and with the new default.

## 8. Rollout and rollback

1. Ship behind `codex_transport` (default `app-server`); `exec` stays one setting away.
2. On first start, Brain trusts its own hook (logged in the IR log with the hash).
3. Rollback: set `codex_transport: exec` and restart; durable queue/recovery are transport-independent.

## 9. Part 2 summary (durable queue, independent of Codex)

`queued_messages` table + startup recovery (§5.2–5.5) apply to all providers and can land first; the Codex app-server
work builds on them.
