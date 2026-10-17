# Import existing agent sessions as threads

Status: approved in chat by the owner, 2026-10-15

## Goal

The owner runs Claude Code (and sometimes Codex) in terminals on the cluster. Brain should pick those sessions up as threads, so the owner can read them and continue them from the phone.

## Owner decisions

- **Scope:**
  - Only the owner's own sessions, whose working directory is under the owner's folder (`/projects/data/ttsteam/ashwin`).
  - Teammates' folders, `/tmp` and automated runs are never touched. The cluster account is shared: `~/.claude/projects` also holds other people's sessions.
  - Sessions outside a git repo are skipped.
  - An unregistered git repo is registered automatically, without a vault.
- **Behaviour:** continue from the phone. The thread shows the full history. The next message continues the conversation in a new native session inside Brain's own config home: a fork. Brain never writes into the terminal's session file.

## Facts this rests on (verified 2026-10-15)

- **Where Brain's providers look:**
  - Brain's Claude runs with `CLAUDE_CONFIG_DIR=~/.brain/claude` and resumes only sessions there.
  - Codex runs with `CODEX_HOME=~/.brain/codex` and resumes only sessions there.

  So an owner session cannot be resumed in place; it must be hydrated (core.py `_catch_up`, providers' `hydrate`).
- **What already exists:**
  - `transcript.read_claude` and `read_codex` turn native files into provider-neutral items.
  - `_catch_up` hydrates whatever a provider has not seen, starting at `provider_sessions.canon_synced`.
- **The phone renders events, not transcript items.** An import writes both.
- **Claude session files:**
  - Records carry `cwd`, `sessionId` and `timestamp`.
  - Titles come from `ai-title` (`aiTitle`) or `agent-name` (`agentName`) records.
  - A `continued-in` record (`continuedInSessionId`) means the conversation moved to another session.
  - Live sessions are listed in `<profile>/sessions/<pid>.json` (`pid`, `sessionId`, `procStart`). A session is live when `/proc/<pid>/stat` field 22 equals `procStart`.
- **Codex rollouts:**
  - The first record is `session_meta`, with `id`, `cwd` and `originator`.
  - `originator` `codex_exec` marks automated runs.
- **Qualifying today:** 13 Claude sessions from the last 30 days, 9 of them in 7 git repos. No Codex sessions qualify; the recent ones are all `/tmp` spikes. Antigravity needs a protobuf workspace lookup and has no qualifying sessions, so it is out of scope.

## Design

- **Settings** (`settings.yaml`, written by `brain import --root … --claude-dir … --codex-dir …`):

  ```yaml
  import:
    root: /projects/data/ttsteam/ashwin
    claude_dirs: [~/.claude-profiles/ash]
    codex_dirs: [~/.codex]
    days: 30
  ```

  Without `root`, nothing is discovered.
- **`brain/importer.py`:**
  - `discover(settings, days)` returns candidates, newest first. Each is `{provider, session_id, path, cwd, repo_path, title, last_ts, live, items_hint}`.
    - It keeps only sessions with `cwd` under `root`, inside a git repo, and active within `days`.
    - It drops Claude sessions that continued elsewhere and Codex `codex_exec` sessions.
  - `import_session(brain, provider, session_id, provider_override=None) -> thread`:
    - **Idempotent:** the key `<provider>:<session_id>` goes in a new column `threads.imported_from`, and a re-import returns the same thread.
    - **Repo:** registers the repo if it is new: id from the folder name, plus a numeric suffix on a clash.
    - **Thread:** a `readonly` thread on the repo, titled from the session.
    - **Transcript:** parses it and redacts it (`redactor.redact_obj`) before storing it.
    - **Events:** writes `user.message`, `assistant.text`, `tool.started` and `tool.completed` events so the phone shows the history, with `actor {"kind": "import"}`, plus one notice naming the source.
    - **Model budget:**
      - It records `provider_sessions(thread, provider, native_session_id=NULL, canon_synced=cut)`, so the first phone message hydrates only the recent part: the newest items within 400 KB of JSON, starting at a user message.
      - The full history stays in the thread.
      - The notice says how many items the model gets.
- **API** (device auth):
  - `GET /v1/imports` lists candidates, each with `thread_id` if already imported.
  - `POST /v1/imports {provider, session_id}` imports one and returns the thread.
  - Admin versions under `/internal/imports` serve the CLI.
- **CLI:**
  - `brain import --list` prints candidates.
  - `brain import --all` imports all of them.
  - `brain import <session_id>` imports one.
  - Brain must be running.
- **App:** an Import screen, reached from the Threads tab. It lists candidates by repo with the title, last active time, "open in a terminal" when live, and Import or Open.

## Follow (added 2026-10-16)

- Every 60 s the scheduler runs `importer.follow`:
  - it imports any listed session not imported yet;
  - for each imported thread whose source file changed, it copies in the items past `threads.import_items`, redacted, as history events.
- Copied turns arrive read unless the thread already had an unread reply, and an archived thread comes back.
- A thread with a Brain turn running waits for the next pass.
- Threads imported before this change count their imported items from the transcript: those with no turn and a provider other than `user`.
- Phone messages never reach the terminal's session.

## Shared session, single writer (added 2026-10-17)

A thread imported from a Claude terminal session (and run by Claude) is *linked*: the thread and the terminal use one
session file, `threads.import_path`, in the owner's profile. Exactly one process writes it at a time:

| Where the session is | A phone message | Brain's own wakes (job done, etc.) |
|---|---|---|
| Open in a tmux pane, status `idle` or `busy` | pasted into that pane (bracketed paste, then Enter); the terminal runs it | held |
| Open in a tmux pane, any other status (a permission prompt, a shell) | held until it is `idle`/`busy` | held |
| Open elsewhere: background session, or idle interactive | Brain takes over: `claude stop <job id>` or SIGTERM, then runs it | the same |
| Open elsewhere and busy | held until idle | held |
| Closed | Brain runs `claude -p --resume <id>` with `CLAUDE_CONFIG_DIR` = the owner's profile | the same |

- **Paste safety:** Brain pastes only into the pane recorded in `<profile>/sessions/<pid>.json`, and only when that pane's shell is an ancestor of the session's pid.
- **Watching the file:** Brain checks the linked file every second and reads only the bytes past `threads.import_offset`, so a 100 MB session costs nothing when it is quiet.
- **Pasted messages:** they arrive back from the file. The user.message the phone already shows is not repeated.
- **Brain-run turns:** they are ingested as before, then the offset moves to the file's end.
- **Linking an older thread:** the `claude` provider session is pointed at the terminal session (`native_seen` = its non-user items, `canon_synced` = the transcript length). Phone turns from before the link stay in the app only.
- **Not covered:**
  - Two writers at once: a terminal opened during a Brain-run turn.
  - Approvals for pasted turns: the terminal's own permission mode applies.
  - Codex sessions: they stay one-way.

## Safety

- **Files read:** only the configured profile folders and only sessions under `root`. Nothing outside `root` is ever listed.
- **Writes:** none to source files. The source session file's bytes are unchanged; a test checks its hash.
- **Read-only thread:** write tools ask, as for any read-only thread, so a phone turn can't clash with live terminal edits.
- **Redaction:** transcript items are redacted before storage; events are redacted already.

## Not doing

- Antigravity.
- Importing sessions outside git repos.
