import numpy as np
import pytest

from shardloom import fx as fxlib
from shardloom.analysis import analyse_file
from shardloom.export import export
from shardloom.media import probe
from shardloom.renderer import Renderer, headless_context
from shardloom.sequencer import Engine, FrameState, LaneState, Voice, starter_project
from shardloom.slicer import PoolFrames, slice_videos

W, H = 160, 90


@pytest.fixture(scope="module")
def ctx():
    try:
        c = headless_context()
    except Exception as e:  # no GPU / EGL on this machine
        pytest.skip(f"no headless OpenGL: {e}")
    yield c
    c.release()


class Frames:
    """Two shards: a left-to-right gradient and a solid colour."""

    width, height = W, H

    def __init__(self):
        x = np.linspace(0, 255, W, dtype=np.float32)
        grad = np.zeros((H, W, 3), np.uint8)
        grad[..., 0] = x
        grad[..., 1] = 255 - x
        grad[..., 2] = 128
        grad[: H // 3] //= 2  # a darker top band, so flips show
        self.data = {0: grad, 1: np.full((H, W, 3), (200, 40, 40), np.uint8), 2: np.full((H, W, 3), (40, 40, 200), np.uint8)}

    def frame(self, shard, index):
        return self.data[shard]


def state(lanes, master=None, t=1.0):
    return FrameState(t, 8, 0, lanes, master or {}, fxlib.PALETTES[0], {"beat_phase": 0.1})


def lane(shard, fx=None, blend="normal", opacity=1.0):
    return LaneState(Voice(0, 0, shard, 0.0, 10.0, 1.0, False, 0, {}), 0, fx or {}, opacity, blend)


def test_plain_lane_is_drawn_unchanged(ctx):
    r = Renderer(ctx, W, H, Frames())
    r.render(state([lane(0)]))
    out = r.read().astype(int)
    assert np.abs(out - Frames().data[0].astype(int)).max() <= 2


def test_every_effect_changes_the_picture(ctx):
    r = Renderer(ctx, W, H, Frames())
    r.render(state([lane(0)]))
    plain = r.read().astype(int)
    for name in fxlib.FX:
        r.render(state([lane(0, {name: 0.8})], t=1.37))
        changed = np.abs(r.read().astype(int) - plain).mean()
        assert changed > 1.0, f"{name} did nothing"


def test_master_only_effects(ctx):
    r = Renderer(ctx, W, H, Frames())
    r.render(state([lane(1)]))
    base = r.read().astype(int)
    r.render(state([lane(1)], {"flash": 1.0}))
    assert r.read().astype(int).mean() > base.mean() + 50
    r.render(state([lane(1)], {"strobe": 1.0}))
    assert r.read().max() == 0
    # Trails: a frame later, the previous picture still shows through.
    r.render(state([lane(1)]))
    r.render(state([lane(2)], {"trails": 1.0}))
    out = r.read()[H // 2, W // 2].astype(int)
    assert out[0] > 120 and out[2] > 150


@pytest.mark.parametrize("mode,expected", [
    ("normal", (40, 40, 200)),
    ("add", (240, 80, 240)),
    ("screen", (209, 74, 209)),
    ("multiply", (31, 6, 31)),
    ("difference", (160, 0, 160)),
])
def test_blend_modes(ctx, mode, expected):
    r = Renderer(ctx, W, H, Frames())
    r.render(state([lane(1), lane(2, blend=mode)]))
    assert np.abs(r.read()[H // 2, W // 2].astype(int) - expected).max() <= 2


def test_opacity_and_empty_lanes(ctx):
    r = Renderer(ctx, W, H, Frames())
    r.render(state([lane(1), lane(2, opacity=0.5)]))
    assert np.abs(r.read()[0, 0].astype(int) - (120, 40, 120)).max() <= 2
    r.render(state([LaneState(None, 0, {}, 1.0, "normal")]))
    assert r.read().max() == 0


def test_export_end_to_end(tmp_path, ctx, videos, song):
    slice_videos([videos["ball"], videos["still"]], tmp_path / "pool", count=8, width=W, height=H, seed=3)
    pool = PoolFrames(tmp_path / "pool")
    a = analyse_file(song)
    proj = starter_project(song, "pool")
    eng = Engine(proj, pool.shards, a, {s.id: pool.length(s.id) for s in pool.shards}, pool.fps)
    out = tmp_path / "out.mp4"
    info = export(eng, Renderer(ctx, W, H, pool), out, start=10.0, end=13.0, fps=24)
    assert info["frames"] == 72
    meta = probe(out)
    assert meta["duration"] == pytest.approx(3.0, abs=0.1)
    assert meta["audio"] and meta["video"]["width"] == W and meta["video"]["fps"] == pytest.approx(24)


def test_shards_cover_another_shape_without_stretching(ctx):
    # A 16:9 gradient drawn into a 9:16 output: only the middle of it shows, unstretched.
    r = Renderer(ctx, 46, 82, Frames())
    r.render(state([lane(0)]))
    out = r.read().astype(int)
    left, right = out[60, 0, 0], out[60, -1, 0]
    expected_span = 255 * (46 / 82) / (W / H)
    assert abs((right - left) - expected_span) < 12
    assert abs((left + right) / 2 - 127) < 10


def test_export_with_titles_and_cards(tmp_path, ctx, videos, song):
    from shardloom.compose import Program
    from shardloom.sequencer import Title

    slice_videos([videos["ball"], videos["still"]], tmp_path / "pool", count=6, width=W, height=H, seed=3)
    pool = PoolFrames(tmp_path / "pool")
    a = analyse_file(song)
    proj = starter_project(song, "pool")
    proj.titles = [Title("Hi", at="start", extra=True, duration=1.0, background="colour", colour="#00ff00", fade=0.0),
                   Title("End", at="end", extra=True, duration=0.5, fade=0.0)]
    proj.output.update(start=10.0, end=12.0, fade_in=0.0, fade_out=0.0)
    eng = Engine(proj, pool.shards, a, {s.id: pool.length(s.id) for s in pool.shards}, pool.fps)
    out = tmp_path / "titled.mp4"
    prog = Program(proj, a.duration)
    info = export(eng, Renderer(ctx, W, H, pool), out, fps=20, program=prog)
    assert info["frames"] == 70
    meta = probe(out)
    assert meta["duration"] == pytest.approx(3.5, abs=0.1) and meta["audio"]
    from shardloom.media import VideoDecoder

    with VideoDecoder(out, W, H, 20) as d:
        first = d.segment(0.2, 0.05)[0].astype(int)
        last = d.segment(3.3, 0.05)[0].astype(int)
    assert first[5, 5, 1] > 200 and first[5, 5, 0] < 60     # the green card
    assert last[5, 5].max() < 30                              # the closing card, on black
