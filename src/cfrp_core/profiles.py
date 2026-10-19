"""Named profiles: a machine, a fibre / resin material, an ink process.

Each is a small JSON file of settings (the GUI's setting keys; for ink the
DIW settings) in the user's profile folder,
`~/.cfrp_slicer/profiles/<kind>/<name>.json` (or `$CFRP_PROFILE_DIR`).
Saving keeps only the keys that belong to the kind, so a machine profile
never changes the part.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .diw.config import DiwConfig

KINDS: dict[str, tuple[str, ...]] = {
    "machine": ("bed_x", "bed_y", "z_max", "bed_margin", "uv", "purge_e", "purge_f", "travel_f"),
    "material": ("needle_dia", "e_mult", "density", "weight", "track_gap", "min_radius", "layer_height",
                 "v_max", "v_min", "accel", "decel", "p_advance", "pump_stall_floor", "pa_table"),
    "ink": tuple(DiwConfig.model_fields),
}
LABELS = {"machine": "Machine", "material": "Fibre / resin", "ink": "Ink process"}


def profile_dir(kind: str, root=None) -> Path:
    if kind not in KINDS:
        raise ValueError(f"Unknown profile kind {kind!r} (machine, material or ink).")
    base = Path(root) if root else Path(os.environ.get("CFRP_PROFILE_DIR") or Path.home() / ".cfrp_slicer" / "profiles")
    return base / kind


def _file(kind, name, root):
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name)).strip(" .")
    if not clean:
        raise ValueError("A profile needs a name.")
    return profile_dir(kind, root) / f"{clean}.json"


def list_profiles(kind: str, root=None) -> list[str]:
    folder = profile_dir(kind, root)
    return sorted(p.stem for p in folder.glob("*.json")) if folder.is_dir() else []


def save_profile(kind: str, name: str, values: dict, root=None) -> Path:
    """Write the kind's keys of `values` as profile `name` (replacing it)."""
    keep = {k: v for k, v in values.items() if k in KINDS[kind]}
    if not keep:
        raise ValueError(f"Nothing to save: no {LABELS[kind].lower()} settings given.")
    if kind == "ink":
        DiwConfig.model_validate(keep)            # refuse an invalid ink process
    path = _file(kind, name, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(keep, indent=2), encoding="utf-8")
    return path


def load_profile(kind: str, name: str, root=None) -> dict:
    path = _file(kind, name, root)
    if not path.is_file():
        raise ValueError(f"No {LABELS[kind].lower()} profile named {name!r}.")
    data = json.loads(path.read_text(encoding="utf-8"))
    out = {k: v for k, v in data.items() if k in KINDS[kind]}
    if kind == "ink" and "tool_offset" in out:
        out["tool_offset"] = tuple(out["tool_offset"])
    return out


def delete_profile(kind: str, name: str, root=None) -> None:
    path = _file(kind, name, root)
    if path.is_file():
        path.unlink()


__all__ = ["KINDS", "LABELS", "delete_profile", "list_profiles", "load_profile", "profile_dir", "save_profile"]
