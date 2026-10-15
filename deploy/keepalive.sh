#!/bin/bash
# Rung C keep-alive (login node). Run from cron every 10 minutes on ONE login node:
#   */10 * * * * $HOME/cluster-brain/deploy/keepalive.sh >> $HOME/.brain/keepalive.log 2>&1
# Starts `brain serve` if no fresh lease exists, at most 3 starts per hour (CONDITIONS.md).
set -u
export PATH="$HOME/.local/bin:$PATH"  # cron's PATH lacks it; claude, codex and agy live there
# cron does not read ~/.bashrc: load SLURM the way login shells do (sbatch/squeue on PATH, SLURM_CONF).
# The site's module init is not nounset-safe.
set +u
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh && module load slurm 2>/dev/null
set -u
command -v sbatch >/dev/null || echo "$(date -Is) warning: sbatch not on PATH; Brain cannot submit jobs"
BRAIN_ROOT="${BRAIN_ROOT:-$HOME/.brain}"
VENV="${BRAIN_VENV:-$HOME/cluster-brain/runtime/.venv}"
PORT="${BRAIN_PORT:-8765}"
LEASE="$BRAIN_ROOT/brain.lease"
STARTS="$BRAIN_ROOT/keepalive.starts"

[ -e "$BRAIN_ROOT/DISABLED" ] || [ -e /etc/cluster-brain/disabled ] && { echo "$(date -Is) disabled"; exit 0; }

# Named Cloudflare tunnel (deploy/relay.md §2): keep it running even while Brain itself is healthy.
TUNNEL_CFG="$BRAIN_ROOT/tunnel.yml"
[ -f "$TUNNEL_CFG" ] && ! command -v cloudflared >/dev/null && echo "$(date -Is) warning: $TUNNEL_CFG exists but cloudflared is not on PATH; the named tunnel is down"
if [ -f "$TUNNEL_CFG" ] && command -v cloudflared >/dev/null; then
  if ! pgrep -u "$(id -u)" -f "cloudflared tunnel .*--config $TUNNEL_CFG run" >/dev/null; then
    echo "$(date -Is) starting named tunnel"
    setsid cloudflared tunnel --no-autoupdate --config "$TUNNEL_CFG" run >> "$BRAIN_ROOT/cloudflared.log" 2>&1 < /dev/null &
  fi
fi

if [ -f "$LEASE" ]; then
  hb=$(python3 -c "import json,sys;print(int(json.load(open('$LEASE'))['heartbeat']))" 2>/dev/null || echo 0)
  host=$(python3 -c "import json;print(json.load(open('$LEASE'))['host'])" 2>/dev/null || echo "")
  age=$(( $(date +%s) - hb ))
  if [ "$age" -lt 90 ]; then exit 0; fi           # healthy (on this or another node)
fi

now=$(date +%s); touch "$STARTS"
recent=$(awk -v t=$((now-3600)) '$1>t' "$STARTS" | wc -l)
if [ "$recent" -ge 3 ]; then echo "$(date -Is) restart budget exhausted (3/h); not starting"; exit 0; fi
echo "$now" >> "$STARTS"

echo "$(date -Is) starting brain serve (lease age ${age:-none}s)"
# Self-imposed caps: 2 cores via nice/taskset where available; Brain exits after 12 h and cron restarts it.
cd "$HOME"
CMD=("$VENV/bin/brain" serve --port "$PORT" --max-hours 12)
if command -v systemd-run >/dev/null 2>&1 && systemd-run --user --scope -p MemoryMax=4G true >/dev/null 2>&1; then
  setsid systemd-run --user --scope -p MemoryMax=4G -p TasksMax=64 nice -n 10 "${CMD[@]}" \
    >> "$BRAIN_ROOT/serve.log" 2>&1 < /dev/null &
else
  setsid nice -n 10 "${CMD[@]}" >> "$BRAIN_ROOT/serve.log" 2>&1 < /dev/null &
fi
