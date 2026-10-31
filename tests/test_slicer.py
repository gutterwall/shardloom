import json
import random

import numpy as np
import pytest

from shardloom.slicer import PoolFrames, plan_cuts, slice_videos


def test_cuts_stay_inside_sources_and_do_not_overlap():
    # 13 s of usable video holds about 20 cuts of 0.25-1 s without overlap.
    cuts = plan_cuts([10.0, 3.0, 0.1], 15, 0.25, 1.0, random.Random(4))
    assert len(cuts) == 15
    by_source = {}
    for src, start, length in cuts:
        assert src in (0, 1), "a source shorter than the shortest shard is never cut"
        assert 0.25 <= length <= 1.0
        assert start >= 0 and start + length <= [10.0, 3.0][src] + 1e-6
        by_source.setdefault(src, []).append((start, start + length))
    for spans in by_source.values():
        spans.sort()
        assert all(a[1] <= b[0] + 1e-6 for a, b in zip(spans, spans[1:]))


def test_cuts_are_repeatable_with_a_seed():
    a = plan_cuts([30.0, 12.0], 20, 0.25, 1.5, random.Random(9))
    b = plan_cuts([30.0, 12.0], 20, 0.25, 1.5, random.Random(9))
    assert a == b


def test_cuts_stop_when_a_source_is_full():
    cuts = plan_cuts([1.0], 50, 0.4, 0.5, random.Random(1))
    assert 1 <= len(cuts) <= 2


def test_slice_videos_writes_a_pool(tmp_path, videos):
    shards = slice_videos([videos["ball"], videos["still"]], tmp_path / "pool", count=12, min_len=0.2,
                          max_len=0.6, width=160, height=90, fps=30, seed=2)
    assert len(shards) == 12
    data = json.loads((tmp_path / "pool" / "pool.json").read_text())
    assert (data["width"], data["height"], data["fps"]) == (160, 90, 30)
    pool = PoolFrames(tmp_path / "pool")
    for s in shards:
        frames = np.load(tmp_path / "pool" / s.file)
        assert frames.shape[1:] == (90, 160, 3) and frames.dtype == np.uint8
        assert len(frames) == s.frames == pytest.approx(s.duration * 30, abs=1)
        assert pool.frame(s.id, len(frames) + 1).shape == (90, 160, 3)
    # The moving ball scores more motion than a still colour; energy ranks 0..1.
    ball = [s for s in shards if s.source.endswith("ball.mp4")]
    still = [s for s in shards if s.source.endswith("still.mp4")]
    assert ball and still
    assert min(s.motion for s in ball) > max(s.motion for s in still)
    assert sorted(s.energy for s in shards)[0] == 0.0 and sorted(s.energy for s in shards)[-1] == 1.0


def test_append_adds_new_ids(tmp_path, videos):
    slice_videos([videos["ball"]], tmp_path / "pool", count=3, width=64, height=36, seed=1)
    shards = slice_videos([videos["still"]], tmp_path / "pool", count=2, width=64, height=36, seed=1, append=True)
    assert [s.id for s in shards] == [0, 1, 2, 3, 4]
    assert len({s.file for s in shards}) == 5
