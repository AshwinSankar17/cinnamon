# Cluster Brain — conditions of use on a shared cluster

This is the one page to hand your cluster admin. `brain init` prints it and will not proceed until you
accept it with `--accept-conditions`.

**What it is.** Cluster Brain is a small per-user service that lets its owner run coding agents (Claude
Code, Codex, Antigravity) and SLURM experiments from a phone. It runs as the owner's account, never as root,
one instance per user.

**Where it runs.** `brain init` probes the site and picks one placement:
- **A** — on an always-on machine the owner controls, reaching the cluster with key-only SSH; nothing
  persistent on the cluster.
- **B** — as an ordinary SLURM job in a CPU partition (2 cores, 4 GB), chained with
  `--dependency=singleton`, so it is accounted and cgroup-limited like any job.
- **C** — on a login node, only when neither A nor B is available, under the caps below, restarted at most
  3 times per hour and exiting cleanly every 12 hours.

**Caps it enforces on itself.**
- 2 CPU cores, 4 GB RSS and 64 processes across Brain, its relay and agent processes.
- At most 4 agent processes at once. An agent process exists only during a turn and exits when idle.
- No training, inference, transcription or audio transcoding on the login node. Agents are instructed to
  submit those with `brain submit`, which goes through `sbatch`.
- Agents are blocked from running `nohup`, `setsid`, `disown`, tmux/screen, background `&` jobs, and
  `sleep`/`squeue` polling loops on the login node.
- Agents change files only inside their thread's repo worktree, that repo's vault and temp directories.
  Anything else (writing elsewhere in the owner's home, user-wide config such as `git config --global`,
  `pip install --user`, `npm -g`, crontab) waits for the owner's explicit approval on their phone. Codex runs
  in its OS sandbox with exactly that write scope; for the other agents Brain's command policy enforces it.

**How it uses SLURM.**
- Every job Brain submits is named `brain.<thread8>.<run8>`, carries
  `--comment={"brain_thread":…,"brain_run":…}`, and exports `BRAIN_THREAD_ID`. You can attribute any job
  with `sacct`.
- It watches jobs politely: a completion file written by the job itself is checked every 30 s. It runs one
  batched `squeue -j <ids>` at most every 60 s, and `sacct -j <id>` only for jobs that vanish. It never
  loops `scontrol`, and never runs `sacct -u` over wide windows.
- It cancels only jobs it submitted; cancelling anything else requires the owner's explicit approval.

**Network.**
- Brain listens on a Unix socket owned by the user (`~/.brain/brain.sock`, mode 0600). It optionally
  listens on 127.0.0.1 for a relay; every request is authenticated with device-signed sessions.
- Its only outbound connections are:
  - HTTPS (443) to the AI providers' APIs;
  - HTTPS to Firebase Cloud Messaging, for push to the owner's phone;
  - GitHub, for pushes the owner already makes;
  - the relay the owner chose (Cloudflare named tunnel, SSH, or Tailscale where VPN clients are allowed).

**Secrets.**
- Brain refuses to start if `~/.brain` is not 0700, or if any key or token file it touches is looser
  than 0600. This includes `~/.netrc`, the Claude and Codex credential files, and the FCM service-account
  key.
- Known secret values are redacted from every log and from every event shown on the phone.
- The FCM key must hold only the "Firebase Cloud Messaging API Admin" role.

**Accountability.**
- Brain writes an incident-response log at `~/.brain/ir/YYYY-MM-DD.log`, kept 90 days. It records every
  command an agent runs (command line, verdict), every sbatch/scancel with its thread and approval, and
  every agent start and stop.
- `brain ir-dump --since-hours 24` prints the log for an admin. It contains command lines and exit codes,
  never file contents or diffs.

**Off switch.**
- Creating `~/.brain/DISABLED` (owner) or `/etc/cluster-brain/disabled` (admin) stops all agent activity:
  running turns are interrupted, new tool calls are denied, and no new jobs are submitted.
- The owner can also pause everything from the phone.

Owner: ______________________  Account: __________  Date: __________
Admin acknowledgement (optional): ______________________
