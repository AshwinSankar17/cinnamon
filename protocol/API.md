# Cluster Brain protocol v0.2 (MVP subset)

Transport: HTTPS for commands and catch-up, WebSocket for live events while the app is in the
foreground, FCM push for everything that must reach a backgrounded phone. Brain listens on a Unix
socket (`~/.brain/brain.sock`) and optionally `127.0.0.1:<port>`; a relay (named tunnel, `ssh -R`,
Tailscale, or Quick Tunnel for first pairing) exposes it. The hostname is never identity.

All JSON. All mutating requests may carry `Idempotency-Key: <uuid>`; a repeat with the same key and
body returns the original response, a repeat with a different body returns 422.

## Identity, pairing, sessions

Brain has a persistent Ed25519 identity key. The device has its own Ed25519 key (kept in secure
storage on the phone).

1. On the cluster: `brain pair` prints a pairing payload (QR + short code):
   `{"v":1,"brain_pub":"<b64url>","endpoint":"https://…","code":"AQ7K-3H9D","secret":"<b64url 32B>","exp":<unix>}`
2. `POST /v1/pair/complete`
   body `{"code","device_pub","device_name","nonce_device","sig","mac"}`
   - `transcript = sha256("cb-pair-v1" ‖ brain_pub ‖ device_pub ‖ endpoint ‖ nonce_device)`
   - `sig` = device Ed25519 signature over `transcript`
   - `mac` = HMAC-SHA256(secret, transcript)
   → `{"device_id","brain_sig"}` where `brain_sig` = Brain signature over `transcript`; the app verifies
   it against the `brain_pub` it already pinned from the QR. Codes are single-use, 5-minute TTL,
   5 attempts.
3. `POST /v1/session/challenge` `{"device_id"}` → `{"nonce","brain_sig"}`; `brain_sig` signs
   `"cb-chal-v1" ‖ nonce ‖ host` (host = the Host header the app used) so the app knows it reached
   the paired Brain through this endpoint.
4. `POST /v1/session/token` `{"device_id","nonce","sig"}` (sig over `"cb-tok-v1" ‖ nonce`) →
   `{"access_token","expires_in":900,"refresh_token","refresh_expires_in":604800}`
5. `POST /v1/session/refresh` `{"device_id","refresh_token"}` → new pair.
Every other call: `Authorization: Bearer <access_token>`. Revoked devices get 401 everywhere.

## Objects

Thread `{id, repo_id, title, mode: "readonly"|"worktree", branch, provider: "claude"|"codex"|"gemini"|"fake",
state: "idle"|"running"|"waiting"|"needs_you"|"paused", pinned, archived, muted, parent_id, fork_turn,
last_seq, updated_at, last_event}`

Event `{event_id, thread_id, seq, ts, type, visibility, turn_id, actor:{kind,device_id?}, payload,
payload_ref?, hash, prev_hash}` — `payload` ≤ 8 KB; larger bodies are truncated (head + tail) with
`payload_ref` = blob id fetchable at `GET /v1/blobs/{id}`.

Event types (payload fields):
- `user.message` {text, attachments?}
- `turn.started` {turn_id, provider}
- `assistant.text` {text}
- `tool.started` {tool_use_id, tool, summary, input_preview}
- `tool.completed` {tool_use_id, tool, ok, exit_code?, output_preview}
- `file.diff` {path, added, removed, diff}  (unified diff, ≤ 8 KB inline)
- `turn.ended` {turn_id, text, counts:{reads,edits,commands,failed}, diffstat:{files,added,removed},
  duration_ms, usage:{input_tokens,output_tokens}, stop_reason}
- `approval.requested` {approval_id, action_type, summary, decision_summary}
- `approval.decided` {approval_id, decision, note?}
- `job.submitted` {job_id, name, partition, gpus, time_limit}
- `job.state` {job_id, state, exit_code?, reason?}
- `wait.created` / `wait.fired` {wait_id, kind, predicate}
- `provider.status` {provider, status: "rate_limited"|"retrying"|"error", resets_at?, message}
- `system.notice` {text}

Approval `{id, thread_id, action_type, summary, decision_summary:{verb,target,blast_radius,
reversible,fallback}, state:"pending"|"allowed"|"denied", created_at, decided_at}` —
pending until decided; never auto-denied.

## REST

| Method & path | Body / query | Returns |
|---|---|---|
| GET `/v1/health` | — | `{ok, brain_pub, version, rung, paused}` (no auth) |
| GET `/v1/repos` | — | `[Repo]` |
| GET `/v1/threads` | `?repo_id&state&archived=0` | `[Thread]` |
| POST `/v1/threads` | `{repo_id, title?, mode, provider, base_ref?}` | `Thread` |
| GET `/v1/threads/{id}` | — | `Thread` |
| PATCH `/v1/threads/{id}` | `{title?, pinned?, archived?, muted?}` | `Thread` |
| POST `/v1/threads/{id}/fork` | `{at_seq?, title, mode}` | `Thread` |
| POST `/v1/threads/{id}/messages` | `{text}` | `{seq}` |
| POST `/v1/threads/{id}/stop` | — | `{ok}` |
| GET `/v1/threads/{id}/events` | `?after_seq=0&limit=200` | `{events:[Event], last_seq}` |
| GET `/v1/pending` | — | `{approvals:[Approval], waits:[…], jobs:[Job], threads_needing_you:[Thread], since:{…}}` |
| POST `/v1/approvals/{id}/decision` | `{decision:"allow"|"deny", note?, device_sig}` sig over `"cb-dec-v1" ‖ id ‖ decision` | `Approval` |
| GET `/v1/jobs` | — | `[Job]` `{job_id, thread_id, name, state, gpus, elapsed, time_limit}` |
| POST `/v1/jobs/{id}/cancel` | — | `{ok}` |
| POST `/v1/pause` / `/v1/resume` | — | `{paused}` |
| POST `/v1/devices/push-token` | `{fcm_token}` | `{ok}` |
| GET `/v1/blobs/{id}` | — | raw bytes |

## WebSocket `/v1/ws?token=<access_token>`
client → `{"op":"subscribe","thread_id","after_seq"}` | `{"op":"unsubscribe","thread_id"}` | `{"op":"ping"}`
server → `{"op":"event","event":Event}` | `{"op":"replay_complete","thread_id","through_seq"}` | `{"op":"pong"}`
The app connects only while foregrounded; on background it closes and relies on push + REST catch-up.

## Push (FCM data + notification)
`data`: `{v:"1", kind:"approval"|"failed"|"done"|"budget"|"changed"|"transport", thread_id?, approval_id?,
job_id?, endpoint?, endpoint_sig?}` — never a bearer token. `kind:"transport"` carries
`endpoint` and `endpoint_sig` = Brain signature over `"cb-ep-v1" ‖ endpoint ‖ ts ‖ device_id`.
Android channels: `needs_you` (high, sound), `failed` (high), `done` (default), `budget` (default),
`changed` (low), `transport` (min, silent).
