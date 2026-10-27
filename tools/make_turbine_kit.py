"""Write a wind turbine kit: the blade, hub disc and nose cone projects (one
set of settings, so the parts fit), the hub's 1:1 drilling template and the
assembly notes; with --gcode, slice every part and save its G-code too.

    python tools/make_turbine_kit.py FOLDER [--gcode] [key=value ...]

Settings start from the app's defaults (validation_sweep.BASE) with a
150 mm blade; key=value pairs override them (e.g. stem_height=120
hub_blades=4 track_gap=1.1). Exit 1 if a part fails its checks.
"""
import ast
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from validation_sweep import BASE  # noqa: E402

from cfrp_core import DEFAULT_MACHINE, from_legacy_dict, slice_part  # noqa: E402
from cfrp_core.turbine import kit_parts, write_kit  # noqa: E402


def _value(text):
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return text


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    folder = Path(args[0])
    params = dict(BASE, stem_height=150.0, strict_bend_radius=True)
    params.update({k: _value(v) for k, _, v in (a.partition("=") for a in args[1:])})
    for path in write_kit(folder, params, app="make_turbine_kit"):
        print("wrote", path)
    bad = False
    if "--gcode" in sys.argv:
        for name, p in kit_parts(params).items():
            t = time.time()
            res = slice_part(from_legacy_dict(p), DEFAULT_MACHINE)
            out = folder / f"{name}.gcode"
            out.write_text(res.gcode, encoding="utf-8")
            hours = res.stats["print_time_s"] / 3600.0
            print(f"wrote {out}: {res.validation['status']}, {res.max_z:.1f} mm tall, {hours:.1f} h, "
                  f"{res.stats['fiber_length_mm'] / 1000.0:.0f} m of fibre (sliced in {time.time() - t:.0f} s)")
            for msg in res.validation["messages"]:
                if not msg.startswith("✅"):
                    print("   ", msg)
            bad |= res.validation["status"] == "Fail"
    sys.exit(1 if bad else 0)
