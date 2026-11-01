"""Shard effects: glitches, analog faults, film burns, fireworks and explosions that strike now and then.

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
}
NAMES = list(EVENTS)
GROUPS = ["Digital glitch", "Analog", "Film", "Pyrotechnics"]

# Chance of a strike on a step at full rate with no pulse preference.
PEAK = 0.4


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


def chance(rate: float, sync: float, pulse: float) -> float:
    """The chance of a strike on a step with this pulse (0 between the beats .. 1 on a strong beat)."""
    weight = 1.0 + sync * (2.0 * pulse - 1.0)
    return max(0.0, min(1.0, PEAK * rate ** 1.5 * weight))


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
        """name -> (amount, age 0..1, seed 0..1) for each effect striking at t."""
        from .sequencer import rand

        e = self.engine
        p = e.project
        out = {}
        now = e.step_at(t)
        for i, name in enumerate(NAMES):
            ev = EVENTS[name]
            s = setting(p, name)
            strength = float(s["strength"])
            if strength <= 0.0:
                continue
            hit = None
            if s["rate"] > 0.0:
                g = now
                while g >= 0:
                    t0, pulse = self.step(g)
                    if t0 < t - ev.seconds:
                        break
                    if t0 <= t and rand(p.seed, 7919, i, g) < chance(float(s["rate"]), float(s["sync"]), pulse):
                        hit = (t0, rand(p.seed, 104729, i, g))
                        break
                    g -= 1
            by_hand = self.manual.get(name)
            if by_hand is not None and by_hand <= t < by_hand + ev.seconds and (hit is None or by_hand > hit[0]):
                hit = (by_hand, (by_hand * 7.31) % 1.0)
            if hit is None:
                continue
            age = (t - hit[0]) / ev.seconds
            amount = strength * envelope(ev.envelope, age)
            if amount > 0.0:
                out[name] = (amount, age, hit[1])
        return out
