"""Shard effects: glitches, analog faults, film burns, fireworks and explosions that strike now and then.

The Colour ones follow the music: how far they push the picture is the song's intensity or level
at that moment, and their strikes join up into one stretch when they come close together.

Each one has three settings, kept in Project.shard_fx[name]:
  rate      0..1   how often it strikes
  sync     -1..1   -1 between the pulses, 0 anywhere, +1 on the pulses (the beats and the hits)
  strength  0..1   how hard it hits the picture

Whether an effect strikes on a step is a random draw that depends only on the project's seed,
the effect and the step, so a render is the same every time and from any starting point.
The shader for the whole-picture ones is shaders.EVENTS; the index of each here is its index there.

Colour flip is a strobe: while it strikes it flashes on and off in time with the beats, and each flash
turns over one or more of the red, green and blue channels, a new mix each time (flip_channels()).

The One layer effects (lens, mirror) strike the same way but land on one or more of the layers instead of
the whole picture: layer_strikes() says which layers and how, and shaders.EFFECTS draws them on each layer.

The Visuals effect (MilkDrop-style visuals) strikes the same way too, and is drawn into the layer stack:
see visuals.py.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field

from .visuals import DEFAULT_OPTIONS as DEFAULT_VISUALS


@dataclass(frozen=True)
class Event:
    label: str
    group: str
    about: str
    seconds: float      # how long one strike lasts
    envelope: str       # "spike": hits then fades; "swell": rises and falls; "flat": holds then fades;
                        # "fade": fades in slowly, holds, fades out
    follows: str = ""   # a music curve (analysis) that sets how far it goes; "" for a fixed strike
    both_ways: bool = False   # follows the curve both ways from its middle (quiet: less, loud: more)
    peak: float = 0.4   # the chance of a strike on a step at full rate with no pulse preference
    layer: bool = False  # lands on layers (layer_strikes) rather than the whole picture
    stack: bool = False  # drawn into the layer stack (visuals.strike) rather than over the whole picture
    options: dict = field(default_factory=dict)   # its own settings beyond rate, sync and strength, with defaults


EVENTS = {
    "datamosh": Event("Datamosh", "Digital glitch", "Blocks smear the last frame along with the motion", 0.6, "flat"),
    "pixel_sort": Event("Pixel sort", "Digital glitch", "Bright pixels drag into streaks", 0.5, "swell"),
    "corrupt": Event("Corruption", "Digital glitch", "Broken blocks: swapped channels, wrong colours, copies", 0.3, "flat"),
    "tear": Event("Slice tear", "Digital glitch", "Horizontal slices torn sideways", 0.25, "spike"),
    "vhs": Event("VHS tracking", "Analog", "A rolling tracking band with snow and wobble", 0.8, "swell"),
    "crt": Event("CRT", "Analog", "Curved glass, scanlines, phosphor mask and glow", 1.0, "swell"),
    "roll": Event("Vertical roll", "Analog", "The picture loses hold and rolls", 0.5, "spike"),
    "bleed": Event("Chroma bleed", "Analog", "Colour smears off the picture with a ghost", 0.7, "swell"),
    "burn": Event("Film burn", "Film", "The frame catches in the gate and burns through", 1.6, "flat"),
    "leak": Event("Light leak", "Film", "Warm light floods in from the edge", 1.4, "swell"),
    "damage": Event("Film damage", "Film", "Scratches, dust, weave and flicker", 1.2, "flat"),
    "fireworks": Event("Fireworks", "Pyrotechnics", "Bursts of sparks that fall and fade", 2.2, "flat"),
    "explosion": Event("Explosion", "Pyrotechnics", "A fireball, shockwave, flash and shake", 1.4, "flat"),
    "hue_shift": Event("Hue shift", "Colour", "Turns the colours round the wheel, further when the music is intense",
                       0.4, "flat", "intensity", peak=1.0),
    "brightness": Event("Brightness", "Colour", "Brighter when the music is loud, darker when it is quiet",
                        0.4, "flat", "level", True, peak=1.0),
    "saturation": Event("Saturation", "Colour", "Richer colour when the music is intense, greyer when it is calm",
                        0.4, "flat", "intensity", True, peak=1.0),
    "lens": Event("Colour lens", "One layer", "A coloured lens fades in over one of the overlay layers: "
                  "a new random colour each time", 3.0, "fade", layer=True),
    "mirror": Event("Mirror", "One layer", "Folds layers into mirror images or a kaleidoscope that zooms "
                    "from the middle or the edges, on the beat", 4.0, "flat", layer=True,
                    options={"style": "kaleido", "segments": 6, "zoom": "middle", "depth": 0.5, "layers": 1,
                             "beat": True, "leaves": 0.0}),
    # Last, so the effects before it keep their strikes (the index is part of each draw).
    "flip": Event("Colour flip", "Colour", "A strobe that turns over one or more of the red, green and blue "
                  "channels, a new mix on every flash", 1.5, "flat", options={"flashes": 2}),
    # After flip for the same reason.
    "visuals": Event("MilkDrop", "Visuals", "A MilkDrop-style music visual, drawn live and mixed into the layers: "
                     "plus, minus, occlude, overlay, multiply or behind them as a background", 8.0, "fade",
                     peak=0.25, stack=True, options=DEFAULT_VISUALS),
}
NAMES = list(EVENTS)
# The whole-picture ones, in shaders.EVENTS order.
PICTURE = [n for n in NAMES if not EVENTS[n].layer and not EVENTS[n].stack]
LAYER = [n for n in NAMES if EVENTS[n].layer]          # the One layer ones
GROUPS = ["Digital glitch", "Analog", "Film", "Pyrotechnics", "Colour", "One layer", "Visuals"]

MIRROR_STYLES = ["mirror", "quad", "kaleido"]           # shaders.EFFECTS fold_style 0, 1, 2
MIRROR_ZOOMS = ["middle", "edges", "none"]
FLASHES = [1, 2, 4]                                     # Colour flip flashes per beat


def default_setting() -> dict:
    return {"rate": 0.0, "sync": 0.0, "strength": 0.7}


def setting(project, name: str) -> dict:
    ev = EVENTS.get(name)
    out = {**default_setting(), **(ev.options if ev else {}), **(getattr(project, "shard_fx", {}) or {}).get(name, {})}
    # Lists (the visuals' presets and blends) are copied, so changing them never changes the defaults or the project.
    return {k: list(v) if isinstance(v, list) else v for k, v in out.items()}


def envelope(kind: str, age: float) -> float:
    """0..1 over a strike's life (age 0..1)."""
    if age < 0.0 or age >= 1.0:
        return 0.0
    if kind == "spike":
        return (1.0 - age) ** 2
    if kind == "swell":
        return math.sin(math.pi * age)
    if kind == "fade":
        return min(1.0, age / 0.4, (1.0 - age) / 0.25)
    return min(1.0, age / 0.04, (1.0 - age) / 0.15)


def hsv(h: float, s: float, v: float) -> tuple[float, float, float]:
    """As hsv() in the shaders (h 0..1)."""
    def k(n):
        x = abs((h * 6.0 + n) % 6.0 - 3.0) - 1.0
        return min(1.0, max(0.0, x))
    return tuple(v * (1.0 + (k(n) - 1.0) * s) for n in (0.0, 4.0, 2.0))


def flip_channels(sd: float) -> tuple[bool, bool, bool]:
    """Which of red, green and blue a Colour flip flash with this seed turns over (as shaders.EVENTS)."""
    m = 1 + min(6, int(sd * 7.0))
    return bool(m & 1), bool(m & 2), bool(m & 4)


def strobe(project, beats: float, sd: float) -> tuple[bool, float]:
    """Colour flip at song position beats: (lit now, the seed of this flash), for a strike with seed sd."""
    from .sequencer import rand

    f = int(setting(project, "flip")["flashes"])
    f = f if f in FLASHES else 2
    phase = beats * f
    k = math.floor(phase)
    return phase - k < 0.5, rand(project.seed, 6007, k, int(math.floor(sd * 1e6)))


def pick_layers(project, name: str, seed: float, count: int) -> list[int]:
    """Which layers a strike of a One layer effect lands on: count of them, from its seed.

    The lens goes on the overlay layers (above the first) when there are any; the mirror on any layer."""
    from .sequencer import rand

    lanes = [li for li, lane in enumerate(project.lanes) if not lane.mute]
    if name == "lens" and len(lanes) > 1:
        lanes = [li for li in lanes if li > 0] or lanes
    key = int(math.floor(seed * 1e6))
    i = NAMES.index(name)
    lanes.sort(key=lambda li: (rand(project.seed, 3571, i, li, key), li))
    return lanes[:max(1, count)]


def layer_strikes(project, hits: dict, beats: float) -> dict[int, dict[str, float]]:
    """layer index -> the shader settings of the One layer effects striking it (see shaders.EFFECTS).

    hits: Scheduler.at(t); beats: the song position in beats, for the mirror's beat moves."""
    out: dict[int, dict[str, float]] = {}
    hit = hits.get("lens")
    if hit:
        amount, _, sd = hit
        r, g, b = hsv(sd * 7.0 % 1.0, 0.85, 1.0)
        for li in pick_layers(project, "lens", sd, 1):
            out.setdefault(li, {}).update({"lens": amount, "lens_r": r, "lens_g": g, "lens_b": b})
    hit = hits.get("mirror")
    if hit:
        amount, age, sd = hit
        s = setting(project, "mirror")
        style = MIRROR_STYLES.index(s["style"]) if s["style"] in MIRROR_STYLES else 2
        segments = float(min(12, max(3, int(round(float(s["segments"]))))))
        depth = min(1.0, max(0.0, float(s["depth"])))
        frac = beats - math.floor(beats)
        if s["beat"]:
            zoom = depth * (1.0 - frac) ** 2                     # punches in on each beat and eases off
            notch = (math.pi / 2.0, math.pi / 4.0, math.pi / segments)[style]
            turn = math.floor(beats) * notch                     # a notch round on each beat
        else:
            zoom = depth * math.sin(math.pi * min(1.0, max(0.0, age)))
            turn = age * 1.2
        zoom *= {"middle": 1.0, "edges": -1.0}.get(s["zoom"], 0.0)
        turn += sd * math.tau
        leaves = min(1.0, max(0.0, float(s.get("leaves", 0.0))))
        # The leaves' colours: new ones on each beat when on the beat.
        leaf_seed = (sd + 0.6180339887 * math.floor(beats)) % 1.0 if s["beat"] else sd
        for li in pick_layers(project, "mirror", sd, int(s["layers"])):
            out.setdefault(li, {}).update({"fold": amount, "fold_style": float(style), "fold_segments": segments,
                                           "fold_zoom": zoom, "fold_turn": turn % math.tau,
                                           "fold_leaves": leaves, "fold_seed": leaf_seed})
    return out


def chance(rate: float, sync: float, pulse: float, peak: float = 0.4) -> float:
    """The chance of a strike on a step with this pulse (0 between the beats .. 1 on a strong beat)."""
    weight = 1.0 + sync * (2.0 * pulse - 1.0)
    return max(0.0, min(1.0, peak * rate ** 1.5 * weight))


class Scheduler:
    """Which shard effects are striking at a time, for one engine."""

    def __init__(self, engine):
        self.engine = engine
        self._steps: dict[int, tuple[float, float]] = {}
        self.manual: dict[str, float] = {}      # name -> time it was set off by hand (not saved)
        # Effects drawn into the stack take turns (turns()): name -> (settings, last step looked at, the steps
        # their strikes started on).
        self._turns: dict[str, tuple[tuple, int, list[int]]] = {}
        self.started: dict[str, float] = {}     # name -> when the strike at the last at() began (exactly)

    def reset(self) -> None:
        self._steps.clear()
        self._turns.clear()

    def turns(self, i: int, name: str, s: dict, now: int) -> list[int]:
        """The steps up to now on which an effect that takes turns (the visuals) starts a strike: as any
        effect, but a new one only once the last has finished. Worked out from the first step on and kept,
        so it is the same from any starting point."""
        from .sequencer import rand

        p = self.engine.project
        ev = EVENTS[name]
        key = (p.seed, float(s["rate"]), float(s["sync"]))
        got = self._turns.get(name)
        if got is None or got[0] != key:
            got = (key, -1, [])
        _, done, starts = got
        for g in range(done + 1, now + 1):
            t0, pulse = self.step(g)
            if starts and t0 < self.step(starts[-1])[0] + ev.seconds:
                continue
            if rand(p.seed, 7919, i, g) < chance(float(s["rate"]), float(s["sync"]), pulse, ev.peak):
                starts.append(g)
        self._turns[name] = (key, max(done, now), starts)
        return starts

    def step(self, g: int) -> tuple[float, float]:
        """(start time, pulse) of step g; the pulse is the beat itself and how hard the music hits there."""
        got = self._steps.get(g)
        if got is None:
            e = self.engine
            t = e.time_of_step(g)
            on_beat = 1.0 if g % max(1, e.project.steps_per_beat) == 0 else 0.0
            onset = e.analysis.at(max(0.0, t)).get("onset", 0.0)
            got = (t, 0.6 * on_beat + 0.4 * min(1.0, max(0.0, onset)))
            if len(self._steps) > 4096:
                self._steps.clear()
            self._steps[g] = got
        return got

    def fire(self, name: str, t: float) -> None:
        self.manual[name] = t

    def at(self, t: float) -> dict[str, tuple[float, float, float]]:
        """name -> (amount, age 0..1, seed 0..1) for each effect striking at t.

        The amount of a Colour effect runs -1..1 for one that follows the music both ways."""
        from .sequencer import rand

        e = self.engine
        p = e.project
        out = {}
        music = None
        now = e.step_at(t)
        for i, name in enumerate(NAMES):
            ev = EVENTS[name]
            s = setting(p, name)
            strength = float(s["strength"])
            if strength <= 0.0:
                continue
            hit = None
            held = 0.0          # Colour effects: the strongest of the strikes still going, so they join up
            if s["rate"] > 0.0 and ev.stack:
                starts = self.turns(i, name, s, now)
                k = bisect.bisect_right(starts, now) - 1
                if k >= 0:
                    t0 = self.step(starts[k])[0]
                    if t0 <= t < t0 + ev.seconds:
                        hit = (t0, rand(p.seed, 104729, i, starts[k]))
            elif s["rate"] > 0.0:
                g = now
                while g >= 0:
                    t0, pulse = self.step(g)
                    if t0 < t - ev.seconds:
                        break
                    if t0 <= t and rand(p.seed, 7919, i, g) < chance(float(s["rate"]), float(s["sync"]), pulse, ev.peak):
                        if hit is None:
                            hit = (t0, rand(p.seed, 104729, i, g))
                        if not ev.follows:
                            break
                        held = max(held, envelope(ev.envelope, (t - t0) / ev.seconds))
                    g -= 1
            by_hand = self.manual.get(name)
            if by_hand is not None and by_hand <= t < by_hand + ev.seconds and (hit is None or by_hand > hit[0]):
                hit = (by_hand, (by_hand * 7.31) % 1.0)
            if hit is None:
                continue
            self.started[name] = hit[0]
            age = (t - hit[0]) / ev.seconds
            amount = strength * max(held, envelope(ev.envelope, age))
            if ev.follows:
                if music is None:
                    music = e.analysis.at(max(0.0, t))
                level = min(1.0, max(0.0, music.get(ev.follows, 0.0)))
                amount *= 2.0 * level - 1.0 if ev.both_ways else level
                hit = (hit[0], rand(p.seed, 104729, i))     # the same direction all through the song
            sd = hit[1]
            if name == "flip":
                lit, sd = strobe(p, e.beats(t), sd)
                if not lit:
                    continue
            if amount != 0.0:
                out[name] = (amount, age, sd)
        return out
