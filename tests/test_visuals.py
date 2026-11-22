"""MilkDrop-style visuals (visuals.py): when they strike, what they pick, and how they are drawn into the layers."""

import math

import numpy as np
import pytest

from shardloom import events, visuals
from shardloom.analysis import analyse_samples
from shardloom.renderer import Renderer
from shardloom.sequencer import Engine, FrameState, Lane, LaneState, Pattern, Project, Step, Voice
from shardloom.slicer import Shard

from conftest import click_track
from test_render import Frames, H, W, ctx  # noqa: F401  (the fixture)

ANALYSIS = analyse_samples(click_track(120, 30.0)).with_grid(120.0, 0.37)


def engine(setting=None, lanes=2, seed=4, muted=()):
    p = Project(seed=seed)
    p.lanes = [Lane(f"L{i}", mute=i in muted) for i in range(lanes)]
    p.patterns = [Pattern("A", 16, [[Step(on=True) for _ in range(16)] for _ in range(lanes)])]
    p.shard_fx = {"visuals": {"rate": 1.0, "strength": 1.0, **(setting or {})}}
    return Engine(p, [Shard(i, "", "", 0, 1, 10) for i in range(3)], ANALYSIS)


def starts(e, seconds=30.0, fps=24):
    out, last = [], None
    for i in range(int(seconds * fps)):
        v = e.state(i / fps).visual
        if v and v["seed"] != last:
            out.append((v["t0"], v))
        last = v["seed"] if v else None
    return out


def music(n=150):
    return [(0.5 + 0.5 * math.sin(k / 7), 0.5 + 0.4 * math.sin(k / 11), 0.5 + 0.5 * math.sin(k / 5),
             1.0 if k % 15 == 0 else 0.1, math.exp(-(k % 15) / 3)) for k in range(n)]


def visual(preset="nebula", blend="add", after=0, steps=120, amount=1.0, seed=0.37):
    return {"preset": preset, "blend": blend, "after": after, "amount": amount, "seed": seed, "t0": 0.0,
            "steps": steps, "music": music(steps + 1)}


def picture(r, lanes, v, t=5.0):
    r.render(FrameState(t, 0, 0, lanes, {}, ["#000000", "#808080", "#ffffff"], {}, {}, v))
    return r.read().astype(int)


def layer(shard, opacity=1.0, blend="normal"):
    return LaneState(Voice(0, 0, shard, 0.0, 99.0, 1.0, False, 0, {}), 0, {}, opacity, blend)


# ---- when they strike and what they pick -----------------------------------------------------------------

def test_off_by_default_and_added_after_every_other_effect():
    assert events.NAMES[-1] == "visuals" and events.EVENTS["visuals"].group == "Visuals"
    assert "visuals" not in events.PICTURE and len(events.PICTURE) == 17   # the shader's arrays are unchanged
    e = engine()
    e.project.shard_fx = {}
    assert all(e.state(i / 10).visual is None for i in range(200))


def test_visuals_take_turns():
    got = starts(engine())
    assert len(got) >= 3
    t0s = [t for t, _ in got]
    assert all(b - a >= events.EVENTS["visuals"].seconds - 1e-6 for a, b in zip(t0s, t0s[1:]))
    # At full rate the next starts soon after the last ends; at a low rate, later.
    gaps = [b - a for a, b in zip(t0s, t0s[1:])]
    assert np.mean(gaps) < 10.0
    low = [t for t, _ in starts(engine({"rate": 0.15}), 60.0)]
    assert len(low) < len(got) * 2 and np.mean(np.diff(low)) > np.mean(gaps)


def test_the_same_from_any_start():
    whole = engine()
    states = {round(i / 24, 6): whole.state(i / 24).visual for i in range(24 * 20)}
    for t in (3.1, 9.7, 17.25, 12.0):
        fresh = engine().state(t).visual
        want = states[round(round(t * 24) / 24, 6)] if round(t * 24) / 24 == t else whole.state(t).visual
        assert (fresh is None) == (want is None)
        if fresh:
            for k in ("preset", "blend", "after", "seed", "steps"):
                assert fresh[k] == want[k], (t, k)
            assert fresh["t0"] == want["t0"]
            assert np.allclose(np.array(fresh["music"]), np.array(want["music"]), atol=1e-9)


def test_picks_only_the_enabled_presets_and_blends():
    seen = {(v["preset"], v["blend"]) for _, v in starts(engine({"presets": ["tunnel", "scope"],
                                                                  "blends": ["subtract", "overlay"]}), 120.0)}
    assert seen and {p for p, _ in seen} <= {"tunnel", "scope"} and {b for _, b in seen} <= {"subtract", "overlay"}
    every = {(v["preset"], v["blend"]) for _, v in starts(engine({"presets": [], "blends": ["nonsense"]}), 120.0)}
    assert len({p for p, _ in every}) > 3 and len({b for _, b in every}) > 3   # nothing usable: all of them


def test_goes_over_a_playing_layer_and_behind_goes_under_everything():
    got = [v for _, v in starts(engine(lanes=4, muted=(1,)), 200.0)]
    assert {v["after"] for v in got if v["blend"] != "behind"} <= {0, 2, 3}
    assert len({v["after"] for v in got if v["blend"] != "behind"}) > 1
    assert all(v["after"] == 3 for v in got if v["blend"] == "behind")


def test_each_step_hears_the_music_at_its_time():
    e = engine()
    for i in range(24 * 12):
        v = e.state(i / 24).visual
        if v and v["steps"] > 30:
            break
    assert len(v["music"]) == v["steps"] + 1
    for k in (0, 7, v["steps"]):
        assert v["music"][k] == pytest.approx(visuals.music_at(e.analysis, v["t0"] + k / visuals.RATE), abs=1e-6)
    assert len({m[0] for m in v["music"]}) > 3          # the bass moves


def test_the_preview_and_the_render_step_the_same_picture():
    assert visuals.feed_size(1920, 1080) == visuals.feed_size(640, 360) == (640, 360)
    assert visuals.feed_size(1080, 1920)[1] == visuals.FEED_HEIGHT
    assert len(visuals.pack("solar")) == len(visuals.PARAMS) == 24


# ---- drawing ---------------------------------------------------------------------------------------------

def test_every_preset_draws_its_own_picture(ctx):  # noqa: F811
    r = Renderer(ctx, W, H, Frames())
    hidden = [layer(0, opacity=0.0)]
    pics = {}
    for key in visuals.PRESETS:
        r._visual_key = None
        pics[key] = picture(r, hidden, visual(key))
        assert pics[key].mean() > 8, key
    keys = list(pics)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            assert np.abs(pics[a] - pics[b]).mean() > 3, (a, b)


def test_each_blend_mixes_it_in_its_own_way(ctx):  # noqa: F811
    r = Renderer(ctx, W, H, Frames())
    lanes = [layer(0)]
    plain = picture(r, lanes, None)
    out = {b: picture(r, lanes, visual(blend=b)) for b in visuals.BLENDS}
    for b, img in out.items():
        # Behind only shows through the test picture's darkest parts (see the next test).
        assert np.abs(img - plain).mean() > (0.5 if b == "behind" else 1.5), b
    lit = out["add"].sum() - plain.sum()
    assert lit > 0 and out["subtract"].sum() < plain.sum() and out["multiply"].sum() < plain.sum()
    names = list(out)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            assert np.abs(out[a] - out[b]).mean() > 0.5, (a, b)
    assert np.array_equal(picture(r, lanes, visual(amount=0.0)), plain)


def test_behind_shows_only_where_the_picture_is_dark(ctx):  # noqa: F811
    r = Renderer(ctx, W, H, Frames())
    bright = [layer(1)]                       # solid red, bright enough to hide it
    assert np.abs(picture(r, bright, visual(blend="behind")) - picture(r, bright, None)).mean() < 2
    dark = [layer(0, opacity=0.0)]            # nothing playing: black
    assert picture(r, dark, visual(blend="behind")).mean() > 8


def test_it_goes_into_the_stack_where_it_is_put(ctx):  # noqa: F811
    r = Renderer(ctx, W, H, Frames())
    lanes = [layer(0), layer(2)]              # a solid blue layer covers everything under it
    top = picture(r, lanes, None)
    assert np.abs(picture(r, lanes, visual(blend="occlude", after=0)) - top).max() <= 2   # under the blue: hidden
    assert np.abs(picture(r, lanes, visual(blend="occlude", after=1)) - top).mean() > 5   # over it: seen


def test_jumping_to_a_frame_draws_what_playing_to_it_draws(ctx):  # noqa: F811
    played = Renderer(ctx, W, H, Frames())
    e = engine()
    want = {}
    for i in range(24 * 10):
        st = e.state(i / 24)
        played.render(st)
        if st.visual and st.visual["steps"] > 40:
            want[i] = played.read().astype(int)
    assert want
    for i in sorted(want)[::max(1, len(want) // 3)]:
        r = Renderer(ctx, W, H, Frames())
        st = engine().state(i / 24)
        r.render(st)
        r.render(st)               # the master pass reads the frame before; draw it twice so it matches
        assert np.array_equal(r.read().astype(int), want[i]), i
    # Back to an earlier frame of the same strike: started again from its beginning, the same picture.
    i = sorted(want)[-1]
    st = e.state(i / 24)
    played.render(e.state(i / 24 - 0.5))
    played.render(st)
    played.render(st)
    assert np.array_equal(played.read().astype(int), want[i])
