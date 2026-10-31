"""The project and the sequencer engine.

Time runs on the music's beats: step g starts at beat g / steps_per_beat of
the analysis (tracked beats or a fixed grid). Every random decision is drawn
from the seed and the step number, so the live view and an export of the same
project show exactly the same picture.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import fx as fxlib

CHOICES = ["random", "bag", "no_repeat", "intensity"]
PROJECT_FILE = "project.json"


# ---- project model -------------------------------------------------------

@dataclass
class Step:
    on: bool = False
    prob: float = 1.0
    # A fixed shard id, or None to let the lane choose.
    shard: int | None = None
    gate: float = 1.0          # length in steps
    speed: float = 1.0
    reverse: bool = False
    jitter: float = 1.0        # 0: start at the shard's first frame; 1: anywhere in it
    fx: dict[str, float] = field(default_factory=dict)


@dataclass
class Lane:
    name: str = "Lane"
    blend: str = "normal"
    opacity: float = 1.0
    choice: str = "bag"
    # Shards this lane draws from: None for the whole pool.
    shards: list[int] | None = None
    fx: dict[str, float] = field(default_factory=dict)
    mute: bool = False


@dataclass
class Pattern:
    name: str = "A"
    length: int = 16
    steps: list[list[Step]] = field(default_factory=list)   # [lane][step]


@dataclass
class Modulation:
    source: str = "bass"
    target: str = "master.zoom"    # master.<fx>, lane<N>.<fx>, or all.<fx>
    amount: float = 0.3


@dataclass
class Project:
    music: str = ""
    pool: str = "pool"
    seed: int = 1
    steps_per_beat: int = 4
    width: int = 640
    height: int = 360
    fps: float = 30.0
    # Fixed tempo instead of the tracked beats: {"bpm": 120, "offset": 0.0}, or None.
    grid: dict | None = None
    tempo_scale: float = 1.0       # 2 for double time, 0.5 for half time
    lanes: list[Lane] = field(default_factory=list)
    patterns: list[Pattern] = field(default_factory=list)
    # Arrangement: [{"pattern": 0, "repeats": 2}, ...]; empty plays the live pattern.
    song: list[dict] = field(default_factory=list)
    master_fx: dict[str, float] = field(default_factory=dict)
    modulations: list[Modulation] = field(default_factory=list)
    palettes: list[list[str]] = field(default_factory=lambda: [list(p) for p in fxlib.PALETTES])
    # "intensity": change palette when the section gets louder or quieter; "bars": every palette_bars bars.
    palette_mode: str = "intensity"
    palette_bars: int = 8
    # Recorded live moves: [{"step": g, "type": "pattern", "value": 1}, {"t": s, "type": "punch", "fx": "invert", "on": true}, ...]
    performance: list[dict] = field(default_factory=list)

    # ---- files ---------------------------------------------------------

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        d = dict(d)
        lanes = [Lane(**l) for l in d.pop("lanes", [])]
        patterns = []
        for p in d.pop("patterns", []):
            p = dict(p)
            steps = [[Step(**s) for s in lane] for lane in p.pop("steps", [])]
            patterns.append(Pattern(steps=steps, **p))
        mods = [Modulation(**m) for m in d.pop("modulations", [])]
        known = {f for f in cls.__dataclass_fields__}
        proj = cls(lanes=lanes, patterns=patterns, modulations=mods, **{k: v for k, v in d.items() if k in known})
        proj.normalise()
        return proj

    def save(self, directory: str | os.PathLike) -> None:
        path = Path(directory) / PROJECT_FILE
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=1))
        tmp.replace(path)

    @classmethod
    def load(cls, directory: str | os.PathLike) -> "Project":
        return cls.from_dict(json.loads((Path(directory) / PROJECT_FILE).read_text()))

    def normalise(self) -> None:
        """Every pattern has a step row of its length for every lane."""
        for p in self.patterns:
            while len(p.steps) < len(self.lanes):
                p.steps.append([])
            del p.steps[len(self.lanes):]
            for row in p.steps:
                while len(row) < p.length:
                    row.append(Step())
                del row[p.length:]


def starter_project(music: str, pool: str, lanes: int = 3, seed: int = 1) -> Project:
    """A project that does something on first play: a busy base lane, sparse overlays, beat-reactive master."""
    names = ["Base", "Hits", "Texture", "Accents"]
    blends = ["normal", "screen", "difference", "add"]
    proj = Project(music=music, pool=pool, seed=seed)
    proj.lanes = [Lane(names[i % 4], blends[i % 4], 1.0 if i == 0 else 0.8, "bag" if i == 0 else "intensity")
                  for i in range(lanes)]
    a = Pattern("A", 16)
    b = Pattern("B", 16)
    for pat, dense in ((a, False), (b, True)):
        for li in range(lanes):
            row = []
            for s in range(16):
                if li == 0:
                    # Base: a cut on every beat, a chance of an extra cut between.
                    step = Step(on=s % 4 == 0 or (dense and s % 2 == 0), prob=1.0 if s % 4 == 0 else 0.5, gate=4 if s % 4 == 0 else 2)
                elif li == 1:
                    step = Step(on=s in (4, 12) or (dense and s in (6, 14, 15)), prob=0.8, gate=1.5,
                                fx={"rgb_split": 0.4} if s == 12 else {})
                else:
                    step = Step(on=s % 8 == 2 or (dense and s % 4 == 2), prob=0.6, gate=3, speed=0.5,
                                fx={"kaleido": 0.5} if s == 10 else {"glitch": 0.3})
                row.append(step)
            pat.steps.append(row)
    proj.patterns = [a, b]
    proj.song = [{"pattern": 0, "repeats": 4}, {"pattern": 1, "repeats": 4}]
    proj.master_fx = {"colorize": 0.25}
    proj.modulations = [
        Modulation("bass", "master.zoom", 0.25),
        Modulation("beat", "master.flash", 0.35),
        Modulation("intensity", "master.colorize", 0.5),
        Modulation("high", "all.rgb_split", 0.2),
        Modulation("onset", "master.shake", 0.15),
    ]
    proj.normalise()
    return proj


# ---- deterministic randomness -------------------------------------------

_MASK = (1 << 64) - 1


def _mix(x: int) -> int:
    """splitmix64 finaliser."""
    x = (x + 0x9E3779B97F4A7C15) & _MASK
    x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & _MASK
    x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & _MASK
    return x ^ (x >> 31)


def rand(*keys: int) -> float:
    """A uniform number in [0, 1) that depends only on the keys."""
    h = 0
    for k in keys:
        h = _mix(h ^ (int(k) & _MASK))
    return (h >> 11) / float(1 << 53)


# ---- engine --------------------------------------------------------------

@dataclass
class Voice:
    lane: int
    step: int          # global step it started on
    shard: int
    t0: float
    t1: float
    speed: float
    reverse: bool
    start: int         # first frame
    fx: dict[str, float]


@dataclass
class LaneState:
    voice: Voice | None
    frame: int
    fx: dict[str, float]
    opacity: float
    blend: str


@dataclass
class FrameState:
    t: float
    step: int
    pattern: int
    lanes: list[LaneState]
    master: dict[str, float]
    palette: list[str]
    music: dict[str, float]


class Engine:
    def __init__(self, project: Project, shards: list, analysis, shard_lengths: dict[int, int] | None = None,
                 pool_fps: float = 30.0):
        self.project = project
        self.shards = {s.id: s for s in shards}
        self.lengths = shard_lengths or {s.id: s.frames for s in shards}
        self.pool_fps = pool_fps
        self.base_analysis = analysis
        self.live_pattern = 0
        self.queued_pattern: int | None = None
        # Play the song arrangement (if there is one) rather than the live pattern.
        self.song_mode = bool(project.song)
        self.fill = False
        self.punch: dict[str, float] = {}
        self.recording = False
        self.retime()

    # ---- clock ---------------------------------------------------------

    def retime(self) -> None:
        """Rebuilds the clock and the palette schedule after the grid or tempo changed."""
        a = self.base_analysis
        if self.project.grid:
            a = a.with_grid(float(self.project.grid["bpm"]), float(self.project.grid.get("offset", 0.0)))
        self.analysis = a
        # (palette index, intensity level) of each bar from the start.
        self._palettes: list[tuple[int, int]] = []
        self.reset()

    def reset(self) -> None:
        self._done = -1               # last processed global step
        self._voices: list[list[Voice]] = [[] for _ in self.project.lanes]
        self._bags: dict[int, list[int]] = {}
        self._bag_round: dict[int, int] = {}
        self._last: dict[int, int] = {}
        self._pattern_at: dict[int, int] = {}
        self._live_pattern_run = self.live_pattern

    def beats(self, t: float) -> float:
        return self.analysis.beat_position(t) * self.project.tempo_scale

    def time_of_step(self, g: float) -> float:
        return self.analysis.time_at_beat(g / self.project.steps_per_beat / self.project.tempo_scale)

    def step_at(self, t: float) -> int:
        return int(math.floor(self.beats(t) * self.project.steps_per_beat + 1e-9))

    # ---- arrangement ---------------------------------------------------

    def _recorded_pattern(self, g: int) -> int | None:
        value = None
        for ev in self.project.performance:
            if ev.get("type") == "pattern" and ev.get("step", 0) <= g:
                value = int(ev["value"])
        return value

    def pattern_for_step(self, g: int) -> tuple[int, int]:
        """(pattern index, step within the pattern) for global step g."""
        p = self.project
        if not p.patterns:
            return 0, 0
        if p.song and not self._live():
            total = sum(p.patterns[e["pattern"]].length * max(1, int(e.get("repeats", 1))) for e in p.song)
            pos = g % total if total else 0
            for e in p.song:
                span = p.patterns[e["pattern"]].length * max(1, int(e.get("repeats", 1)))
                if pos < span:
                    return e["pattern"], pos % p.patterns[e["pattern"]].length
                pos -= span
        idx = self._pattern_at.get(g)
        if idx is None:
            idx = self._recorded_pattern(g) if self.project.performance and not self.recording else None
            idx = self._live_pattern_run if idx is None else idx
        idx = min(idx, len(p.patterns) - 1)
        return idx, g % p.patterns[idx].length

    def _live(self) -> bool:
        """Whether the live (or recorded) pattern plays instead of the song."""
        return not self.song_mode or any(e.get("type") == "pattern" for e in self.project.performance)

    def queue_pattern(self, index: int) -> None:
        """Switches pattern at the end of the current one (recorded when recording); leaves song mode."""
        if self.song_mode:
            g = max(self._done, 0)
            self._live_pattern_run = self.live_pattern = self.pattern_for_step(g)[0]
            self.song_mode = False
        self.queued_pattern = index

    # ---- triggering ----------------------------------------------------

    def _candidates(self, lane: Lane) -> list[int]:
        ids = [i for i in (lane.shards if lane.shards is not None else self.shards) if i in self.shards]
        return ids or list(self.shards)

    def _choose(self, li: int, lane: Lane, step: Step, g: int, t: float) -> int:
        seed = self.project.seed
        if step.shard is not None and step.shard in self.shards:
            return step.shard
        ids = self._candidates(lane)
        if len(ids) == 1:
            return ids[0]
        r = rand(seed, g, li, 7)
        if lane.choice == "bag":
            bag = self._bags.get(li)
            if not bag:
                rnd = self._bag_round.get(li, 0)
                self._bag_round[li] = rnd + 1
                bag = sorted(ids, key=lambda i: rand(seed, li, rnd, i, 11))
                # A new round never starts with the shard that ended the last one.
                if len(bag) > 1 and bag[-1] == self._last.get(li):
                    bag[0], bag[-1] = bag[-1], bag[0]
                self._bags[li] = bag
            return bag.pop()
        if lane.choice == "no_repeat":
            last = self._last.get(li)
            pool = [i for i in ids if i != last] or ids
            return pool[int(r * len(pool))]
        if lane.choice == "intensity":
            target = self.analysis.at(t).get("intensity", 0.5)
            weights = [math.exp(-((getattr(self.shards[i], "energy", 0.5) - target) / 0.2) ** 2) + 1e-6 for i in ids]
            pick = r * sum(weights)
            for i, w in zip(ids, weights):
                pick -= w
                if pick <= 0:
                    return i
            return ids[-1]
        return ids[int(r * len(ids))]

    def _trigger(self, g: int) -> None:
        p = self.project
        if self.queued_pattern is not None:
            pat_now, pos = self.pattern_for_step(g)
            if pos == 0:
                self._live_pattern_run = self.queued_pattern
                self.live_pattern = self.queued_pattern
                if self.recording:
                    p.performance = [e for e in p.performance if not (e.get("type") == "pattern" and e.get("step", 0) >= g)]
                    p.performance.append({"step": g, "type": "pattern", "value": self.queued_pattern})
                self.queued_pattern = None
        pi, si = self.pattern_for_step(g)
        self._pattern_at[g] = pi
        if not p.patterns:
            return
        pattern = p.patterns[pi]
        t = self.time_of_step(g)
        for li, lane in enumerate(p.lanes):
            if li >= len(pattern.steps):
                continue
            step = pattern.steps[li][si]
            if not step.on:
                continue
            prob = 1.0 if self.fill else step.prob
            if rand(p.seed, g, li, 3) >= prob:
                continue
            shard = self._choose(li, lane, step, g, t)
            self._last[li] = shard
            length = self.lengths.get(shard, 1)
            start = int(rand(p.seed, g, li, 5) * step.jitter * length) % max(1, length)
            if step.reverse and step.jitter == 0:
                start = length - 1
            voice = Voice(li, g, shard, t, self.time_of_step(g + max(0.05, step.gate)), step.speed, step.reverse,
                          start, dict(step.fx))
            voices = self._voices[li]
            voices.append(voice)
            # Only the newest few can still be sounding.
            del voices[:-4]

    def advance(self, t: float) -> int:
        g = self.step_at(t)
        if g < self._done:
            # Went back in time: replay from the start so choices stay identical.
            self.reset()
        start = max(self._done + 1, min(g, self.step_at(0.0)) if self._done < 0 else self._done + 1)
        for s in range(start, g + 1):
            self._trigger(s)
        self._done = max(self._done, g)
        return g

    # ---- the picture at time t -----------------------------------------

    def palette_index(self, bar: int) -> int:
        p = self.project
        count = max(1, len(p.palettes))
        if p.palette_mode == "bars":
            return (bar // max(1, p.palette_bars)) % count
        if p.palette_mode != "intensity":
            return 0
        # Advance one palette each time the section's intensity level (low / mid / high) changes.
        seq = self._palettes
        while len(seq) <= bar:
            b = len(seq)
            level = self._palette_level(b)
            idx = 0 if b == 0 else (seq[-1][0] + (level != seq[-1][1])) % count
            seq.append((idx, level))
        return seq[max(0, bar)][0]

    def _palette_level(self, bar: int) -> int:
        t = self.time_of_step(bar * 4 * self.project.steps_per_beat)
        v = self.analysis.at(max(0.0, t)).get("intensity", 0.0)
        return 0 if v < 0.33 else 1 if v < 0.66 else 2

    def punch_at(self, t: float) -> dict[str, float]:
        """Live punch-ins, or recorded ones when playing back a performance."""
        if self.recording or not any(e.get("type") == "punch" for e in self.project.performance):
            return dict(self.punch)
        held: dict[str, float] = {}
        for ev in self.project.performance:
            if ev.get("type") == "punch" and ev.get("t", 0.0) <= t:
                if ev.get("on"):
                    held[ev["fx"]] = float(ev.get("amount", 1.0))
                else:
                    held.pop(ev["fx"], None)
        return held

    def record_punch(self, t: float, name: str, on: bool, amount: float = 1.0) -> None:
        if on:
            self.punch[name] = amount
        else:
            self.punch.pop(name, None)
        if self.recording:
            self.project.performance.append({"t": round(t, 4), "type": "punch", "fx": name, "on": on, "amount": amount})

    def state(self, t: float) -> FrameState:
        g = self.advance(t)
        p = self.project
        music = self.analysis.at(t)
        mods: dict[str, dict[str, float]] = {}
        for m in p.modulations:
            scope, _, name = m.target.partition(".")
            mods.setdefault(scope, {})
            mods[scope][name] = mods[scope].get(name, 0.0) + m.amount * music.get(m.source, 0.0)
        punch = self.punch_at(t)
        lanes = []
        for li, lane in enumerate(p.lanes):
            voice = None
            for v in reversed(self._voices[li]):
                if v.t0 <= t < v.t1:
                    voice = v
                    break
            frame = 0
            if voice:
                length = self.lengths.get(voice.shard, 1)
                n = int((t - voice.t0) * self.pool_fps * voice.speed)
                frame = (voice.start - n if voice.reverse else voice.start + n) % max(1, length)
            lane_fx = fxlib.add(lane.fx, voice.fx if voice else {}, mods.get("all"), mods.get(f"lane{li}"))
            lanes.append(LaneState(None if lane.mute else voice, frame, lane_fx, lane.opacity, lane.blend))
        master = fxlib.add(p.master_fx, mods.get("master"), punch)
        bar = int(math.floor(max(0.0, self.beats(t)) / 4))
        palette = p.palettes[self.palette_index(bar) % len(p.palettes)] if p.palettes else fxlib.PALETTES[0]
        pi, _ = self.pattern_for_step(g)
        return FrameState(t, g, pi, lanes, master, palette, music)
