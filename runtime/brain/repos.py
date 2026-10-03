"""Git worktrees, restore points and per-turn diffs.

Each write-capable thread gets its own worktree on branch brain/<thread8>-<slug>. Before and after every
turn Brain records a snapshot commit (tracked + untracked files, via a temporary index) under
refs/brain/<thread>/<n>, so any turn can be diffed or restored without touching the user's index.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    pass


def git(cwd: str | Path, *args: str, env: dict | None = None, check: bool = True) -> str:
    p = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                       env={**os.environ, **(env or {})})
    if check and p.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {p.stderr.strip() or p.stdout.strip()}")
    return p.stdout


def slugify(text: str, n: int = 24) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:n].rstrip("-")) or "thread"


def is_repo(path: str | Path) -> bool:
    return subprocess.run(["git", "-C", str(path), "rev-parse", "--git-dir"], capture_output=True).returncode == 0


def create_worktree(repo: str | Path, root: str | Path, thread_id: str, title: str, base_ref: str) -> tuple[Path, str]:
    branch = f"brain/{thread_id[-8:]}-{slugify(title)}"
    wt = Path(root) / f"{Path(repo).name}-{thread_id[-8:]}"
    wt.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "-b", branch, str(wt), base_ref)
    return wt, branch


def snapshot(worktree: str | Path, ref: str, message: str = "brain snapshot") -> str:
    """Commit the full working tree (incl. untracked, honouring .gitignore) to `ref` without touching HEAD/index."""
    with tempfile.NamedTemporaryFile(prefix="brain-index-", delete=False) as tf:
        idx = tf.name
    try:
        env = {"GIT_INDEX_FILE": idx}
        head = git(worktree, "rev-parse", "--verify", "-q", "HEAD", check=False).strip()
        if head:
            git(worktree, "read-tree", head, env=env)
        git(worktree, "add", "-A", env=env)
        tree = git(worktree, "write-tree", env=env).strip()
        ident = {"GIT_AUTHOR_NAME": "Brain", "GIT_AUTHOR_EMAIL": "brain@localhost",
                 "GIT_COMMITTER_NAME": "Brain", "GIT_COMMITTER_EMAIL": "brain@localhost"}
        args = ["commit-tree", tree, "-m", message] + (["-p", head] if head else [])
        commit = git(worktree, *args, env=ident).strip()
        git(worktree, "update-ref", ref, commit)
        return commit
    finally:
        Path(idx).unlink(missing_ok=True)


@dataclass
class FileChange:
    path: str
    added: int
    removed: int
    diff: str


def diff_between(worktree: str | Path, a: str, b: str, max_bytes: int = 6000) -> list[FileChange]:
    out: list[FileChange] = []
    numstat = git(worktree, "diff", "--numstat", a, b)
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        add = int(parts[0]) if parts[0].isdigit() else 0
        rem = int(parts[1]) if parts[1].isdigit() else 0
        path = parts[2]
        d = git(worktree, "diff", "--no-color", "-U3", a, b, "--", path)
        if len(d.encode()) > max_bytes:
            d = d[:max_bytes] + "\n… (diff truncated; open Changes for the full file)\n"
        out.append(FileChange(path, add, rem, d))
    return out


def restore(worktree: str | Path, ref: str) -> None:
    """Make the working tree match a snapshot (keeps HEAD; leaves the result uncommitted)."""
    git(worktree, "read-tree", "-u", "--reset", ref)
    git(worktree, "clean", "-fdq")
