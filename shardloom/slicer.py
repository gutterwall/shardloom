"""Cut raw videos into small random shards for the sequencer's pool.

Each shard is stored as frames (uint8, frames x height x width x 3) in
pool/NNNN.npy, described in pool/pool.json with its source, start, length and
two scores the sequencer can choose by: motion (mean frame-to-frame change)
and brightness.
"""

from __future__ import annotations

import json
import os
import random
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from .media import VideoDecoder

POOL_FILE = "pool.json"


@dataclass
class Shard:
    id: int
    file: str
    source: str
    start: float
    duration: float
    frames: int
    motion: float = 0.0
    brightness: float = 0.0
    # Rank of motion within the pool, 0 (calmest) .. 1 (busiest).
    energy: float = 0.0
    source_id: str = ""


def plan_cuts(durations: list[float], count: int, min_len: float, max_len: float, rng: random.Random,
              overlap: bool = False, weights: list[float] | None = None) -> list[tuple[int, float, float]]:
    """count random (source index, start, length) cuts, spread over the sources by duration (times weight).

    Without overlap, cuts of the same source do not overlap; when a source is
    too short for more, fewer cuts are returned.
    """
    if min_len <= 0 or max_len < min_len:
        raise ValueError("need 0 < min length <= max length")
    weights = weights or [1.0] * len(durations)
    eligible = [i for i, d in enumerate(durations) if d >= min_len and weights[i] > 0]
    if not eligible:
        return []
    taken: dict[int, list[tuple[float, float]]] = {i: [] for i in eligible}
    cuts = []
    attempts = 0
    while len(cuts) < count and attempts < count * 50:
        attempts += 1
        # A source is picked in proportion to its length.
        src = rng.choices(eligible, weights=[durations[i] * weights[i] for i in eligible])[0]
        dur = durations[src]
        length = rng.uniform(min_len, min(max_len, dur))
        start = rng.uniform(0, max(0.0, dur - length))
        if not overlap and any(start < e and start + length > s for s, e in taken[src]):
            continue
        taken[src].append((start, start + length))
        cuts.append((src, round(start, 3), round(length, 3)))
    return cuts


def scores(frames: np.ndarray) -> tuple[float, float]:
    """(motion, brightness) of a shard, both 0..1."""
    small = frames[:, ::8, ::8].astype(np.float32) / 255.0
    brightness = float(small.mean())
    motion = float(np.abs(np.diff(small, axis=0)).mean()) if len(small) > 1 else 0.0
    return motion, brightness


def slice_videos(sources: list[str | os.PathLike], pool_dir: str | os.PathLike, count: int = 48,
                 min_len: float = 0.25, max_len: float = 1.5, width: int = 640, height: int = 360,
                 fps: float = 30.0, seed: int | None = None, overlap: bool = False, append: bool = False,
                 progress=None) -> list[Shard]:
    """Cuts count random shards from the sources into pool_dir and writes pool.json."""
    pool_dir = Path(pool_dir)
    pool_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    existing = load_pool(pool_dir) if append and (pool_dir / POOL_FILE).exists() else []
    decoders = []
    try:
        for s in sources:
            decoders.append(VideoDecoder(s, width, height, fps))
        cuts = plan_cuts([d.duration for d in decoders], count, min_len, max_len, rng, overlap)
        shards = list(existing)
        next_id = max((s.id for s in existing), default=-1) + 1
        for n, (src, start, length) in enumerate(cuts):
            frames = decoders[src].segment(start, length)
            name = f"{next_id:04d}.npy"
            np.save(pool_dir / name, frames)
            motion, brightness = scores(frames)
            shards.append(Shard(next_id, name, str(Path(sources[src]).resolve()), start, length, len(frames),
                                motion, brightness))
            next_id += 1
            if progress:
                progress(n + 1, len(cuts))
    finally:
        for d in decoders:
            d.close()
    rank_energy(shards)
    save_pool(pool_dir, shards, width, height, fps)
    return shards


def rank_energy(shards: list[Shard]) -> None:
    order = sorted(range(len(shards)), key=lambda i: shards[i].motion)
    for rank, i in enumerate(order):
        shards[i].energy = rank / max(1, len(shards) - 1)


def save_pool(pool_dir: Path, shards: list[Shard], width: int, height: int, fps: float) -> None:
    data = {"width": width, "height": height, "fps": fps, "shards": [asdict(s) for s in shards]}
    tmp = pool_dir / (POOL_FILE + ".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(pool_dir / POOL_FILE)


def load_pool(pool_dir: str | os.PathLike) -> list[Shard]:
    data = json.loads((Path(pool_dir) / POOL_FILE).read_text())
    known = Shard.__dataclass_fields__
    return [Shard(**{k: v for k, v in s.items() if k in known}) for s in data["shards"]]


def pool_format(pool_dir: str | os.PathLike) -> dict:
    data = json.loads((Path(pool_dir) / POOL_FILE).read_text())
    return {"width": data["width"], "height": data["height"], "fps": data["fps"]}


class PoolFrames:
    """Shard frames, memory-mapped from disk so a large pool loads instantly."""

    def __init__(self, pool_dir: str | os.PathLike):
        self.dir = Path(pool_dir)
        self.shards = load_pool(self.dir)
        fmt = pool_format(self.dir)
        self.width, self.height, self.fps = fmt["width"], fmt["height"], fmt["fps"]
        self._frames = {s.id: np.load(self.dir / s.file, mmap_mode="r") for s in self.shards}
        self.by_id = {s.id: s for s in self.shards}

    def frame(self, shard_id: int, index: int) -> np.ndarray:
        frames = self._frames[shard_id]
        return frames[index % len(frames)]

    def length(self, shard_id: int) -> int:
        return len(self._frames[shard_id])
