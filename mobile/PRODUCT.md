# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

Installed PWA served by the user's own Brain (Expo / React Native Web build). Must work best on an Android phone in Chrome and in a desktop browser. A native Android build from the same code exists but is secondary.

## Users

ML researchers (primary: TTS / speech) who run coding agents and SLURM training jobs on a shared GPU cluster. The owner, plus colleagues or students on the same cluster who each run their own Brain. They are usually away from the terminal: in a meeting, commuting, or at another machine.

Primary job: **quick check-ins.** Seconds to a few minutes: see what needs them, approve or deny, skim progress, send the next instruction, rate a listening test. Longer reading (diffs, results) happens but is secondary.

## Product Purpose

Cluster Brain lets a researcher steer coding agents (Claude Code, Codex, Antigravity) and SLURM experiments on their cluster from a phone. Agents work autonomously inside the thread's repo and vault; the owner's attention is asked for only when something reaches beyond that scope. Success: the owner spends little attention, and work keeps moving while they are away.

## Positioning

- Backend-agnostic: one thread can move between Claude, Codex and Antigravity with its full history, not a summary.
- Runs on the researcher's own cluster account; the phone pairs to it directly (device keys, signed approvals). No hosted service in the middle.
- Built around the research loop: training runs with their own vault, SLURM jobs that wake the agent when done, blind listening tests for audio outputs.

## Operating Context

- Owner on a phone, one-handed, often on a poor connection; the relay URL may change (Cloudflare Quick Tunnel during testing).
- Push notifications (Web Push) bring them in for approvals, finished runs, and listening tests.
- Threads are grouped by repo; each thread has a model (Claude Code / Codex / Antigravity), a mode (own branch or read-only), a state (needs you, running, waiting, idle, paused).
- Agent output is Markdown with LaTeX, code, tool steps (commands, file edits), diffs, SLURM job cards.

## Capabilities and Constraints

- Screens today: Inbox (needs you, ready to listen, running, done), Threads (grouped by repo, filters), Runs (SLURM jobs), Repos, thread chat (turns, steps, diffs, approvals, job and listening cards, composer with `/` skills menu and attachments), approval review (signed allow/deny), New (thread, training run, new idea), listening test (blind A/B/C, 1–5 scores, best pick, reveal), file browser (repo, run vault, shared vault), pairing.
- Approvals must stay pending until decided; decisions are signed on the device.
- Rare-but-critical moments (approvals, new device pairing) must never be missable or ambiguous.
- No invented numbers: every metric, cost and status shown comes from Brain.

## Brand Commitments

Name: Cluster Brain ("Brain" for short).

The incumbent "editorial light" look is binding (owner decision, 2026-10-06, after trying a flight-strip redesign): keep its colour palette (warm off-white ground, white cards, terracotta accent `#c2410c`), its fonts (Newsreader serif display, Manrope UI, IBM Plex Mono) and its rounded card language (src/theme.ts). Improve aesthetics and UX within that system; do not replace palette or fonts.

## Evidence on Hand

Real data only comes from the user's Brain (threads, jobs, listening tests). There are no testimonials, customers or benchmarks; do not fabricate any.

## Product Principles

1. Attention is the scarce resource: surface what needs the owner first, everything else stays quiet.
2. Glanceable in seconds: state, progress and the next action readable without opening anything.
3. Trust through precision: every action says what it will do; destructive or out-of-scope actions are explicit.
4. The agent's work is the content: chat, results and audio deserve first-class reading and listening.
5. Same product on phone and desktop; phone first.

## Accessibility & Inclusion

Large touch targets for one-handed use; readable outdoors; screen-reader labels on every control (already a practice in the codebase).
