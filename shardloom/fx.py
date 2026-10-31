"""The effects: names, ranges and what they do (implemented in shaders.py).

Every effect is an amount from 0 (off) to 1 (full). Per-step effects, lane
effects, live punch-ins and music modulation all add up, clamped to 0..1.
"""

FX = {
    "zoom": "Push in on the picture",
    "shake": "Jolt the frame around",
    "mirror": "Fold left onto right",
    "kaleido": "Kaleidoscope (more amount, more segments)",
    "pixelate": "Big blocky pixels",
    "glitch": "Displaced blocks and torn lines",
    "rgb_split": "Pull the red and blue channels apart",
    "hue": "Rotate colours around the wheel",
    "saturate": "Push colour saturation",
    "bw": "Black and white, high contrast",
    "posterize": "Fewer colour levels",
    "invert": "Negative",
    "scanlines": "VHS lines and wobble",
    "colorize": "Map brightness onto the current palette",
}

# Only on the master (applied to the whole picture).
MASTER_FX = {
    "trails": "Each frame leaves an echo of the last",
    "flash": "Brighten",
    "strobe": "Black-out flicker on every step",
}

ALL_FX = {**FX, **MASTER_FX}

BLEND_MODES = ["normal", "add", "screen", "multiply", "difference"]

# Music features a modulation can follow (see analysis.Analysis.at).
SOURCES = ["bass", "mid", "high", "level", "onset", "intensity", "beat"]

# Three-colour gradients (dark, mid, light) for colorize; the palette changes with the music.
PALETTES = [
    ["#0b0420", "#d1206b", "#ffd98a"],
    ["#020b14", "#1f7a8c", "#e8f1c4"],
    ["#100808", "#c2410c", "#fde68a"],
    ["#05050a", "#6d28d9", "#a7f3d0"],
    ["#0a0a0a", "#9ca3af", "#fafafa"],
    ["#04140a", "#16a34a", "#f0abfc"],
]


def clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def add(*dicts: dict) -> dict:
    """Sum of effect amounts, clamped to 0..1."""
    out: dict[str, float] = {}
    for d in dicts:
        for k, v in (d or {}).items():
            out[k] = out.get(k, 0.0) + float(v)
    return {k: clamp01(v) for k, v in out.items()}


def hex_rgb(color: str) -> tuple[float, float, float]:
    c = color.lstrip("#")
    return tuple(int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
