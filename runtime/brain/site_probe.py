"""`brain init` site probe: learn what this cluster allows, pick a placement rung, write site-profile.yaml.

Rungs (SDD §2.1):
  A  control plane on an always-on host you own; SSH key-only into the cluster
  B  Brain as a long-running SLURM job in a CPU/service partition (≥24 h, singleton-chained)
  C  Brain on the login node under self-imposed caps with a 12 h self-restart
"""
from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml


def _run(cmd: list[str], timeout: float = 15) -> tuple[int, str]:
    if not shutil.which(cmd[0]):
        return 127, ""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        return 124, ""


def _hours(limit: str) -> float:
    """Parse SLURM time limits like 7-00:00:00, 2:00:00, 30:00, UNLIMITED."""
    if not limit or limit.upper() in ("UNLIMITED", "INFINITE"):
        return float("inf")
    days = 0
    if "-" in limit:
        d, limit = limit.split("-", 1)
        days = int(d)
    parts = [int(x) for x in limit.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts[-3:]
    return days * 24 + h + m / 60 + s / 3600


def probe_partitions() -> list[dict[str, Any]]:
    rc, out = _run(["sinfo", "-h", "-o", "%P|%l|%a|%D|%G"])
    parts = []
    if rc != 0:
        return parts
    for line in out.splitlines():
        f = line.strip().split("|")
        if len(f) < 5:
            continue
        name = f[0].rstrip("*")
        parts.append({"name": name, "default": f[0].endswith("*"), "time_limit": f[1], "hours": _hours(f[1]),
                      "available": f[2], "nodes": f[3], "gres": f[4], "gpu": "gpu" in f[4].lower()})
    return parts


def probe_egress(host: str, port: int = 443, timeout: float = 5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def probe_https(url: str) -> bool:
    rc, _ = _run(["curl", "-sS", "-o", "/dev/null", "-m", "8", "-I", url], timeout=12)
    return rc == 0


def probe_quota() -> str:
    for cmd in (["quota", "-s"], ["lfs", "quota", "-h", "-u", os.environ.get("USER", ""), str(Path.home())]):
        rc, out = _run(cmd)
        if rc == 0 and out.strip():
            return out.strip()[:2000]
    return ""


def probe_slurm_config() -> dict[str, str]:
    rc, out = _run(["scontrol", "show", "config"])
    keep = ("PrivateData", "JobCompType", "MinJobAge", "ScronParameters", "AccountingStorageType")
    cfg: dict[str, str] = {}
    if rc == 0:
        for line in out.splitlines():
            m = re.match(r"\s*(\w+)\s*=\s*(.*)", line)
            if m and m.group(1) in keep:
                cfg[m.group(1)] = m.group(2).strip()
    return cfg


def choose_rung(partitions: list[dict], host_mode: str | None) -> tuple[str, str]:
    if host_mode == "external":
        return "A", "an always-on host was given; Brain runs there and reaches the cluster over SSH"
    cpu_long = [p for p in partitions if not p["gpu"] and p["hours"] >= 24 and p["available"] == "up"]
    if cpu_long:
        best = max(cpu_long, key=lambda p: p["hours"])
        return "B", f"partition '{best['name']}' accepts ≥24 h CPU jobs ({best['time_limit']}); run Brain as a chained SLURM job"
    if not partitions:
        return "C", "SLURM not visible from here; running on this node under self-imposed caps"
    return "C", "no CPU/service partition accepts a ≥24 h job; running on the login node under caps with a 12 h self-restart"


def run_probe(host_mode: str | None = None) -> dict[str, Any]:
    parts = probe_partitions()
    rung, why = choose_rung(parts, host_mode)
    prof = {
        "probed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "hostname": socket.gethostname(),
        "user": os.environ.get("USER", ""),
        "rung": rung,
        "rung_reason": why,
        "partitions": [{k: v for k, v in p.items() if k != "hours"} for p in parts],
        "egress": {
            "api.anthropic.com:443": probe_egress("api.anthropic.com"),
            "fcm.googleapis.com:443": probe_egress("fcm.googleapis.com"),
            "oauth2.googleapis.com:443": probe_egress("oauth2.googleapis.com"),
            "api.github.com:443": probe_egress("api.github.com"),
            "region1.v2.argotunnel.com:7844": probe_egress("region1.v2.argotunnel.com", 7844),
            "https_proxy": os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY") or "",
        },
        "slurm_config": probe_slurm_config(),
        "quota": probe_quota(),
        "tools": {t: bool(shutil.which(t)) for t in ("sbatch", "squeue", "sacct", "scancel", "git", "claude",
                                                      "codex", "gemini", "cloudflared", "tailscale")},
        "polling": {"squeue_floor_s": 60, "sentinel_s": 30},
        "caps": {"cpus": 2, "rss_gb": 4, "procs": 64, "hot_providers": 4},
    }
    return prof


def write_profile(path: Path, prof: dict) -> None:
    path.write_text(yaml.safe_dump(prof, sort_keys=False))
    os.chmod(path, 0o600)
