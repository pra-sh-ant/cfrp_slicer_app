"""Regenerate the golden reference files. Run deliberately, never from CI:

    python -m tests.golden.regenerate            # all cases
    python -m tests.golden.regenerate NAME ...   # selected cases

Regenerating overwrites the reference behaviour; do it only for an
intentional, CHANGELOG-listed behaviour change.
"""
import json
import sys
from pathlib import Path

import numpy as np

from tests.golden.cases import CASES
from tests.golden.runner import run_case

GOLDEN_DIR = Path(__file__).resolve().parent / "data"


def write_case(case):
    gcode, arrays, meta = run_case(case)
    out = GOLDEN_DIR / case["name"]
    out.mkdir(parents=True, exist_ok=True)
    # newline="\n": byte-identical on every OS.
    (out / "output.gcode").write_text(gcode, encoding="utf-8", newline="\n")
    np.savez_compressed(out / "trajectory.npz", **arrays)
    (out / "meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    (out / "config.json").write_text(
        json.dumps({"config": case["config"], "layup": case["layup"]}, indent=2) + "\n",
        encoding="utf-8", newline="\n",
    )
    return len(arrays["x"]), meta["status"]


def main(names):
    selected = [c for c in CASES if not names or c["name"] in names]
    for case in selected:
        n, status = write_case(case)
        print(f"{case['name']:32s} {n:7d} pts  {status}")


if __name__ == "__main__":
    main(sys.argv[1:])
