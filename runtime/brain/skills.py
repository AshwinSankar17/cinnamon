"""What a thread's provider can run as `/name`: skills (<dir>/<name>/SKILL.md) and slash commands (<name>.md).

Only the places that provider actually loads under Brain are listed, so everything offered on the phone works when
sent as "/name args" — all three CLIs expand that in non-interactive mode (verified: claude -p, codex exec, agy -p).
Project entries win over user ones of the same name. Brain never writes any of these directories.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import yaml

SCOPE_RANK = {"project": 0, "user": 1, "plugin": 2, "builtin": 3}


def _frontmatter(path: Path) -> dict:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    try:
        data = yaml.safe_load(text[3:end]) if end > 0 else None
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _skills(root: Path, scope: str, ns: str = "") -> list[dict]:
    out = []
    for f in sorted(root.glob("*/SKILL.md")) if root.is_dir() else []:
        fm = _frontmatter(f)
        if fm.get("user-invocable") is False:
            continue
        name = str(fm.get("name") or f.parent.name)
        out.append({"name": f"{ns}:{name}" if ns else name, "description": str(fm.get("description") or "")[:300],
                    "kind": "skill", "scope": scope})
    return out


def _commands(root: Path, scope: str, ns: str = "", prefix: str = "") -> list[dict]:
    out = []
    for f in sorted(root.glob("*.md")) if root.is_dir() else []:
        fm = _frontmatter(f)
        desc = fm.get("description") or next((l.strip() for l in f.read_text(errors="replace").splitlines()
                                              if l.strip() and not l.startswith(("---", "#"))), "")
        name = f"{prefix}{f.stem}"
        out.append({"name": f"{ns}:{name}" if ns else name, "description": str(desc)[:300],
                    "kind": "command", "scope": scope, "args": fm.get("argument-hint")})
    return out


def _claude_plugins(home: Path) -> list[dict]:
    try:
        reg = json.loads((home / "plugins" / "installed_plugins.json").read_text())
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for key, installs in (reg.get("plugins") or {}).items():
        ns = key.split("@")[0]
        for inst in installs if isinstance(installs, list) else [installs]:
            p = Path(inst.get("installPath", ""))
            out += _skills(p / "skills", "plugin", ns) + _commands(p / "commands", "plugin", ns)
    return out


def list_skills(provider: str, cwd: Path, env: dict[str, str], agy_home: Path | None = None) -> list[dict]:
    home = Path.home()
    found: list[dict] = []
    if provider == "claude":
        ch = Path(env.get("CLAUDE_CONFIG_DIR") or home / ".claude")
        found += _skills(cwd / ".claude" / "skills", "project") + _commands(cwd / ".claude" / "commands", "project")
        found += _skills(ch / "skills", "user") + _commands(ch / "commands", "user")
        for synced in sorted((ch / "skills" / "synced").glob("*")) if (ch / "skills" / "synced").is_dir() else []:
            found += _skills(synced, "user", "anthropic-skills")  # skills synced from claude.ai
        found += _claude_plugins(ch)
    elif provider == "codex":
        cx = Path(env.get("CODEX_HOME") or home / ".codex")
        found += _skills(cwd / ".codex" / "skills", "project") + _skills(cwd / ".agents" / "skills", "project")
        found += _skills(cx / "skills", "user") + _skills(home / ".agents" / "skills", "user")
        found += _commands(cx / "prompts", "user", prefix="prompts:")
    elif provider == "agy":
        for d in (".agents", ".agent", "_agents", "_agent"):
            found += _skills(cwd / d / "skills", "project")
        found += _skills(home / ".gemini" / "config" / "skills", "user")
        if agy_home:
            found += _skills(agy_home / "builtin" / "skills", "builtin")
    best: dict[str, dict] = {}
    for s in found:
        if s["name"] not in best or SCOPE_RANK[s["scope"]] < SCOPE_RANK[best[s["name"]]["scope"]]:
            best[s["name"]] = s
    return sorted(best.values(), key=lambda s: (SCOPE_RANK[s["scope"]], s["name"]))
