#!/bin/bash
# Rung B: Brain as a chained SLURM job in a CPU/service partition (only if `brain init` chose rung B).
#   sbatch deploy/brain-service.sbatch
# --dependency=singleton keeps exactly one "brain" job per user running; each job queues its successor
# at start, and on USR1 (10 min before the walltime) Brain finishes turns, checkpoints and exits.
#SBATCH --job-name=brain
#SBATCH --partition=CHANGE_ME          # a CPU partition that accepts >= 24 h
#SBATCH --cpus-per-task=2
#SBATCH --mem=4G
#SBATCH --time=7-00:00:00
#SBATCH --signal=B:USR1@600
#SBATCH --dependency=singleton
#SBATCH --output=%x-%j.log
set -u
VENV="${BRAIN_VENV:-$HOME/cluster-brain/runtime/.venv}"
sbatch --dependency=singleton "$0"      # successor waits until this job ends
exec "$VENV/bin/brain" serve --port "${BRAIN_PORT:-8765}" --max-hours 0
