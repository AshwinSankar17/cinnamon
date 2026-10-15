# Cinnamon: repo sessions, threads on frozen code, worlds, and a menu

Status: design for owner review · 2026-10-15
Mockups: https://claude.ai/artifact/GRQ6isFm5eR1bWT3h4f6f6 (page "Cinnamon · worlds, menu, structure")
Prerequisite: Part 2 of `2026-10-08-codex-app-server-and-durable-state-design.md` (durable queue, startup recovery, one `_finish_turn`).

## 1. Goal

Make Brain's structure match how the owner works, and give the phone app a reason to be opened.

1. **Structure.** A project is a repo plus a vault. One long-lived *repo session* is the only agent that edits source. *Threads* are long-lived workstreams of actions (train, evaluate, listen) that run on a *frozen code version* and keep every artifact in their own vault folder. When a thread needs a code change it files a *change request* with the repo session; a thread never switches into a code session.
2. **App.** Rebrand the UI as Cinnamon. The main character is a cinnamon roll whose swirl is the project's history and whose face is its state. The home is a standup: what each thread is doing, its latest verified finding, and what needs the owner. Switchable *worlds* (Bakery, Forest, Rustic, Space, Tide) put the roll in a different place, each with its own way of presenting threads, its own waiting card, a themed companion, its own type and motion. A menu in the bottom tray holds the world switcher, the companion switch, provider quota, rules, quiet hours, connection and sign-out.
3. **Quality of life.** Fix what the audit found: missing pushes, a broken "since you left", auto-archive of live work, unreadable job names, and more (§9).

Success, checked after two weeks of real use on EDIT TTS:
- The owner sends no status pings ("Status?" and the like). Today it's 12 of 47 messages.
- No eval wrapper or run config is committed to the repo. Today 23 of 80 commits are wrappers.
- Every job and every finding names the code version it ran on.
- Push to decision on an approval is no slower than today on the owner's phone.

## 2. Owner decisions (binding)

| Topic | Decision |
|---|---|
| Home | A standup of your agents: what each is doing now, latest finding, next. |
| Code ownership | One repo session per repo is the only writer of source. |
| Threads | Long-lived collections of actions on a frozen code version. Changes go to the repo session as requests. Context never switches inside a thread. |
| New code versions | The thread's agent decides whether its next action moves to the new version and records why. Running jobs never change version. |
| Repo session autonomy | Commits inside the repo without asking. Pushes, protected branches and anything outside the repo still ask. |
| Numbers | Verified only: a number appears only if Brain read it from a file a job wrote, or a provider reported it. Agent claims appear as words. An empty slot beats a guess. |
| Relay | Named Cloudflare tunnel (stable URL). |
| Platforms | Native parity: motion must work in the PWA and the native build (Reanimated + react-native-svg; no web-only tricks in the shipped path). |
| Look | Free-rein rebrand as Cinnamon. This supersedes the binding palette in `mobile/PRODUCT.md`, which must be updated. |
| Main character | A cinnamon roll, the same in every world. Its swirl is the project's history and its face is the project's state. |
| Themes | Worlds are places and experiences, not colour packs: Bakery, Forest, Rustic, Space, Tide. Each has a day and a night, its own scene, its own way of showing threads and waiting items, and its own type and motion. |
| Companions | One themed pet per world (sparrow, squirrel, cat, eight-armed alien, fish), switched on or off from the menu. |
| Menu | A hamburger in the bottom tray: sign out, world switching, provider quota, rules, and more. |
| Showcase | A switch that hides names and numbers so the owner can show the app off. |

## 3. The model

### 3.1 Today (verified in code and data)

- **Repo:** a registered git repo with an optional `vault_root` outside it.
- **Vault:** two meanings. The repo's folder (`vaults/edit-tts/`, the app's "shared vault") and, only for threads created as training runs, a subfolder `<title>-<id8>` (the "run vault").
- **Thread:** one agent conversation in one of three modes picked at creation: read-only, own branch (git worktree), or training run (branch + run vault). Read-only threads run on the live checkout (`core.py:189`), so code can change under a running job.
- **Job outputs:** the run vault if there is one; otherwise the shared `vault/runs/run_<hex>` (86 of 107 entries in EDIT TTS); otherwise `.brain-runs` in the worktree.
- **What actually happened on EDIT TTS:** one thread, 80 commits on `brain/e5514867-edit-tts` (main has 1), 23 of them eval-wrapper scripts. The agent froze code by hand nine times in `vault/edit-tts/code/` (`round1_41be262` … `round3g_f1a01a6`). It invented this model; Cinnamon makes it the structure.

### 3.2 Cinnamon

```
project edit-tts = repo + vault
├─ repo session            (thread kind = repo; only writer of source)
│    works on branch brain/edit-tts-session in its own worktree
│    inbox: change requests → decide → commit → freeze vN
├─ code versions v1..vN    (immutable read-only checkouts in the vault)
└─ threads                  (thread kind = work; long-lived)
     each pinned to one code version; actions = jobs, evals, listening tests
     outputs → vault/<repo>/threads/<slug>-<id8>/
```

Vault layout for new work. Nothing that exists is moved; 226 GB stays where it is.

```
vault/<repo>/
├─ code/v9-round3g-f1a01a6/      read-only checkout (git archive + chmod a-w)
├─ threads/<slug>-<id8>/
│  ├─ configs/                   run and eval scripts (were repo commits)
│  ├─ runs/<label>-<run8>/       sbatch outputs, ckpts, log.jsonl, results.json
│  └─ listen/
└─ (existing hf/, envs/, tools/ stay as they are; shown as "shared")
```

### 3.3 Lifecycle

1. **New project:** creates the repo, the vault and the repo session. The first message goes to the repo session.
2. **New thread:** pick a project, a code version (default: latest), a name and a first instruction. Brain creates the thread folder and sets `BRAIN_CODE_DIR` (read-only), `BRAIN_THREAD_DIR` and `BRAIN_CODE_VERSION`. The agent's cwd is the thread folder. Brain puts `BRAIN_CODE_DIR` first on `PYTHONPATH`, and scripts call code by that path (`python $BRAIN_CODE_DIR/train.py`). `brain submit` runs jobs with the same env and cwd, and records `code_version_id` on the job.
3. **Change request:** the thread runs `brain request-change "<what and why>" [--evidence <path|job>…]`. Brain records it, wakes the repo session with it, and shows it on the project screen. The thread keeps working on its current version.
4. **Decision:** the repo session runs `brain decide <cr> accept|decline --why "…"`, makes the change, and runs `brain release [--name round3h] [--cr <cr>]`. Release freezes the session branch HEAD as vN+1 and posts `code.version` to every work thread of the repo.
5. **Adoption:** the woken thread decides. `brain adopt v10 --why "…"` moves its *next* actions to v10. Jobs already submitted keep their checkout path, so a running job never changes version.
6. **Merging to main** stays an owner decision. A push or protected-branch write asks, as today.

### 3.4 Enforcement

- **Work threads may write only to** their thread folder and scratch dirs. `BRAIN_CODE_DIR` is read-only on disk (`chmod -R a-w`) and outside the policy scope. Brain's policy (`policy.py` scope roots), Claude hooks, Codex `writable_roots` and agy permissions all get the same roots.
- **The repo session may write to** its worktree and run `brain release`. It may not write into any thread folder except to read evidence.
- **A work thread that runs `git commit`** in a code dir is denied with: "file a change request: `brain request-change`".
- **Python byte-code:** `PYTHONDONTWRITEBYTECODE=1` for work threads, so imports from a read-only tree don't fail.
- **Kill criterion:** if a real EDIT TTS job can't run from a read-only `git archive` checkout (editable installs, code that writes next to itself), fall back to a detached `git worktree` per version with policy-only enforcement, and record that in this spec.

### 3.5 Migration of what exists (idempotent, on a DB copy first)

- Register `vault/edit-tts/code/*` as v1–v9 in commit-date order. The name comes from the folder and the commit from the suffix (all nine verified against `git log`). Nothing on disk moves.
- Create the EDIT TTS repo session on the existing branch `brain/e5514867-edit-tts`. Its 80 commits are the history.
- The existing EDIT TTS thread becomes a work thread on v9 with `vault_dir` = the current vault root (legacy layout), so old jobs, listening tests and paths keep working.
- brain-sandbox gets a repo session. Archived test threads stay archived.

## 4. Data model (additive migrations)

| Table / column | Purpose |
|---|---|
| `threads.kind` `repo`\|`work` (default `work`) | Repo session vs. workstream. |
| `threads.code_version_id` | Pin for a work thread. NULL means legacy (live checkout). |
| `code_versions(id, repo_id, n, name, commit, path, created_at, cr_id, notes)` | Frozen versions. `path` is the read-only checkout. |
| `change_requests(id, repo_id, from_thread, text, evidence_json, state, decision, version_id, created_at, decided_at)` | `state` is one of open, accepted, declined, shipped. |
| `jobs.code_version_id`, `jobs.run_dir`, fill `time_limit`/`partition`/`gpus` from squeue `%l %P %b` | Provenance and progress. |
| `findings`, `thread_status` (council: `brain status`, `brain finding`) | Truth layer for the standup (§6). |
| `quota_snapshots(provider, fetched_at, source, raw_json)` | Menu → Quota (§7.3). |
| `devices.name` editable; UA-derived default ("Chrome · Android") | Today every PWA device is named "phone". |

Naming stays as it is internally: `brain`, `BRAIN_*`, `~/.brain`, hook names, `refs/brain` and signature domains are never renamed. "Cinnamon" is UI only, plus a `cinnamon` CLI alias.

## 5. API

- `GET /v1/repos/{id}/versions`, `GET /v1/repos/{id}/change-requests`, `GET /v1/change-requests/{id}`.
- `POST /v1/threads` gains `kind`, `code_version` (default latest) and loses `run` (every work thread has a folder).
- `GET /v1/standup` with `rev` = `MAX(events.rowid)`, plus WebSocket op `subscribe_standup` (council). Every field carries `as_of` and a source.
- `GET /v1/pending?since=<left_at>` (fixes the always-12 h "since you left").
- `GET /v1/quota`, `POST /v1/quota/refresh` (at most once per minute per provider).
- `GET /v1/rules`; `PATCH /v1/rules` for the owner's switches only (§7.4). Signed with the device key, like approvals.
- `GET /v1/devices` (own devices), `PATCH /v1/devices/self` (rename), `POST /v1/devices/self/revoke` (sign out).
- `GET /v1/threads/{tid}/file?tail=<bytes>`, and `run_dir` on `/v1/jobs`.
- `/v1/health` adds `features: ["standup","versions","quota"]`, so a new app can degrade against an older Brain.

## 6. Truth layer (from the council, unchanged in substance)

- **`brain status "<now>" [--progress a/b] [--next "<next>"]`** and **`brain finding "<claim>" --evidence <job|path>…`** are written by agents. The standup shows them with their age. If an agent skips them, the standup falls back to the latest event, with an age label.
- **`results.json`** (`{"schema":"brain.results/1","run","step","variant","eval_set","metrics":{…}}`) is written by each eval. A metric is shown only after Brain reads it from that file. If an agent's prose disagrees with the file, the app flags the mismatch instead of hiding it.
- **Progress** comes from the tail of `log.jsonl` (step/total) when present. Otherwise it shows elapsed against `time_limit`, labelled as wall time and never as a percentage.
- **The failure classifier** scans the last 200 log lines for OOM, NCCL/hardware, test gate, missing file, command not found, network, and timelimit. A FAILED job is not called a failed experiment.
- **Kill criterion:** if `brain status` is stale on more than 30% of turns in a week, the standup headline falls back to the agent's last message.

## 7. The app

### 7.1 Screens

1. **Standup (home).** One sentence per project ("The refinement study is evaluating both of its arms.") and "since you left" counts from `/v1/pending?since`. Below it is the world's scene: the roll (§7.2), the project's threads presented the world's way, and the companion. The thing that needs you sits at thumb height as the world's waiting card. Composer and menu live in the bottom tray.
2. **Project.** Repo session card: idle/working, a strand of code versions (tap a bead to read its changelog), change requests and their trail. Then threads, each with its code version, state chip, one-line now, and verified metrics. The composer addresses the repo session.
3. **Thread.** As today (chat, steps, job cards, approvals, listening), plus a header with code version, folder path and Files button. Job cards gain Log (tail), run dir, and attempt count.
4. **Trays:** approval, listening test, change request, finding peek. They open over the current screen. Dismissing a tray never decides anything.

### 7.2 The roll, its worlds and its companions

**The main character is a cinnamon roll.** It is the same roll in every world, and it is the project.

- **Its swirl is the history.** The swirl on top is the project timeline rolled up: 150 time bins from the centre (oldest) to the tail (now).
  - **Width** is smoothed activity per bin (turns and jobs).
  - **The stretch since you left** is drawn in a brighter cinnamon and traces itself in when you open the app.
  - **Sugar crystals** mark verified findings.
  - **A glowing tip** marks now.
- **Tap the roll** and it unrolls into the full timeline. The timeline is drawn from the same 150 points, with day labels, failed-job flecks, finding labels and the now tip.
- **Its face is the project's state.** Moods come only from Brain state; the agent never sets them.

  | Mood | Shown when |
  |---|---|
  | Needs you | Something waits on the owner (approval, listening test). Eyebrows up, eyes on the card at thumb height. |
  | Working | Jobs or a turn are running and nothing waits. Steam rises while they run. |
  | Proud | A verified finding arrived since the owner last looked. It shows once, then falls back to working or idle. |
  | Asleep | Brain or the tunnel is unreachable. All motion stops and values read "as of N min ago". |

**A world is a place with an experience, not a palette.** It decides:
- where the roll lives;
- what the threads become;
- how the thing waiting for you arrives;
- who keeps the roll company;
- the type and how everything moves.

What never changes across worlds: the headline sentence, the waiting card at thumb height, the composer and menu, the order of information, and every number.

| World | The roll | Threads appear as | Waiting for you | Companion (role) | Type (display · UI · mono) | Ambient |
|---|---|---|---|---|---|---|
| Bakery (default) | cools on a rack, iced; steams while jobs run | order tickets clipped to a rail | the ticket that says Order up | **Crumb** the sparrow: hops to the ticket when an order is up | Fraunces (SOFT) · Instrument Sans · DM Mono | flour motes in window light |
| Forest | on a stump under a great oak, leaf hat | wooden tags swinging from a branch | a note hung on twine | **Hazel** the squirrel: one acorn per finding | Young Serif · Hanken Grotesk · IBM Plex Mono | pollen by day, fireflies by night |
| Rustic | on a plate on a checked cloth, by the hearth | recipe cards pinned to a cork board, handwritten (Caveat) | a note with a red pin | **Nutmeg** the cat: sleeps by the fire; ears up and one eye open when needed | Alegreya · Alegreya Sans · Courier Prime | firelight flicker, snow outside |
| Space | floats in a helmet | satellites on the roll's orbit | an incoming transmission | **Anise**, eight-armed alien: one lit arm per running job, up to 8 | Unbounded · Sora · Martian Mono | stars twinkle, satellites drift, galaxy by night |
| Tide | rests in an open clam | bubbles, newest floating highest | a bottle washed up | **Saffron** the fish: swims laps, heads for what needs you | Fredoka · Figtree · DM Mono | kelp sway, rising bubbles, marine snow by night |

- **Day and night** are part of each world, not a recolour: sun or moon, lit windows, fireflies, the galaxy, the colour of the deep sea. Choices are Day, Night, and Match phone (follows the system setting).
- **Companions** are on by default and switch off in the menu. They only ever restate verified state:
  - Hazel's acorns are the finding count.
  - Anise's lit arms are running jobs.
  - Tapping a companion shows a one-line status built from the same verified numbers as the headline.

  A companion never decides anything, never blocks a control, and is hidden in reduced-motion mode if it would move.
- **Switching worlds** happens in this order:
  1. The new world's backdrop grows out of the menu tray as a circle (600 ms).
  2. The scene settles in (700 ms out-expo).
  3. The roll drops into its new home with a squash and settle (950 ms).
  4. The companion fades in 500 ms later.
- **A world is data plus one scene component:**
  - Data: tokens for day and night, three typefaces, a hero placement (position, scale, accessory: icing, leaf hat, helmet, clam), a thread presenter (ticket, tag, card, satellite, bubble), a waiting-card shape, a companion, an ambient recipe and a haptic.
  - Scene component: one react-native-svg component, ≤ 120 shapes.

  Adding a world means one scene file and one data entry; no screen changes.
- **Motion rules:**
  - At most three loops per screen: one scene ambient, one companion idle, and the live tip.
  - All loops share one clock and stop when Brain or the tunnel is unreachable, when the app is backgrounded, when a sheet covers the scene, and under reduced motion.
  - Tokens: tap 100 ms at scale .97; trays 500 ms `cubic-bezier(.32,.72,0,1)` with drag-to-dismiss and velocity; step slides 420 ms; reveal 420 ms out-expo.
- **Native parity.**
  - Scenes, the roll and companions are react-native-svg; patterns and gradients are supported there.
  - Loops animate transform and opacity only, through Reanimated on `G` wrappers, never per-path `animatedProps` at scale.
  - The world wipe is a scaling circle `View`, not `clip-path`.
  - The unroll morph interpolates the 150 points in a Reanimated derived value.
  - No SVG filters.
- **Fonts** are self-hosted. Only the active world's faces load at start. The World step shows each world's name in its own face, loading display faces lazily.
- **Showcase** swaps project names, thread names and numbers for generic copy. Companions say generic lines. Nothing else changes.
- **Haptics:** `expo-haptics` on native and `navigator.vibrate` (≤ 20 ms) on Android web, only for world switch, roll landing, approval signed, and reveal. No sound.

### 7.3 Menu (bottom tray)

A hamburger to the left of the composer morphs into ✕. The menu is one sheet whose height changes per step, with steps sliding in the direction of travel.

- **World:** carousel of world cards (a miniature of each place with the roll in it), Day / Night / Match phone, Showcase.
- **Companion** (switch, row): the current world's companion by name ("Hazel the squirrel"). Stored per device.
- **Quota:** one block per provider, each with gauges for 5-hour and weekly windows, reset times, credits, and the snapshot age. Sources, all verified on this machine on 2026-10-15 with no model turn:

  | Provider | Source | Fields |
  |---|---|---|
  | Claude Code 2.1.299 | `get_usage` control request on `claude -p --input-format stream-json` (self-described experimental; pin the version and parse defensively) | `five_hour`, `seven_day`, model-scoped buckets `{utilization, resets_at}`, credits |
  | Codex 0.153.4 | app-server `account/rateLimits/read` (stable surface); until the app-server spec lands, the last rollout `token_count.rate_limits`, labelled with its age | `primary`/`secondary {usedPercent, windowDurationMins, resetsAt}`, credits, reset credits |
  | Antigravity (`agy` 1.3.4) | `agy -p /usage` and `/credits` with `--output-format stream-json` (documented as no turn, no quota spent) | groups → buckets `{window, remaining_fraction, reset_time}` (normalised to % used) |

  Anything missing reads "not reported". Nothing is estimated from Brain's own token ledger. Refresh happens on open if the snapshot is older than 5 min, and every 10 min while a client is connected. Also: store Claude's non-rejected `rate_limit_event` utilisation, and use Codex's real `resetsAt` for `provider_quota_reset` instead of +1 h.
- **Rules:** three groups.
  - **How code changes:** threads run on frozen code (always on); the repo session commits without asking (switch); threads adopt new versions themselves (switch).
  - **Brain asks you first:** writes outside repo and vault, pushes to protected branches, deleting vaults or checkpoints, global installs/config/crontab.
  - **Never on the login node:** detached processes, multiplexers, poll loops; reading Brain's keys.

  Enforced rules are listed read-only, with a chip, and change only from the terminal. Switch changes are signed. Turning off a switch makes that action an approval.
- **Quiet hours** (switch, row): non-approval pushes are silent from 23:00 to 07:00 and roll into a morning digest. Approvals still ring. The setting exists today and is never applied (`config.py:165`).
- **Connection** (row, status only): relay host, live/unreachable, latency.
- **This phone:** name (editable), notifications on/test, earlier pairings, **Sign out**. Sign-out takes two taps; it revokes the key on Brain, wipes local keys and cache, and returns to pairing.
- **Later rows (P4):** Search (fixed `/v1/search`), Usage today (`/v1/ledger` + GPU-hours), Projects (read-only paths: repo, vault, worktree root; default provider).

## 8. Safety

- **The approval tray** shows Brain's verbatim fields, keeps hold-to-allow, and decides only on Brain's confirmation. Dismissing never denies.
- **Blind listening test:** systems and scores are not in the DOM until all items are rated (not hidden with opacity). The reveal comes only after the last item.
- **Device key in localStorage:**
  - Self-host fonts; add a CSP (`default-src 'self'`; no third-party scripts).
  - Sandbox HTML/SVG previews from the file endpoint.
  - Close the redaction gaps on `/v1/threads/{tid}/file` (the `hf/` cache) and in push bodies.
- **Signatures** currently cover `id + decision`. Extend them to a hash of the action shown, and sign rule changes and sign-out the same way.
- **Work threads cannot commit code.** The repo session cannot write into thread folders. Changing a version pin never touches a running job.
- **Every motion and decoration** is gated by `<Live>`. Pairing, identity-mismatch and revocation states outrank any decoration, with the `ConnectionBanner` copy kept word for word.

## 9. Quality-of-life fixes (from the audit; evidence in the audit report)

| # | Fix | Layer | Phase |
|---|---|---|---|
| 1 | Push when a job-woken turn ends: title "<thread>: <label> finished" (or failed), body = the agent's first line. `/status` (job state without a turn) moves to the P2 standup | brain, mobile | P0 |
| 2 | Push on failed turns (a 10 h "`claude` not found" outage went silent) | brain | P0 |
| 3 | Don't auto-archive threads with pending listening tests or jobs active within the TTL; show the TTL; repo sessions never expire | brain | P0 |
| 4 | "Since you left" uses the client's `left_at`; finished/failed job counts actually render | api, mobile | P0 |
| 5 | Failed-job push uses the label, not `brain.e5514867.0e4f520c` | brain | P0 |
| 6 | Quiet hours applied | brain | P0 |
| 7 | Policy false positives: drop `/`, `~` and quoted targets; strip heredoc bodies (all 3 "write outside scope" approvals so far were garbage) | brain | P0 |
| 8 | Listening: dismiss/supersede older tests; "N older" in the inbox | brain, api, mobile | P1 |
| 9 | Job progress: fill `time_limit`/`partition`/`gpus`; elapsed counts from start, not from the last state change; pending reason shown | brain, mobile | P1 |
| 10 | Job → log: `run_dir` on `/v1/jobs`, Log button, open `.out`/`.log` at the tail, `tail=` on file endpoint | api, mobile | P1 |
| 11 | Device names from UA, editable | mobile | P0 |
| 12 | `set_endpoint` also sends a visible Web Push ("Brain moved") until the named tunnel lands | brain | P0 |
| 13 | Search: quote FTS tokens server-side; results jump to the event | api, mobile | P4 |
| 14 | Load earlier history (`before_seq`); the store keeps 2000 events and EDIT TTS has 2349 | api, mobile | P4 |
| 15 | Rename thread; edit-and-resend the last message | mobile | P4 |
| 16 | Archived worktrees and `refs/brain/*` (244 refs) cleanup action | brain, mobile | P4 |
| 17 | Default provider from settings instead of hard-coded `claude` | api, mobile | P0 |
| 18 | Offline: cache the last standup; Resend when back online | mobile | P3 |
| 19 | Confirm on Stop; state never by colour alone; text contrast ≥ 4.5:1; dark mode (worlds) | mobile | P3 |
| 20 | Health checks for provider binaries and `sbatch` on PATH | brain | P0 |
| 21 | Remove dead settings: `max_hot_providers`, `idle_exit_seconds`, `archive_policy` | brain | P0 |

## 10. Phases

Each phase ships on its own, is tested on a throwaway Brain (never the owner's phone or live Brain), and has a kill criterion.

- **P0: fixes that matter now (days).** §9 rows marked P0, plus the named tunnel. *Done when* a job-woken turn pushes, EDIT TTS stops being archived with pending tests, and "since you left" shows real counts. *Kill:* none; these are fixes.
- **Prerequisite: Codex spec Part 2.** Durable queue, startup recovery, one `_finish_turn`, and `interrupted`/`stalled` states.
- **P1: structure.** Repo sessions, code versions, change requests, work threads on read-only checkouts, the vault layout, job provenance, the migration (§3.5), listening dismiss, job progress and logs. *Kill:* §3.4 read-only fallback. If agents keep routing around change requests (e.g. copying code into the thread folder) on more than 2 occasions in a week, add a policy check that flags `.py` files written into thread folders.
- **P2: truth layer.** `brain status`/`finding`, `results.json`, the progress tail, the failure classifier, `GET /v1/standup` + WebSocket. *Kill:* §6 staleness rule.
- **P3: Cinnamon shell.**
  - The roll, the world engine, Bakery and Forest scenes with their companions first.
  - Standup, project screen, trays, and the menu (World, Quota, Rules, Quiet hours, Connection, This phone).
  - `<Live>` gating, CSP, self-hosted fonts.
  - *Budget:* LCP ≤ 2.5 s, INP ≤ 200 ms, JS ≤ 550 KB gzip, warm open to a cached standup ≤ 1.0 s, 60 fps morph on the owner's phone.
  - *Kill:* if the morph drops frames on the owner's phone, cross-fade between day and night shapes instead. If push-to-decision gets slower, ship the trays without motion.
- **P4: the rest.** Rustic, Space and Tide with their companions; search; usage; projects row; history paging; rename/edit-resend; cleanup actions.

## 11. Testing

- **Brain:**
  - Unit tests for the policy parser, including the three garbage-target commands.
  - Version freeze and release, change-request state machine, adoption, read-only enforcement per provider.
  - Expiry rules and quota parsers (fixtures from the real probes, with account ids removed).
  - Migration on a copy of the real DB (`BRAIN_TEST_REAL_DB`): row counts unchanged, `verify_chain` passes, v1–v9 registered, second run is a no-op.
- **API:**
  - Golden standup with a frozen clock.
  - Provenance test: every number maps to a column, a `results.json` key, or a quota snapshot.
- **App:**
  - Playwright screenshots of each world × day/night × mood × companion on/off, and each menu step, against an Expo web build, compared as visual goldens. The mockups were checked the same way.
  - A provenance test for companions: every statement a companion makes maps to the same verified field as the headline.
  - Reduced-motion and offline (`document.getAnimations().length === 0`) tests.
  - A real-phone trace of standup → project → tray → back.

## 12. Rollout and rollback

- Additive migrations only. Old clients keep working through `/v1/health` features.
- A per-repo `structure: legacy|cinnamon` setting (default `legacy` until P1 ships for that repo). Rolling back is flipping it: version pins are ignored and threads fall back to their recorded worktree/vault paths.
- Nothing on disk is moved by any phase.

## 13. Not doing (YAGNI)

- User-made or shared worlds: worlds are code-defined until someone asks to share one.
- Companions that act on anything, wander across controls, or are animated beyond one idle loop.
- Sound.
- Location-based day/night.
- Moving existing vault folders.
- Renaming internals.
- A lead agent that spawns threads by itself: threads are started by the owner or on the owner's instruction.
