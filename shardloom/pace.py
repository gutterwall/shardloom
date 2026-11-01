"""Shard timing: holding each layer on one shard for a while instead of cutting on the sequencer's steps.

With it on, each layer plays phrases. A phrase picks a length in beats (LENGTHS, by the weights),
then plays one shard from the same frame that many beats, again and again, `repeats` times, before
the next phrase picks a new length and shard. Settings, in Project.pace:

  on         bool      replaces the sequencer's cuts
  weights    [0..1]    one per LENGTHS: how likely each length is
  tilt       -1..1     the overall speed: -1 faster (shorter lengths), +1 slower
  repeats    1..16     how many times a phrase plays its shard
  intensity  0..1      how much intense music tilts to shorter lengths (and calm music to longer)
"""

from __future__ import annotations

LENGTHS = [0.25, 0.5, 1.0, 2.0, 4.0, 6.0, 8.0]
LABELS = ["1/4 beat", "1/2 beat", "1 beat", "2 beats", "4 beats", "6 beats", "8 beats"]


def default_pace() -> dict:
    return {"on": False, "weights": [0.0, 0.0, 1.0, 1.0, 1.0, 0.0, 0.0], "tilt": 0.0, "repeats": 4,
            "intensity": 0.5}


def settings(project) -> dict:
    out = default_pace()
    out.update(getattr(project, "pace", None) or {})
    w = list(out["weights"])[:len(LENGTHS)]
    out["weights"] = w + [0.0] * (len(LENGTHS) - len(w))
    return out


def shares(pace: dict, intensity: float = 0.5) -> list[float]:
    """The chance of each length (sums to 1) at this music intensity (0..1)."""
    # Positive leans to longer lengths: the speed setting, less when the music is intense.
    lean = float(pace["tilt"]) - float(pace["intensity"]) * (2.0 * intensity - 1.0)
    # Each doubling in length is 3x more (or less) likely at full lean.
    w = [max(0.0, float(x)) * 3.0 ** (lean * _octave(n)) for x, n in zip(pace["weights"], LENGTHS)]
    total = sum(w)
    if total <= 0.0:
        return [1.0 if n == 1.0 else 0.0 for n in LENGTHS]
    return [x / total for x in w]


def _octave(beats: float) -> float:
    import math

    return math.log2(beats)


def pick(pace: dict, intensity: float, r: float) -> float:
    """A length in beats, for a uniform r in [0, 1)."""
    for n, share in zip(LENGTHS, shares(pace, intensity)):
        r -= share
        if r < 0.0:
            return n
    return next(n for n, s in zip(reversed(LENGTHS), reversed(shares(pace, intensity))) if s > 0)
