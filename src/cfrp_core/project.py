"""One project file (`.cfrpproj`, JSON) holding everything needed to slice a
part again: the slicer settings, the structural-PCB design, the ink
settings and the print mode.

The STL model is referenced, not embedded: its path is stored relative to
the project file when it lies beside it (so a project folder can be moved
or shared) and absolute otherwise.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .diw.config import DiwConfig
from .diw.design import BoardDesign

FORMAT = "cfrpproj"
VERSION = 1
SUFFIX = ".cfrpproj"
_KEYS = {"format", "version", "params", "board", "diw", "print_mode", "app"}


def save_project(path, params: dict, board: BoardDesign | None = None, diw: DiwConfig | dict | None = None,
                 print_mode: str = "structural", app: str = "") -> Path:
    """Write the project; returns the path written."""
    path = Path(path)
    params = dict(params)
    stl = params.get("stl_path")
    if stl:
        try:
            params["stl_path"] = os.path.relpath(Path(stl).resolve(), path.resolve().parent)
            if params["stl_path"].startswith(".."):
                params["stl_path"] = str(Path(stl).resolve())
        except ValueError:                        # another drive on Windows
            params["stl_path"] = str(Path(stl).resolve())
    if isinstance(diw, DiwConfig):
        diw = diw.model_dump(mode="json")
    data = {"format": FORMAT, "version": VERSION, "app": app, "params": params,
            "board": board.model_dump(mode="json") if board is not None else None,
            "diw": diw, "print_mode": print_mode}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def load_project(path) -> dict:
    """{params, board (BoardDesign | None), diw (DiwConfig | None),
    print_mode, ignored: [keys this version does not know]}. Raises
    ValueError in plain words for a file that is not a project."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read {path.name}: {exc}") from exc
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError(f"{path.name} is not a CFRP slicer project (.cfrpproj).")
    if int(data.get("version", 0)) > VERSION:
        raise ValueError(f"{path.name} was saved by a newer version (project format {data['version']}); "
                         f"update the slicer to open it.")
    params = dict(data.get("params") or {})
    stl = params.get("stl_path")
    if stl and not Path(stl).is_absolute():
        params["stl_path"] = str((path.parent / stl).resolve())
    board = BoardDesign.model_validate(data["board"]) if data.get("board") else None
    ignored = sorted(set(data) - _KEYS)
    diw = None
    if data.get("diw"):
        known = {k: v for k, v in data["diw"].items() if k in DiwConfig.model_fields}
        ignored += sorted(f"diw.{k}" for k in set(data["diw"]) - set(known))
        if "tool_offset" in known:
            known["tool_offset"] = tuple(known["tool_offset"])
        diw = DiwConfig.model_validate(known)
    return {"params": params, "board": board, "diw": diw, "print_mode": data.get("print_mode", "structural"),
            "ignored": ignored}


__all__ = ["FORMAT", "SUFFIX", "VERSION", "load_project", "save_project"]
