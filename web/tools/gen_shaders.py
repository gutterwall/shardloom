"""Writes web/js/shaders.js from shardloom/shaders.py, so the phone app draws with the desktop's shaders.

Run it after changing shaders.py:  python web/tools/gen_shaders.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from shardloom import shaders  # noqa: E402

HEADER = "#version 300 es\nprecision highp float;\nprecision highp int;"


def es(source: str) -> str:
    """GLSL 3.30 core to GLSL ES 3.00 (WebGL2)."""
    out = source.strip().replace("#version 330", HEADER, 1)
    assert out.startswith(HEADER), "every shader starts with #version 330"
    return out + "\n"


def generate() -> str:
    lines = ["// Made by web/tools/gen_shaders.py from shardloom/shaders.py; do not edit by hand.", ""]
    for name in ("VERTEX", "EFFECTS", "BLEND", "MASTER", "OVERLAY", "EVENTS"):
        lines.append(f"export const {name} = {json.dumps(es(getattr(shaders, name)))};")
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
