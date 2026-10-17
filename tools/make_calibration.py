"""Write the calibration coupons (see cfrp_core.calibration) as G-code files.

    python tools/make_calibration.py [params.json] [out_dir]

params.json is a file saved with the GUI's File > Save Parameters (defaults
if omitted); out_dir defaults to the current folder.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cfrp_core import ProcessConfig, from_legacy_dict, resolve_process_constraints  # noqa: E402
from cfrp_core.calibration import COUPONS, make_coupon  # noqa: E402

if __name__ == "__main__":
    params = sys.argv[1] if len(sys.argv) > 1 else None
    out = Path(sys.argv[2] if len(sys.argv) > 2 else ".")
    cfg = from_legacy_dict(json.loads(Path(params).read_text(encoding="utf-8"))) if params else ProcessConfig()
    cfg = resolve_process_constraints(cfg).config
    out.mkdir(parents=True, exist_ok=True)
    for kind in COUPONS:
        path = out / f"calibration_{kind}.gcode"
        path.write_text(make_coupon(kind, cfg)[1], encoding="utf-8")
        print("wrote", path)
