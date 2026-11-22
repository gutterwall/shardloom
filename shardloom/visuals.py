"""MilkDrop-style visuals: music visualisations drawn live and mixed into the layers as a shard effect.

Like the MilkDrop visualiser, each one is a feedback loop: every step the last picture is warped (zoomed, turned,
rippled, drifted) and dimmed, and a new wave and shapes, moved by the music, are drawn over it. The presets below
are Shardloom's own, written for it; it doesn't run MilkDrop presets or share MilkDrop's code.

When the "visuals" shard effect strikes (events.EVENTS), strike() picks one of the enabled presets, one of the
enabled ways of mixing it in (BLENDS) and the layer it goes over, all from the strike's seed. The renderer then
steps the visual RATE times a second of song time from the start of the strike (shaders.VISUAL_WARP), at a fixed
size (feed_size), and mixes it into the layers (shaders.VISUAL_COMP, then shaders.BLEND). Because the steps follow
song time from the strike's start and the music each step hears comes with the frame state, the visual is the
same in the preview and the render, at any frame rate, from any starting point.
"""

from __future__ import annotations

import math

RATE = 30                 # visual steps a second of song time
FEED_HEIGHT = 360         # the feedback picture's height; its width follows the output's shape

# The ways a visual is mixed into the layers, in the panel's order, with the BLEND shader mode each uses.
BLENDS = {
    "add": ("Plus", "Adds its light to the layers under it", 1),
    "subtract": ("Minus", "Takes its light away from the layers under it", 5),
    "occlude": ("Occlude", "Covers the layers under it wherever it is drawn", 7),
    "overlay": ("Overlay", "Lightens and darkens the layers under it, keeping their contrast", 6),
    "behind": ("Background", "Goes behind the whole picture, showing through where it is dark", 8),
    "multiply": ("Multiply", "Multiplies into the layers under it: they show where it is lit", 9),
}

# Wave shapes (shaders.VISUAL_WARP).
WAVES = ["circle", "line", "spiral", "scope", "rays", "lines"]

# The numbers of a preset, in the order shaders.VISUAL_WARP and VISUAL_COMP read them (uniform pre[24]).
PARAMS = [
    "decay",        # how much of the last picture stays each step (0.85 .. 0.995)
    "zoom",         # > 1 zooms in each step, < 1 out
    "zoom_bass",    # the bass pushes the zoom this much
    "rot",          # radians a step
    "rot_mid",      # the mids turn it this much more
    "warp",         # rippling
    "warp_speed",
    "dx", "dy",     # drift a step
    "wander",       # how far the centre wanders
    "wave",         # index into WAVES
    "wave_size",
    "wave_width",
    "hue",          # the wave's colour (0..1 round the wheel)
    "hue_speed",    # turns a second
    "sat",
    "sides",        # shapes: 0 none, else 3 .. 8 sides
    "shape_size",
    "shape_spin",
    "shapes",       # how many, round a ring
    "echo",         # a mirrored, zoomed copy of itself (0..1)
    "kaleido",      # folds the picture into this many segments (0 none)
    "gamma",        # brightens (0..1)
    "solarize",     # turns over the bright parts (0..1)
]


def _preset(label: str, **values: float) -> dict:
    base = {name: 0.0 for name in PARAMS}
    base.update({"decay": 0.96, "zoom": 1.01, "wave_size": 1.0, "wave_width": 1.0, "sat": 0.8, "shapes": 1.0})
    base.update(values)
    return {"label": label, **base}


PRESETS = {
    "nebula": _preset("Nebula", decay=0.975, zoom=1.012, zoom_bass=0.02, rot=0.004, rot_mid=0.01, warp=0.6,
                      warp_speed=0.7, wander=0.4, wave=0, hue=0.62, hue_speed=0.05, sat=0.7, gamma=0.25),
    "tunnel": _preset("Tunnel", decay=0.925, zoom=1.045, zoom_bass=0.05, rot=0.012, wave=2, wave_size=0.7,
                      hue=0.05, hue_speed=0.08, sat=0.85),
    "scope": _preset("Scope", decay=0.93, zoom=0.995, rot=-0.003, dx=0.15, wave=3, wave_width=1.3, hue=0.33,
                     hue_speed=0.03, sat=0.9, echo=0.5),
    "starburst": _preset("Starburst", decay=0.95, zoom=0.975, zoom_bass=-0.03, wave=4, wave_size=1.2, hue=0.12,
                         hue_speed=0.12, sat=0.9, gamma=0.2),
    "ripple": _preset("Ripple", decay=0.955, zoom=1.0, warp=1.4, warp_speed=1.3, dy=0.25, wave=1, wave_width=1.2,
                      hue=0.55, hue_speed=0.04, sat=0.75),
    "polygons": _preset("Polygons", decay=0.94, zoom=1.02, rot=0.006, wave=0, wave_size=0.6, hue=0.8,
                        hue_speed=0.06, sides=6, shape_size=1.0, shape_spin=0.6, shapes=5),
    "bloom": _preset("Kaleido bloom", decay=0.97, zoom=1.015, rot=0.008, rot_mid=0.02, warp=0.4, warp_speed=0.5,
                     wave=0, wave_size=0.8, hue=0.9, hue_speed=0.07, sides=3, shape_size=0.7, shape_spin=-0.4,
                     shapes=3, kaleido=6, echo=0.3),
    "vortex": _preset("Vortex", decay=0.97, zoom=1.03, zoom_bass=0.03, rot=0.03, rot_mid=0.03, warp=0.3,
                      warp_speed=0.9, wave=1, wave_size=0.9, hue=0.7, hue_speed=0.09, sat=0.85),
    "aurora": _preset("Aurora", decay=0.95, zoom=1.004, warp=0.8, warp_speed=0.35, dy=0.12, wander=0.2, wave=5,
                      wave_width=1.6, hue=0.4, hue_speed=0.02, sat=0.65, gamma=0.1),
    "solar": _preset("Solar", decay=0.96, zoom=1.02, zoom_bass=0.04, rot=-0.008, warp=0.5, warp_speed=0.6, wave=0,
                     wave_size=1.3, wave_width=1.4, hue=0.07, hue_speed=0.015, sat=0.95, sides=4, shape_size=0.5,
                     shape_spin=0.3, shapes=4, solarize=0.6),
}

DEFAULT_OPTIONS = {"presets": list(PRESETS), "blends": list(BLENDS)}


def pack(key: str) -> list[float]:
    """A preset's numbers in PARAMS order, for the shaders."""
    p = PRESETS[key]
    return [float(p[name]) for name in PARAMS]


def feed_size(width: int, height: int) -> tuple[int, int]:
    """The feedback picture's size for an output of this shape (the same for the preview and the render)."""
    return max(64, min(1280, int(round(FEED_HEIGHT * width / max(1, height))))), FEED_HEIGHT


def enabled(options: dict, key: str, every: dict) -> list[str]:
    chosen = options.get(key)
    names = [n for n in (chosen if isinstance(chosen, (list, tuple)) else every) if n in every]
    return names or list(every)


def strike(project, hit: tuple[float, float, float], t0: float, seconds: float) -> dict:
    """What a strike of the visuals effect draws: hit is (amount, age 0..1, seed) from events.Scheduler and t0
    when the strike began. Returns the preset, how it is mixed in, the layer it goes over, the strike's start and
    how many steps the visual has taken since."""
    from .events import setting
    from .sequencer import rand

    amount, age, sd = hit
    s = setting(project, "visuals")
    presets = enabled(s, "presets", PRESETS)
    blends = enabled(s, "blends", BLENDS)
    key = int(math.floor(sd * 1e6))
    preset = presets[int(rand(project.seed, 8111, key) * len(presets))]
    blend = blends[int(rand(project.seed, 8117, key) * len(blends))]
    lanes = [li for li, lane in enumerate(project.lanes) if not lane.mute]
    if blend == "behind" or not lanes:
        after = len(project.lanes) - 1
    else:
        after = lanes[int(rand(project.seed, 8123, key) * len(lanes))]
    return {"preset": preset, "blend": blend, "after": after, "amount": amount, "seed": sd,
            "t0": t0, "steps": int(math.floor(age * seconds * RATE + 1e-6))}


def music_at(analysis, t: float) -> tuple[float, float, float, float, float]:
    """What a visual step at song time t hears: bass, mid, high, onset and the beat (1 on it, falling away)."""
    m = analysis.at(max(0.0, t))
    return tuple(float(m.get(k, 0.0)) for k in ("bass", "mid", "high", "onset", "beat"))
