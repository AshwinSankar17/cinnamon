import os
import subprocess
from pathlib import Path

import pytest

from brain.config import Paths, RepoConfig, Settings, ensure_root
from brain.core import Brain
from brain.db import DB, connect
from brain.providers.fake import FakeProvider
from brain.redact import SecretRegistry

FAKESLURM = Path(__file__).parent / "fakeslurm"


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "hi-tts-v3"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "train.py").write_text("print('hi')\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True, env=env)
    return repo


@pytest.fixture
def brain(tmp_path: Path, git_repo: Path, monkeypatch) -> Brain:
    monkeypatch.setenv("BRAIN_ROOT", str(tmp_path / ".brain"))
    monkeypatch.setenv("FAKESLURM_DIR", str(tmp_path / "fakeslurm"))
    monkeypatch.setenv("PATH", f"{FAKESLURM}:{os.environ['PATH']}")
    p = ensure_root(Paths(tmp_path / ".brain"))
    vault = tmp_path / "vaults"
    vault.mkdir()
    s = Settings(repos=[RepoConfig(id="hi-tts-v3", path=str(git_repo), name="hi-tts-v3",
                                   worktree_root=str(tmp_path / "wt"), vault_root=str(vault))],
                 default_provider="fake")
    s.save(p)
    db = DB(connect(p.db))
    b = Brain(p, s, db, providers={"fake": FakeProvider()}, redactor=SecretRegistry(["wandb-secret-0123456789"]))
    b.watcher.squeue_interval = 0
    b.watcher.sentinel_interval = 0
    return b
