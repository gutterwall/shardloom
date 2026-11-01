"""Shard effects: glitches, analog faults, film burns, fireworks and explosions that strike now and then.

The Colour ones follow the music: how far they push the picture is the song's intensity or level
at that moment, and their strikes join up into one stretch when they come close together.

Each one has three settings, kept in Project.shard_fx[name]:
  rate      0..1   how often it strikes
  sync     -1..1   -1 between the pulses, 0 anywhere, +1 on the pulses (the beats and the hits)
  strength  0..1   how hard it hits the picture

Whether an effect strikes on a step is a random draw that depends only on the project's seed,
the effect and the step, so a render is the same every time and from any starting point.
The shader for all of them is shaders.EVENTS; the index of each here is its index there.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Event:
    label: str
    group: str
    about: str
    seconds: float      # how long one strike lasts
    envelope: str       # "spike": hits then fades; "swell": rises and falls; "flat": holds then fades
    follows: str = ""   # a music curve (analysis) that sets how far it goes; "" for a fixed strike
    both_ways: bool = False   # follows the curve both ways from its middle (quiet: less, loud: more)
    peak: float = 0.4   # the chance of a strike on a step at full rate with no pulse preference


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
}
NAMES = list(EVENTS)
GROUPS = ["Digital glitch", "Analog", "Film", "Pyrotechnics", "Colour"]


def default_setting() -> dict:
    return {"rate": 0.0, "sync": 0.0, "strength": 0.7}


def setting(project, name: str) -> dict:
    return {**default_setting(), **(getattr(project, "shard_fx", {}) or {}).get(name, {})}


def envelope(kind: str, age: float) -> float:
    """0..1 over a strike's life (age 0..1)."""
    if age < 0.0 or age >= 1.0:
        return 0.0
    if kind == "spike":
        return (1.0 - age) ** 2
    if kind == "swell":
        return math.sin(math.pi * age)
    return min(1.0, age / 0.04, (1.0 - age) / 0.15)


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

    def reset(self) -> None:
        self._steps.clear()

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
            if s["rate"] > 0.0:
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
            age = (t - hit[0]) / ev.seconds
            amount = strength * max(held, envelope(ev.envelope, age))
            if ev.follows:
                if music is None:
                    music = e.analysis.at(max(0.0, t))
                level = min(1.0, max(0.0, music.get(ev.follows, 0.0)))
                amount *= 2.0 * level - 1.0 if ev.both_ways else level
                hit = (hit[0], rand(p.seed, 104729, i))     # the same direction all through the song
            if amount != 0.0:
                out[name] = (amount, age, hit[1])
        return out
