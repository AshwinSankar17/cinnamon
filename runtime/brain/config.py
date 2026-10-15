"""Paths, settings and filesystem hygiene for Brain Runtime.

Everything lives under BRAIN_ROOT (default ~/.brain), created 0700 with umask 077.
"""
from __future__ import annotations

import os
import threading
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def brain_root() -> Path:
    return Path(os.environ.get("BRAIN_ROOT", Path.home() / ".brain")).expanduser()


@dataclass
class Paths:
    root: Path

    @property
    def db(self) -> Path:
        return self.root / "brain.sqlite3"

    @property
    def lease(self) -> Path:
        return self.root / "brain.lease"

    @property
    def sock(self) -> Path:
        return self.root / "brain.sock"

    @property
    def identity_key(self) -> Path:
        return self.root / "identity.ed25519"

    @property
    def token_secret(self) -> Path:
        return self.root / "token.secret"

    @property
    def hook_token(self) -> Path:
        return self.root / "hook.token"

    @property
    def site_profile(self) -> Path:
        return self.root / "site-profile.yaml"

    @property
    def settings(self) -> Path:
        return self.root / "settings.yaml"

    @property
    def audit_dir(self) -> Path:
        return self.root / "audit"

    @property
    def ir_dir(self) -> Path:
        return self.root / "ir"

    @property
    def blobs(self) -> Path:
        return self.root / "blobs"

    @property
    def claude_config_dir(self) -> Path:
        return self.root / "claude"

    @property
    def codex_home(self) -> Path:
        return self.root / "codex"

    @property
    def fcm_key(self) -> Path:
        return self.root / "fcm-service-account.json"

    @property
    def disabled_flag(self) -> Path:
        return self.root / "DISABLED"

    @property
    def admin_token(self) -> Path:
        return self.root / "admin.token"

    @property
    def vapid_key(self) -> Path:
        return self.root / "vapid.pem"

    @property
    def push_outbox(self) -> Path:
        return self.root / "push-outbox.jsonl"


SITE_DISABLED_FLAG = Path("/etc/cluster-brain/disabled")


def paths() -> Paths:
    return Paths(brain_root())


def ensure_root(p: Paths | None = None) -> Paths:
    """Create BRAIN_ROOT and its subdirectories with owner-only permissions."""
    p = p or paths()
    os.umask(0o077)
    for d in (p.root, p.audit_dir, p.ir_dir, p.blobs, p.claude_config_dir, p.codex_home):
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)
    return p


class PermissionProblem(RuntimeError):
    pass


def check_permissions(p: Paths, fix: bool = False) -> list[str]:
    """Return a list of permission problems. Brain refuses to start if any remain.

    Rules (CONDITIONS.md): BRAIN_ROOT is 0700; any key, token or credential file is 0600 or tighter.
    """
    problems: list[str] = []

    def too_open(path: Path, max_mode: int) -> bool:
        if not path.exists():
            return False
        mode = stat.S_IMODE(path.stat().st_mode)
        return bool(mode & ~max_mode)

    checks: list[tuple[Path, int]] = [(p.root, 0o700), (p.claude_config_dir, 0o700), (p.codex_home, 0o700)]
    for f in (p.identity_key, p.token_secret, p.hook_token, p.admin_token, p.fcm_key, p.vapid_key, p.db):
        checks.append((f, 0o600))
    home = Path.home()
    for f in (home / ".netrc", home / ".claude" / ".credentials.json", home / ".codex" / "auth.json",
              home / ".cache" / "huggingface" / "token"):
        checks.append((f, 0o600))
    for path, mx in checks:
        if too_open(path, mx):
            if fix:
                os.chmod(path, mx)
            else:
                mode = oct(stat.S_IMODE(path.stat().st_mode))
                problems.append(f"{path} is {mode}; must be {oct(mx)} or tighter")
    return problems


@dataclass
class RepoConfig:
    id: str
    path: str
    name: str = ""
    default_branch: str = "main"
    worktree_root: str = ""
    vault_root: str = ""
    profile: str = "auto"  # auto | student
    protected_branches: list[str] = field(default_factory=lambda: ["main", "master"])


@dataclass
class Settings:
    repos: list[RepoConfig] = field(default_factory=list)
    default_provider: str = "claude"
    quiet_hours: tuple[str, str] = ("23:00", "07:00")
    fcm_project_id: str = ""
    endpoint: str = ""  # current public endpoint (relay); used in pairing payloads
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, p: Paths) -> "Settings":
        if not p.settings.exists():
            return cls()
        data = yaml.safe_load(p.settings.read_text()) or {}
        known = RepoConfig.__dataclass_fields__
        repos = [RepoConfig(**{k: v for k, v in r.items() if k in known}) for r in data.get("repos", [])]
        qh = data.get("quiet_hours", ["23:00", "07:00"])
        return cls(
            repos=repos,
            default_provider=data.get("default_provider", "claude"),
            quiet_hours=(qh[0], qh[1]),
            fcm_project_id=data.get("fcm_project_id", ""),
            endpoint=data.get("endpoint", ""),
            raw=data,
        )

    def save(self, p: Paths) -> None:
        data = dict(self.raw)
        for dead in ("max_hot_providers", "idle_exit_seconds"):
            data.pop(dead, None)
        data.update(
            repos=[r.__dict__ for r in self.repos],
            default_provider=self.default_provider,
            quiet_hours=list(self.quiet_hours),
            fcm_project_id=self.fcm_project_id,
            endpoint=self.endpoint,
        )
        # write-then-rename: two saves racing (threadpool endpoints) must never leave a torn settings.yaml behind
        tmp = p.settings.with_name(f".{p.settings.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(yaml.safe_dump(data, sort_keys=False))
        os.chmod(tmp, 0o600)
        os.replace(tmp, p.settings)

    def repo(self, repo_id: str) -> RepoConfig | None:
        return next((r for r in self.repos if r.id == repo_id), None)
