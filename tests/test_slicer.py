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


def test_a_shard_is_written_a_frame_at_a_time(tmp_path):
    # A long, large shard: 120 frames of 640 x 360 (83 MB) must never be in memory all at once.
    import tracemalloc

    from shardloom.slicer import scores, write_shard

    def frames():
        rng = np.random.default_rng(5)
        base = rng.integers(0, 255, (360, 640, 3), dtype=np.uint8)
        for k in range(120):
            yield np.roll(base, 3 * k, axis=1)

    tracemalloc.start()
    n, motion, brightness = write_shard(tmp_path / "s.npy", frames(), 120, 360, 640)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert peak < 8_000_000                                  # a few frames, not 83 MB
    saved = np.load(tmp_path / "s.npy")
    want = np.stack(list(frames()))
    assert n == 120 and np.array_equal(saved, want)          # the same file np.save would write
    assert (motion, brightness) == pytest.approx(scores(want), rel=1e-6)


def test_a_short_shard_is_refused(tmp_path):
    from shardloom.slicer import write_shard

    with pytest.raises(ValueError):
        write_shard(tmp_path / "s.npy", iter([np.zeros((4, 4, 3), np.uint8)] * 3), 5, 4, 4)
    assert not (tmp_path / "s.npy").exists()


def test_frames_one_at_a_time_match_the_segment(videos):
    from shardloom.media import VideoDecoder

    with VideoDecoder(videos["ball"], 64, 36, 50.0) as d:   # faster than the source: frames repeat
        whole = d.segment(1.0, 0.7)
        one_by_one = list(d.iter_segment(1.0, 0.7))
        assert len(one_by_one) == len(whole) == d.segment_length(0.7)
        assert all(np.array_equal(a, b) for a, b in zip(one_by_one, whole))


def test_only_one_video_is_open_at_a_time(videos, tmp_path, monkeypatch):
    # Each open decoder holds its frames (about 100 MB at 1080p), so a pool from many videos opens one at a time.
    from shardloom import media

    live, most = set(), [0]
    real_init, real_close = media.VideoDecoder.__init__, media.VideoDecoder.close

    def init(self, *a, **k):
        real_init(self, *a, **k)
        live.add(id(self))
        most[0] = max(most[0], len(live))

    def close(self):
        live.discard(id(self))
        real_close(self)

    monkeypatch.setattr(media.VideoDecoder, "__init__", init)
    monkeypatch.setattr(media.VideoDecoder, "close", close)
    shards = slice_videos([videos["ball"], videos["still"], videos["ball"]], tmp_path / "pool", count=9,
                          width=32, height=18, seed=2)
    assert len({s.source for s in shards}) == 2 and len(shards) >= 6
    assert most[0] == 1 and not live
