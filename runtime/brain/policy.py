"""Policy: what runs without asking, what always asks, what is never allowed on the host.

Owner decision: agents run in auto mode. Anything inside the thread's own scope (its worktree, its run vault, temp)
runs without asking, deletes included. Only actions that reach beyond the thread pause for the owner: writing or
deleting outside the repo/vault, deleting from the repo's shared vault, user-wide config changes, force-pushing a
protected branch, cancelling a job Brain did not submit. Those approvals stay pending until decided.
The `student` profile is the council's conservative default for new accounts.
"""
from __future__ import annotations

import os
import posixpath
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
    scratch_roots: tuple[str, ...] = ()  # temp dirs: ephemeral, so writes there are in scope
    run_vault: str | None = None  # a training-run thread's own vault folder
    brain_root: str | None = None  # Brain's own state: agents may never change it or read its secrets

    @property
    def thread_scope(self) -> tuple[str, ...]:
        """What belongs to this thread alone: anything goes here, deletes included."""
        return tuple(x for x in (self.worktree, self.run_vault, *self.scratch_roots) if x)

    @property
    def scope(self) -> tuple[str, ...]:
        """Where agents may change things without asking: the thread's worktree, the repo's vaults, temp dirs.
        Anything else needs the owner's approval (CONDITIONS.md: changes are repo/vault scoped)."""
        return tuple(x for x in (self.worktree, *self.vault_roots, *self.scratch_roots) if x)


# Commands whose path arguments they modify. LAST: only the destination (last path) is written.
WRITES_ALL = {"mkdir", "touch", "rm", "rmdir", "unlink", "truncate", "tee", "chmod", "chown", "chgrp", "shred"}
WRITES_LAST = {"cp", "mv", "install", "ln", "rsync", "scp"}
GLOBAL_ENV = re.compile(
    r"\bgit\s+config\s+(--global|--system)\b|\bpip3?\s+install\b[^;&|]*\s--user\b|\bnpm\s+(i|install|uninstall)\b"
    r"[^;&|]*\s(-g|--global)\b|\bcrontab\b|\bconda\s+(install|create|remove|update)\b(?![^;&|]*\s(-p|--prefix)\s)")


# Inside BRAIN_ROOT agents may read what they need (e.g. skills in claude/skills, codex/skills) but never these.
BRAIN_SECRETS = ("identity.ed25519", "token.secret", "hook.token", "admin.token", "vapid.pem", "fcm-service-account.json",
                 "brain.sqlite3", "brain.sqlite3-wal", "brain.sqlite3-shm", "brain.lease", "brain.sock", "audit", "ir",
                 "blobs", "push-outbox.jsonl", "claude/.credentials.json", "codex/auth.json", "agy/antigravity-oauth-token")
SECRET_REFS = re.compile(r"\bBRAIN_(HOOK|ADMIN)_TOKEN\b|\b(hook|admin)\.token\b|\$\{?(HOME|BRAIN_ROOT)\}?/\.brain\b|"
                         r"\$\{?BRAIN_ROOT\b")


def _brain_verdict(paths: list[str], cmd: str, ctx: PolicyContext, writes: list[str]) -> Decision | None:
    """Deny an agent's attempt to read Brain's secrets or change anything of Brain's own."""
    why = "that is Cluster Brain's own state (credentials, keys, its database); agents may not touch it"
    if SECRET_REFS.search(cmd):
        return Decision(DENY, "brain_internals", cmd[:120], why)
    if not ctx.brain_root:
        return None
    root = posixpath.normpath(ctx.brain_root)
    secrets = tuple(f"{root}/{x}" for x in BRAIN_SECRETS)
    for p in paths:
        full = _resolve(p, ctx)
        if full and (_inside(full, secrets) or (p in writes and _inside(full, (root,)))):
            return Decision(DENY, "brain_internals", cmd[:120], why)
    return None


def _resolve(path: str, ctx: PolicyContext) -> str | None:
    """Absolute normalized form of a command's path argument, relative ones against the worktree (the cwd)."""
    if path == "~" or path.startswith("~/"):
        return posixpath.normpath(os.path.expanduser(path))
    if path.startswith("~"):
        return "/home/~" + path[1:]  # ~user: outside any scope root
    if "$" in path or "`" in path or path.startswith("-"):
        return None  # ponytail: expansions are not evaluated; the OS sandbox (Codex/agy) still bounds them
    if not path.startswith("/"):
        if not ctx.worktree:
            return None
        path = f"{ctx.worktree}/{path}"
    return posixpath.normpath(path)


def _out_of_scope_writes(cmd: str, ctx: PolicyContext) -> list[str]:
    out = []
    for t in _write_targets(cmd, ctx):
        p = _resolve(t, ctx)
        if p and ctx.worktree and not _inside(p, ctx.scope):
            out.append(t)
    return out


def _write_targets(cmd: str, ctx: PolicyContext) -> list[str]:
    words_list, targets = _parse(cmd)
    for words in words_list:
        prog = PurePosixPath(words[0]).name
        args = [w for w in words[1:] if not w.startswith("-")]
        if prog in ("chmod", "chown", "chgrp") and args:
            args = args[1:]  # mode / owner first
        if prog in WRITES_ALL:
            targets += args
        elif prog in WRITES_LAST and len(args) >= 2:
            targets.append(args[-1])
        elif prog == "sed" and any(w.startswith("-i") or w == "--in-place" for w in words[1:]):
            targets += [a for a in args[1:]]
        elif prog == "dd":
            targets += [w[3:] for w in words[1:] if w.startswith("of=")]
    return [t for t in targets if not t.startswith(("/dev/", "&"))]


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


SEPARATORS = {";", "&&", "||", "|", "&", "|&", "(", ")"}
REDIRECTS = {">", ">>", ">|", "&>", "&>>", ">&"}


HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)(\w+)\1([^\n]*)\n(?:.*?\n)??[ \t]*\2[ \t]*(?=\n|$)", re.S)
DATA_SINKS = {"cat", "tee"}  # commands whose heredoc body is data, not shell that runs


def _strip_heredocs(cmd: str) -> str:
    """`cat > f <<'EOF' ... EOF`: keep `cat > f` (a real write), drop the body (text, not commands).
    A heredoc fed to anything else (bash, ssh, python) keeps its body: it runs, so the policy must see it."""
    def sub(m: re.Match) -> str:
        line = m.string[m.string.rfind("\n", 0, m.start()) + 1:m.start()]
        words = re.split(r"&&|\|\||[;|&(]", line)[-1].split()
        words = [w for w in words if not re.match(r"^\w+=", w)]  # leading VAR=value assignments
        return m.group(3) if words and PurePosixPath(words[0]).name in DATA_SINKS else m.group(0)
    return HEREDOC.sub(sub, cmd)


def _parse(cmd: str) -> tuple[list[list[str]], list[str]]:
    """Quote-aware split into simple commands (word lists) and output-redirect targets.
    `sed 's/a;b/c|d/'` or `echo "x > y"` are one command with no redirect; `cmd 2>&1` is not a file write."""
    lex = shlex.shlex(_strip_heredocs(cmd), posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        toks = list(lex)
    except ValueError:  # unbalanced quotes: fall back to a plain split
        toks = _strip_heredocs(cmd).split()
    cmds, cur, targets, i = [], [], [], 0
    while i < len(toks):
        t = toks[i]
        if t in SEPARATORS:
            cmds.append(cur)
            cur = []
        elif t in REDIRECTS or t == "<":
            nxt = toks[i + 1] if i + 1 < len(toks) else ""
            if t != "<" and nxt and not (t == ">&" and nxt.isdigit()) and not nxt.startswith("&"):
                targets.append(nxt)
            if cur and cur[-1].isdigit():
                cur.pop()  # the fd in `2>file`
            i += 1  # skip the target / source
        else:
            cur.append(t)
        i += 1
    cmds.append(cur)
    return [c for c in cmds if c], targets


def _first_words(cmd: str) -> list[list[str]]:
    return _parse(cmd)[0]


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
    words_list, redirects = _parse(c)
    args = [w for ws in words_list for w in ws[1:] if not w.startswith("-")] + [w.split("=", 1)[1] for ws in words_list
                                                                                  for w in ws if w.startswith(("of=", "if="))]
    d = _brain_verdict(args + redirects, c, ctx, writes=_write_targets(c, ctx))
    if d:
        return d

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
        # rm -r: free inside the thread's own scope (worktree, its run vault, temp). Asks only for the repo's shared
        # vault (other runs' artifacts) and for anything outside Brain's area.
        if prog == "rm" and any(w.startswith("-") and "r" in w.lower() for w in words[1:]):
            for p in (w for w in words[1:] if not w.startswith("-")):
                full = _resolve(p, ctx)
                if full is None:  # $VAR / ~user: unknowable here; only artifact-looking targets ask
                    if re.search(r"(checkpoint|ckpt|vault)", p, re.I):
                        return _ask("delete_vault_or_checkpoints", c, "delete experiment artifacts", target=p,
                                    reversible="no", blast="vault / checkpoint files", fallback="skip the cleanup")
                    continue
                if ctx.worktree and _inside(full, tuple(x for x in (ctx.worktree, ctx.run_vault) if x)):
                    continue
                if _inside(full, ctx.vault_roots):  # checked before temp: a vault may live under /tmp or $TMPDIR
                    return _ask("delete_vault_or_checkpoints", c, "delete artifacts in the repo's shared vault",
                                target=p, reversible="no", blast="other runs' checkpoints and logs",
                                fallback="skip the cleanup and continue")
                if ctx.worktree and not _inside(full, ctx.thread_scope):
                    return _ask("rm_outside_roots", c, "delete files outside this repo and its vault",
                                target=p, reversible="no", blast="files outside this thread's repo/vault",
                                fallback="leave the files in place")
        # scancel of a job Brain did not submit
        if prog == "scancel":
            ids = [w for w in words[1:] if not w.startswith("-")]
            if any(i.split("_")[0] not in ctx.brain_job_ids for i in ids) or not ids:
                return _ask("cancel_foreign_job", c, "cancel a job Brain did not submit",
                            target=" ".join(ids) or "(filter)", reversible="no",
                            blast="someone else's or a manual job", fallback="leave the job running")

    if GLOBAL_ENV.search(c):
        return _ask("change_global_environment", c, "change your user-wide environment", target=c[:80],
                    reversible="usually", blast="every project in your account, not just this repo",
                    fallback="use a repo-local setting or a venv inside the worktree")
    outside = _out_of_scope_writes(c, ctx)
    if outside:
        return _ask("write_outside_scope", c, "change files outside this repo and its vault",
                    target=", ".join(outside)[:120], reversible="maybe", blast="files outside this thread's repo/vault",
                    fallback="write inside the worktree or the vault instead")

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
    path_arg = str(tool_input.get("file_path") or tool_input.get("notebook_path") or tool_input.get("path") or "")
    is_write = tool_name in ("Write", "Edit", "MultiEdit", "NotebookEdit")
    if path_arg:
        d = _brain_verdict([path_arg], f"{tool_name} {path_arg}", ctx, writes=[path_arg] if is_write else [])
        if d:
            return d
    if is_write:
        path = path_arg
        full = _resolve(path, ctx) if path else None
        if ctx.worktree and full and not _inside(full, ctx.scope):
            return _ask("write_outside_roots", f"{tool_name} {path}", "write outside this repo and its vault",
                        target=path, reversible="maybe", blast="files outside this thread's repo/vault",
                        fallback="write inside the worktree or the vault instead")
        return Decision(ALLOW, "edit", f"{tool_name} {path}")
    return Decision(ALLOW, "tool", tool_name)
