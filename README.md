# Cluster Brain — basic setup (v0.2.0-dev)

Run coding agents and SLURM experiments on your cluster from your phone. The design lives in the v0.2 PRD
and System Design docs; this repo is the first working slice.

```
cluster-brain/
  runtime/     Brain Runtime (Python ≥3.10): event spine, threads, worktrees, approvals, SLURM, push, API
  spikes/      the three risk tests to run on aqua-hpc first
  mobile/      Android-first Expo app (Inbox, Threads, coding chat, approvals, pairing)
  protocol/    API.md — the contract between the two
  deploy/      keep-alive (rung C), SLURM service job (rung B), relay options
  CONDITIONS.md  the one page for your cluster admin
```

## What works today

| Area | Status |
|---|---|
| Event spine | Per-thread sequence numbers, hash chain, 8 KB inline cap with blobs, replay from a cursor, WebSocket fan-out, full-text search |
| Threads | Read-only or worktree (`brain/<id>-<slug>`); pin, mute and archive; fork; snapshots before and after every turn (`refs/brain/…`) with per-file diffs |
| Claude Code | `claude -p` stream-json adapter. Every tool call goes through `brain-hook` (PreToolUse): auto mode by default, a small always-ask set is deferred, host-conduct rules are denied |
| Codex, Antigravity | `codex exec --json` and `agy -p` stream-json adapters behind the same `brain-hook`. Neither CLI can defer, so a pending approval is a deny and Brain resumes with a prompt once you decide. agy runs on `settings.json` allow rules by default (`agy_permissions: rules`) or `--dangerously-skip-permissions` with the hook as the only gate (`agy_permissions: skip`) |
| Start from the phone | **+** on Threads (or tap a repo): a thread on an existing repo, a **training run** (its own worktree + vault folder `<vault>/<run>-<id>`, where its jobs write), or a **new idea** (Brain creates a repo under `--projects-root` with a vault under `--vault-root` and starts on the idea). Switch model from a thread's ⋯ menu |
| PWA | `cd mobile && npx expo export --platform web --output-dir dist`; Brain serves it on the relay URL. Pair in Chrome, Add to Home Screen. Push is standard Web Push (VAPID key in `~/.brain/vapid.pem`), no Firebase needed |
| Relay failover | Give Brain several relays (`--alt-endpoint`, or `brain endpoint set URL --alt URL2`); the app fails over to whichever answers and keeps the list current |
| Scope | Agents change files only in their thread's worktree, the repo's vault and temp dirs; anything else (incl. user-wide config) becomes an approval pushed to your phone. Codex is OS-sandboxed to that scope; Claude and agy rely on Brain's command policy (no usable sandbox here: no `bwrap`, agy's `--sandbox` does not match the scope) |
| Provider handoff | Brain keeps a lossless provider-neutral transcript of every thread. `POST /v1/threads/{id}/provider` switches provider; the next turn first gives the new one everything it has not seen — appended to its native session for Claude and Codex, replayed verbatim for agy. Hidden reasoning does not carry over |
| Approvals | Pending until you decide, never auto-denied. Device-signed decisions. Resume via `--resume` with no prompt |
| SLURM | `brain submit` from inside a turn; jobs named and commented for `sacct`; completion-file plus batched-squeue watcher; the agent is woken when the job ends |
| Pairing and auth | QR pins Brain's Ed25519 key; code + HMAC + device signature; 15-minute access and 7-day rotating refresh tokens; revocation |
| Push | FCM HTTP v1 sender, used when a key is installed (otherwise pushes go to `~/.brain/push-outbox.jsonl`); signed endpoint-rotation push |
| Safety | Secret registry with exact-value redaction; permission checks; incident-response log; DISABLED flags; Pause |
| Tests | 16 unit and flow tests, plus `runtime/tests/smoke_live.py` (real server, real hook round-trip, fake `claude` and fake SLURM) |

Not yet: `dev_alloc` rerouting (waits on spike S2), the vault and
listening endpoints, ledger hard limits, Drive export, and the remaining phone screens. See `mobile/README.md`.

## 1. On aqua-hpc: install and run the risk tests (≈ 1 hour, once)

```bash
git clone <your repo> ~/cluster-brain && cd ~/cluster-brain
python3 spikes/spikes.py                  # S1 defer/resume, S2 reroute, S3 auto mode, S5 footprint
# optional: salloc --no-shell -p gpu --gres=gpu:1 -t 0:30:00   then
python3 spikes/spikes.py --alloc <jobid>  # proves rerouting onto a GPU allocation
```
Send back `spike-out/report.json`. If S1's resume-without-prompt fails, Brain needs a one-line change in
`runtime/brain/core.py` (resume with a short prompt instead) — the report says which.

## 2. On aqua-hpc: install Brain

```bash
cd ~/cluster-brain/runtime
python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[qr]"
brain init --repos ~/code --vault-root /scratch/$USER/vaults --projects-root ~/code \
           --endpoint https://<relay-host> --alt-endpoint https://<second-relay> --accept-conditions
CLAUDE_CONFIG_DIR=~/.brain/claude claude          # log Claude in once for Brain-run sessions
CODEX_HOME=~/.brain/codex codex login             # optional: Codex for Brain-run sessions
agy --app_data_dir=../.brain/agy                 # optional: log Antigravity in once (Brain's agy profile)
brain doctor
```
`brain init` reports the placement it chose. For aqua-hpc it should be rung C, because the cluster has no
CPU/service partition. Then keep it alive:

```bash
crontab -e   # add:  */10 * * * * $HOME/cluster-brain/deploy/keepalive.sh >> $HOME/.brain/keepalive.log 2>&1
```
Pick a relay from `deploy/relay.md` (a Quick Tunnel is fine for the first pairing).

## 3. Push (optional but recommended)

Create a personal Firebase project and add an Android app with package `dev.clusterbrain.mobile` (or your
own). Download `google-services.json` into `mobile/`. Create a service account holding only the
**Firebase Cloud Messaging API Admin** role, then run `brain init --accept-conditions --fix-perms --fcm-key <json>`
(it stores the key at 0600).

## 4. Phone

```bash
cd mobile && npm install
npx expo run:android            # or: eas build --profile development --platform android
```
On the cluster run `brain pair` and scan the QR in the app. Daily use:

- The Inbox shows anything that needs you.
- Threads lists conversations grouped by repo.
- In a chat, tap any step, file or `$` command to expand it.
- Approvals arrive as notifications and stay pending until you decide.

## Development

```bash
cd runtime && . .venv/bin/activate
pytest -q                      # unit + flow tests (fake provider, fake SLURM)
python tests/smoke_live.py     # real server end-to-end
BRAIN_DEV_NO_AUTH=1 brain serve --port 8765   # local UI work without pairing (never on the cluster)
```
