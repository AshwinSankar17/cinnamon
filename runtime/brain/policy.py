"""Policy: what runs without asking, what always asks, what is never allowed on the host.

Owner decision: the default profile is `auto` — no per-command approvals. Only a small always-ask set
pauses for a human, and those approvals stay pending until decided (never auto-denied).
The `student` profile is the council's conservative default for new accounts.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

ALLOW, ASK, DENY = "allow", "ask", "deny"


@dataclass
class Decision:
    verdict: str  # allow | ask | deny
    action_type: str = "tool"
    summary: str = ""
    reason: str = ""
    decision_summary: dict[str, Any] = field(default_factory=dict)
    route: str = "host"  # host | dev_alloc | batch  (execution-target hint, see SDD §10)


@dataclass
class PolicyContext:
    profile: str = "auto"
    worktree: str | None = None
    vault_roots: tuple[str, ...] = ()
    protected_branches: tuple[str, ...] = ("main", "master")
    brain_job_ids: frozenset[str] = frozenset()


# Never on a shared login node: detaching, multiplexers, sleep/poll loops (CONDITIONS.md).
HOST_DENY = [
    (re.compile(r"(^|[;&|]\s*)(nohup|setsid|disown)\b"), "detached processes are not allowed on the login node"),
    (re.compile(r"(^|[;&|]\s*)(tmux|screen)\b"), "terminal multiplexers are not allowed; Brain owns long-lived work"),
    (re.compile(r"&\s*$"), "background jobs on the login node are not allowed; submit with slurm_submit"),
    (re.compile(r"\b(sleep\s+\d{2,}|watch\s+)"),
     "do not poll from the shell; end your turn and Brain will wake you when the job changes state"),
    (re.compile(r"while\s+.*squeue|squeue.*;\s*sleep"), "do not poll squeue; Brain watches jobs for you"),
]

# Commands that should run on a GPU dev allocation rather than the host (routing hint only for now).
HEAVY = re.compile(
    r"\b(python[0-9.]*\s+.*(train|synth|infer|eval|generate)\w*\.py|torchrun|accelerate\s+launch|"
    r"ffmpeg\b.*\.(wav|flac|mp3|opus)|nvidia-smi\s+-l)"
)
BATCH = re.compile(r"\b(torchrun|deepspeed|accelerate\s+launch)\b")


def _first_words(cmd: str) -> list[list[str]]:
    parts = re.split(r"\s*(?:&&|\|\||;|\|)\s*", cmd)
    out = []
    for p in parts:
        try:
            out.append(shlex.split(p))
        except ValueError:
            out.append(p.split())
    return [w for w in out if w]


def _inside(path: str, roots: tuple[str, ...]) -> bool:
    try:
        pp = PurePosixPath(path)
        return any(pp == PurePosixPath(r) or PurePosixPath(r) in pp.parents for r in roots if r)
    except Exception:
        return False


def classify_bash(cmd: str, ctx: PolicyContext) -> Decision:
    c = cmd.strip()
    for rx, why in HOST_DENY:
        if rx.search(c):
            return Decision(DENY, "host_conduct", c[:120], why)

    for words in _first_words(c):
        prog = PurePosixPath(words[0]).name if words else ""
        # git push --force to a protected branch
        if prog == "git" and len(words) > 1 and words[1] == "push":
            forced = any(w in ("-f", "--force", "--force-with-lease") or w.startswith("+") for w in words[2:])
            targets = [w.lstrip("+").split(":")[-1] for w in words[2:] if not w.startswith("-")]
            if forced and any(t in ctx.protected_branches for t in targets[1:] or targets):
                return _ask("force_push_protected", c, "force-push to a protected branch",
                            target=",".join(targets) or "?", reversible="hard to undo",
                            blast="rewrites shared history", fallback="push to a brain/* branch instead")
        # rm -r of vaults / checkpoints, or anything outside worktree + vault roots
        if prog == "rm" and any(w.startswith("-") and "r" in w.lower() for w in words[1:]):
            paths = [w for w in words[1:] if not w.startswith("-")]
            roots = tuple(x for x in (ctx.worktree, *ctx.vault_roots) if x)
            for p in paths:
                if re.search(r"(checkpoint|ckpt|vault)", p, re.I) or _inside(p, ctx.vault_roots):
                    return _ask("delete_vault_or_checkpoints", c, "delete experiment artifacts",
                                target=p, reversible="no (moves to .trash if allowed)",
                                blast="vault / checkpoint files", fallback="skip the cleanup and continue")
                if p.startswith("/") and roots and not _inside(p, roots):
                    return _ask("rm_outside_roots", c, "delete files outside the worktree and vaults",
                                target=p, reversible="no", blast="files outside Brain's area",
                                fallback="leave the files in place")
        # scancel of a job Brain did not submit
        if prog == "scancel":
            ids = [w for w in words[1:] if not w.startswith("-")]
            if any(i.split("_")[0] not in ctx.brain_job_ids for i in ids) or not ids:
                return _ask("cancel_foreign_job", c, "cancel a job Brain did not submit",
                            target=" ".join(ids) or "(filter)", reversible="no",
                            blast="someone else's or a manual job", fallback="leave the job running")

    route = "batch" if BATCH.search(c) else ("dev_alloc" if HEAVY.search(c) else "host")
    if ctx.profile == "student":
        if re.search(r"\b(sbatch|git\s+push)\b", c):
            return _ask("student_guard", c, "submit or push (student profile asks first)",
                        target=c[:80], reversible="depends", blast="shared resources", fallback="wait for you")
    return Decision(ALLOW, "bash", c[:120], route=route)


def _ask(action_type: str, cmd: str, verb: str, *, target: str, reversible: str, blast: str, fallback: str) -> Decision:
    return Decision(
        ASK,
        action_type,
        cmd[:200],
        f"always-ask: {verb}",
        decision_summary={"verb": verb, "target": target, "blast_radius": blast,
                          "reversible": reversible, "fallback": fallback},
    )


def classify(tool_name: str, tool_input: dict[str, Any], ctx: PolicyContext) -> Decision:
    if tool_name == "Bash":
        return classify_bash(str(tool_input.get("command", "")), ctx)
    if tool_name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        if ctx.worktree and path.startswith("/") and not _inside(path, (ctx.worktree, *ctx.vault_roots)):
            return _ask("write_outside_roots", f"{tool_name} {path}", "write outside the worktree",
                        target=path, reversible="maybe", blast="files outside Brain's area",
                        fallback="write inside the worktree instead")
        return Decision(ALLOW, "edit", f"{tool_name} {path}")
    return Decision(ALLOW, "tool", tool_name)
