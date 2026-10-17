# Native delivery to open terminal sessions (Claude, Codex, agy)

**Goal:** a phone message for a thread whose terminal session is open reaches that session the way the CLI itself
queues input, as the owner's own words, for Claude, Codex and agy. Nothing is shut down to deliver it.

**Spec:** docs/superpowers/specs/2026-10-15-session-import-design.md ("Shared session, single writer"; this plan
adds "Native delivery"). Multiple writers stay deferred.

## Facts this rests on (all observed 2026-10-17, throwaway sessions)

| | Open? | Deliver | Busy? |
|---|---|---|---|
| Claude 2.1.302 | `<profile>/sessions/<pid>.json` | type into its tmux pane; background session: `claude attach <job>` in a hidden tmux, type, close (session keeps running) | status `busy`; `waiting` = permission prompt; `shell` = idle with a background shell |
| Codex 0.153 | FLOCK on `<home>/thread-writer-locks/<sid>.lock` (`/proc/locks`) | `codex queue --thread <sid> --message <text>`: user message, queued while busy, kept on disk while closed | last of `task_started` / `task_complete` / `turn_aborted` in the rollout |
| agy 1.4 | FLOCK on `<profile>/presence/<conv>.lock` | type into its tmux pane (queued natively while busy) | screen footer `esc to cancel` |

- Messages from other sessions, MCP channels, Slack and plugins are all labelled "NOT from your user" by Claude:
  typing is the only way in with the owner's authority. Codex's queue counts as user input.
- Typed text that starts with `!` runs as a shell command and `/` as a slash command. Claude: a leading newline
  keeps it literal (a space does not for `/`). agy: a leading zero-width space does (newline and space do not;
  `/exit` closed it). agy drops the zero-width space from what it records.
- Claude's input box is the line between the last two `─` rules: `❯\xa0` + text; empty shows nothing or the
  `Try "…"` placeholder. agy's is `>` + text.
- Codex accepts the PreToolUse hook as `-c hooks.PreToolUse=[…]`, so Brain never has to write `hooks.json` into
  the owner's Codex home. agy's provider writes `settings.json` into its app-data dir: never into the owner's.

## Global constraints

- Never write the owner's provider files (Claude settings, `~/.codex/hooks.json`/`config.toml`, agy
  `settings.json`). Brain runs in the owner's homes only for linked threads, as already accepted for Claude.
- Repo-scoped changes only; tests on throwaway sessions outside `/projects/data/ttsteam/ashwin`.

## Tasks

### 1. Safer typing (importer.py)
- `RELAY_STATUS = {"idle", "busy", "shell"}`.
- `_literal(provider, text)`: Claude prefixes `\n`, agy `​`, when the text starts with `!` or `/`.
- `_ready(sock, pane, glyph)`: capture the pane; the input box (between the last two `─` rule lines) is one line,
  glyph + nothing or the placeholder. A draft or a dialog means hold.
- `route` returns `("deliver", target)` instead of `("relay", pane)`; `deliver(brain, t, target, text) -> bool`.
  False (the box was not ready when typing) puts the message back in the queue.
- Delivery check: `_relayed[tid]` holds `(text, sent_at)`. `importer.unconfirmed(brain)` from `tick`: an entry
  older than 60 s on a session that is not busy gets a notice "Your terminal did not take this message" and is dropped.
- Tests: shell status relays; draft holds; `!`/`/` escaped per provider; unconfirmed notice; existing tests updated.

### 2. Claude background sessions (importer.py)
- A `bg` record with no pane and status in `RELAY_STATUS` routes to `deliver` via `_attach_paste(profile, job,
  text)`: `tmux -L brain-attach new-session -d` running `claude attach <job>`, wait up to 10 s for `_ready`,
  type, close the window. Not ready -> close, return False.
- `take_over` no longer stops background sessions (`_claude_stop` removed); only an idle plain terminal outside
  tmux is closed as before.
- Tests: bg idle/shell -> attach; bg waiting -> hold; never `claude stop`.

### 3. Codex shares the terminal session (importer.py, core.py, providers/codex.py)
- Codex imports store `import_path` (the rollout). `linked(t)`: import_path set and the thread runs the imported
  provider (claude, codex or agy). `profile_of`/`home_of` per provider. `_point_at_terminal(provider)`.
- `_holder(path)`: pid holding an FLOCK on a lock file, from `/proc/locks`. Discover's `live` for Codex uses it
  (drops the 10-minute mtime guess).
- Route: lock held -> `deliver` via `codex queue` (CODEX_HOME = the owner's home); else `run`.
- `provider_ctx`: `CODEX_HOME` = the owner's home for a linked Codex thread.
- Codex provider: hook passed with `-c hooks.PreToolUse=…`; `hooks.json` is no longer written anywhere.
- `watch` covers linked Codex threads (item-count slice, whole file read only when its mtime moved). Turns stay
  open while busy (`_busy`: last task event), closed by `_settle` like Claude's.
- `synced` after a Brain turn: Codex skips to the item count.
- Tests: codex import links; queue when held, run when not (ctx env); watch + one turn across passes; hook flag.

### 4. agy sessions (importer.py, core.py, providers/agy.py, config)
- `import.agy_dirs` (owner: `~/.gemini/antigravity-cli-profiles/ash`). Discover conversations from
  `history.jsonl` (conversationId -> workspace) and `brain/<conv>/.system_generated/logs/transcript_full.jsonl`;
  title = first user text; live = presence lock held.
- Route: lock holder pid -> pane from its `TMUX`/`TMUX_PANE` env (pane must own the pid) -> `deliver` by typing
  when `_ready(">")`; holder outside tmux -> hold; no holder -> `run`.
- Linked agy turns: `agy_app_data_dir` = the owner's profile, `--dangerously-skip-permissions` with brain-hook as
  the gate, and no `settings.json` write.
- Tests: discover agy; import links; deliver by typing with the zero-width escape; closed runs in the profile
  without touching settings.json.

### 5. Docs, live check, deploy
- Spec section "Native delivery"; memory update.
- Real-CLI e2e on throwaways: Claude tmux + bg attach, Codex queue (isolated CODEX_HOME), agy pane.
- Dry run on a copy of the live DB, merge, deploy with the existing script, check routes of live threads.

## Review focus
1. A message typed while the owner has a half-written draft: must hold, never merge.
2. A message starting with `!` or `/`: must arrive as text in every CLI.
3. A Codex terminal that closes between the lock check and `codex queue`: the item waits in Codex's queue for the
   next resume (accepted; noted).
4. Brain running a linked Codex/agy turn must not write any file in the owner's provider home except the
   session itself.
5. An attach window that never becomes ready (version skew, e.g. Robin on 2.1.295): message held, window closed.
