"""Listening tests: the standard `listen.json` an agent writes, validation, safe file access, and rating summaries.

listen.json (paths are relative to the file's own folder; layout of the audio is up to the agent):
{
  "version": 1,
  "title": "Sampler sweep, step 10k",                     # shown on the phone
  "description": "kappa grid vs current, 64 vs 128 steps", # optional
  "systems": [{"id": "base", "label": "current grid, 64 steps"}, {"id": "kappa", "label": "kappa grid"}],
  "items": [{
      "id": "utt_001",
      "text": "The sentence that was synthesised.",          # optional but recommended
      "samples": {"base": "base/utt_001.wav", "kappa": "kappa/utt_001.wav"},
      "reference": "refs/utt_001.wav",                      # optional: ground-truth recording
      "prompt": "prompts/utt_001.wav",                       # optional: voice prompt
      "metrics": {"base": {"wer": 0.04}, "kappa": {"wer": 0.02}}   # optional, per system
  }],
  "metrics": {"base": {"wer": 0.051}, "kappa": {"wer": 0.032}}    # optional, per system aggregate
}
Results land next to it as listen_results.json and the thread is woken with a summary.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

AUDIO = {".wav", ".flac", ".mp3", ".ogg", ".opus", ".m4a", ".aac", ".webm"}
MEDIA = {".wav": "audio/wav", ".flac": "audio/flac", ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".opus": "audio/ogg",
         ".m4a": "audio/mp4", ".aac": "audio/aac", ".webm": "audio/webm", ".png": "image/png", ".jpg": "image/jpeg",
         ".jpeg": "image/jpeg", ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp"}
MAX_ITEMS = 500


class ListenError(ValueError):
    pass


def _inside(p: Path, roots: list[Path]) -> bool:
    return any(p == r or r in p.parents for r in roots)


def load(manifest: Path, roots: list[Path]) -> dict:
    """Parse and validate listen.json. Every referenced file must exist, be audio, and stay inside `roots`."""
    manifest = manifest.resolve()
    if not _inside(manifest, roots):
        raise ListenError(f"{manifest} is outside this thread's repo and vault")
    try:
        m = json.loads(manifest.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise ListenError(f"cannot read {manifest.name}: {e}")
    systems = m.get("systems")
    items = m.get("items")
    if not isinstance(systems, list) or not systems or not all(isinstance(s, dict) and s.get("id") for s in systems):
        raise ListenError('"systems" must be a non-empty list of {"id", "label"}')
    ids = [str(s["id"]) for s in systems]
    if len(set(ids)) != len(ids):
        raise ListenError("system ids must be unique")
    if not isinstance(items, list) or not items:
        raise ListenError('"items" must be a non-empty list')
    if len(items) > MAX_ITEMS:
        raise ListenError(f"at most {MAX_ITEMS} items per test")
    base = manifest.parent
    problems: list[str] = []

    def check(rel, where: str) -> str | None:
        if rel in (None, ""):
            return None
        p = (base / str(rel)).resolve()
        if not _inside(p, roots):
            problems.append(f"{where}: {rel} is outside this thread's repo and vault")
        elif not p.is_file():
            problems.append(f"{where}: {rel} does not exist")
        elif p.suffix.lower() not in AUDIO:
            problems.append(f"{where}: {rel} is not an audio file")
        return str(p.relative_to(base)) if _inside(p, [base]) else str(p)

    out_items = []
    for n, it in enumerate(items):
        iid = str(it.get("id") or n)
        samples = it.get("samples") or {}
        if not isinstance(samples, dict) or not samples:
            problems.append(f"item {iid}: needs a samples map {{system id: file}}")
            continue
        unknown = set(samples) - set(ids)
        if unknown:
            problems.append(f"item {iid}: unknown systems {sorted(unknown)}")
        out_items.append({"id": iid, "text": str(it.get("text") or ""),
                          "samples": {k: check(v, f"item {iid}/{k}") for k, v in samples.items() if k in ids},
                          "reference": check(it.get("reference"), f"item {iid}/reference"),
                          "prompt": check(it.get("prompt"), f"item {iid}/prompt"),
                          "metrics": it.get("metrics") if isinstance(it.get("metrics"), dict) else {}})
    if problems:
        raise ListenError("; ".join(problems[:8]) + (f" (+{len(problems) - 8} more)" if len(problems) > 8 else ""))
    return {"version": 1, "title": str(m.get("title") or manifest.parent.name), "description": str(m.get("description") or ""),
            "systems": [{"id": str(s["id"]), "label": str(s.get("label") or s["id"])} for s in systems],
            "items": out_items, "metrics": m.get("metrics") if isinstance(m.get("metrics"), dict) else {}}


def resolve_file(base: Path, rel: str, roots: list[Path], audio_only: bool = False) -> Path:
    p = (base / rel).resolve()
    if not _inside(p, roots) or not p.is_file():
        raise ListenError("no such file in this thread's repo or vault")
    if audio_only and p.suffix.lower() not in AUDIO:
        raise ListenError("not an audio file")
    return p


def summarize(test: dict, ratings: dict) -> dict:
    """ratings = {item id: {"scores": {system: 1..5}, "best": system | "tie" | None, "note": str}}"""
    sys_ids = [s["id"] for s in test["systems"]]
    label = {s["id"]: s["label"] for s in test["systems"]}
    scores: dict[str, list[float]] = {s: [] for s in sys_ids}
    wins = {s: 0 for s in sys_ids}
    ties = rated = 0
    for it in test["items"]:
        r = ratings.get(it["id"]) or {}
        got = False
        for s, v in (r.get("scores") or {}).items():
            if s in scores and isinstance(v, (int, float)) and 1 <= v <= 5:
                scores[s].append(float(v))
                got = True
        best = r.get("best")
        if best in wins:
            wins[best] += 1
            got = True
        elif best == "tie":
            ties += 1
            got = True
        rated += got
    per = {}
    for s in sys_ids:
        v = scores[s]
        mean = sum(v) / len(v) if v else None
        sd = math.sqrt(sum((x - mean) ** 2 for x in v) / (len(v) - 1)) if len(v) > 1 else 0.0
        per[s] = {"label": label[s], "mos": round(mean, 3) if mean is not None else None,
                  "mos_ci95": round(1.96 * sd / math.sqrt(len(v)), 3) if len(v) > 1 else None, "n_scores": len(v),
                  "best": wins[s]}
    order = sorted(sys_ids, key=lambda s: (-(per[s]["mos"] or 0), -wins[s]))
    lines = [f"{label[s]}: MOS {per[s]['mos']} ± {per[s]['mos_ci95']} (n={per[s]['n_scores']}), best in {wins[s]}"
             if per[s]["mos"] is not None else f"{label[s]}: best in {wins[s]}" for s in order]
    notes = [f"{it['id']}: {(ratings.get(it['id']) or {}).get('note')}" for it in test["items"]
             if (ratings.get(it["id"]) or {}).get("note")]
    return {"rated_items": rated, "total_items": len(test["items"]), "ties": ties, "systems": per,
            "ranking": order, "text": "\n".join(lines), "notes": notes}
