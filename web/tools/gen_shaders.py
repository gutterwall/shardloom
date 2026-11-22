"""Writes web/js/shaders.js from shardloom/shaders.py, so the phone app draws with the desktop's shaders, and the
MilkDrop-style visuals' presets from shardloom/visuals.py.

Run it after changing shaders.py:  python web/tools/gen_shaders.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from shardloom import shaders, visuals  # noqa: E402

HEADER = "#version 300 es\nprecision highp float;\nprecision highp int;"


def es(source: str) -> str:
    """GLSL 3.30 core to GLSL ES 3.00 (WebGL2)."""
    out = source.strip().replace("#version 330", HEADER, 1)
    assert out.startswith(HEADER), "every shader starts with #version 330"
    return out + "\n"


def generate() -> str:
    lines = ["// Made by web/tools/gen_shaders.py from shardloom/shaders.py; do not edit by hand.", ""]
    for name in ("VERTEX", "EFFECTS", "BLEND", "MASTER", "OVERLAY", "EVENTS", "VISUAL_WARP", "VISUAL_COMP"):
        lines.append(f"export const {name} = {json.dumps(es(getattr(shaders, name)))};")
        lines.append("")
    lines.append("// The visuals' numbers (shardloom/visuals.py).")
    for name, value in (("VISUAL_RATE", visuals.RATE), ("VISUAL_FEED_HEIGHT", visuals.FEED_HEIGHT),
                        ("VISUAL_PARAMS", visuals.PARAMS), ("VISUAL_WAVES", visuals.WAVES),
                        ("VISUAL_PRESETS", visuals.PRESETS),
                        ("VISUAL_BLENDS", {k: list(v) for k, v in visuals.BLENDS.items()})):
        lines.append(f"export const {name} = {json.dumps(value)};")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    target = ROOT / "web" / "js" / "shaders.js"
    text = generate()
    if "--check" in sys.argv:
        if not target.exists() or target.read_text(encoding="utf-8") != text:
            sys.exit("web/js/shaders.js is out of date: run python web/tools/gen_shaders.py")
        print("web/js/shaders.js is up to date")
    else:
        target.write_text(text, encoding="utf-8")
        print(f"wrote {target}")
