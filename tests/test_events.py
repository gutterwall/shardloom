import math

import numpy as np
import pytest

from shardloom import events
from shardloom.analysis import analyse_samples
from shardloom.slicer import Shard
from shardloom.sequencer import Engine, FrameState, LaneState, Lane, Pattern, Project, Step, Voice

from conftest import click_track
from test_render import Frames, H, W, ctx  # noqa: F401  (the fixture)


def engine(shard_fx, seed=3):
    p = Project(seed=seed)
    p.lanes = [Lane("L")]
    p.patterns = [Pattern("A", 16, [[Step(on=True) for _ in range(16)]])]
    p.shard_fx = shard_fx
    shards = [Shard(i, "", "", 0, 1, 10) for i in range(4)]
    return Engine(p, shards, analyse_samples(click_track(120, 20.0)).with_grid(120.0, 0.37))


def strikes(e, name, seconds=16.0, fps=30):
    """Start times of the strikes of one effect."""
    starts, last = [], None
    for i in range(int(seconds * fps)):
        hit = e.state(i / fps).events.get(name)
        if hit and (last is None or hit[1] < last):
            starts.append(i / fps - hit[1] * events.EVENTS[name].seconds)
        last = hit[1] if hit else None
    return starts


def test_off_by_default_and_when_strength_is_zero():
    assert engine({}).state(3.0).events == {}
    assert engine({"tear": {"rate": 1.0, "strength": 0.0}}).state(3.0).events == {}


def test_rate_sets_how_often():
    few = strikes(engine({"tear": {"rate": 0.2}}), "tear")
    many = strikes(engine({"tear": {"rate": 0.9}}), "tear")
    assert len(many) > 2 * max(1, len(few)) and len(many) > 8


def test_the_same_every_time_and_from_any_start():
    fx = {"fireworks": {"rate": 0.6}, "tear": {"rate": 0.6, "sync": 1.0}}
    times = [7.3, 2.0, 11.9, 0.4, 7.3, 9.95]
    jumping = engine(fx)
    fresh = [engine(fx).state(t).events for t in times]        # each from a new engine, like a render from t
    assert [jumping.state(t).events for t in times] == fresh
    assert any(fresh)
    assert [engine(fx, seed=4).state(t).events for t in times] != fresh


def test_pulse_sync_puts_strikes_on_or_between_the_beats():
    def on_beat_share(sync):
        e = engine({"corrupt": {"rate": 0.8, "sync": sync}})
        starts = strikes(e, "corrupt", 20.0)
        beats = [abs((t - 0.37) * 2 - round((t - 0.37) * 2)) < 0.02 for t in starts]   # a beat every 0.5 s from 0.37
        return sum(beats) / max(1, len(beats)), len(starts)
    on, n_on = on_beat_share(1.0)
    off, n_off = on_beat_share(-1.0)
    assert n_on > 5 and n_off > 5
    assert on > 0.8 and off < 0.2


def test_chance_and_envelopes():
    assert events.chance(0.0, 0.0, 1.0) == 0.0
    assert events.chance(1.0, 1.0, 0.0) == 0.0 and events.chance(1.0, -1.0, 1.0) == 0.0
    assert events.chance(1.0, 1.0, 1.0) == pytest.approx(2 * events.chance(1.0, 0.0, 1.0))
    for kind in ("spike", "swell", "flat"):
        assert events.envelope(kind, 1.0) == 0.0 and 0.0 < events.envelope(kind, 0.3) <= 1.0


def test_try_sets_one_off_now():
    e = engine({})
    e.events.fire("explosion", 5.0)
    assert "explosion" in e.state(5.5).events and e.state(4.9).events == {}
    assert e.state(5.0 + events.EVENTS["explosion"].seconds + 0.01).events == {}


def test_every_effect_changes_the_picture(ctx):  # noqa: F811
    from shardloom.renderer import Renderer

    r = Renderer(ctx, W, H, Frames())
    voice = Voice(0, 0, 0, 0.0, 99.0, 1.0, False, 0, {})

    def picture(hits, t=0.5):
        st = FrameState(t, 0, 0, [LaneState(voice, 0, {}, 1.0, "normal")], {}, ["#000000", "#808080", "#ffffff"], {}, hits)
        r.render(st)
        r.render(st)
        return r.read().astype(int)

    plain = picture({})
    for name in events.PICTURE:
        img = picture({name: (1.0, 0.4, 0.37)})
        assert np.abs(img - plain).mean() > 2.0, name
    assert np.array_equal(picture({"tear": (0.0, 0.4, 0.37)}), plain)   # struck with no strength: untouched


def test_colour_effects_follow_the_music_and_join_up():
    e = engine({"hue_shift": {"rate": 1.0, "strength": 1.0}, "brightness": {"rate": 1.0, "strength": 1.0}})
    amounts, levels = [], []
    for i in range(1, 300):
        t = i / 30
        ev = e.state(t).events
        if e.analysis.at(t)["intensity"] > 0.02:
            assert "hue_shift" in ev                               # at full rate, on all the time
        amounts.append(ev.get("brightness", (0.0,))[0])
        levels.append(e.analysis.at(t)["level"])
        assert 0.0 <= ev.get("hue_shift", (0.0,))[0] <= 1.0
    amounts, levels = np.array(amounts), np.array(levels)
    assert np.corrcoef(amounts, levels)[0, 1] > 0.9                # louder: brighter
    assert amounts.min() < 0.0 < amounts.max()                     # quiet parts go darker
    seeds = {hit[2] for t in np.arange(0.5, 10.0, 0.5) if (hit := e.state(t).events.get("hue_shift"))}
    assert len(seeds) == 1                                         # one direction round the wheel all song


def test_negative_brightness_darkens(ctx):  # noqa: F811
    from shardloom.renderer import Renderer

    r = Renderer(ctx, W, H, Frames())
    voice = Voice(0, 0, 0, 0.0, 99.0, 1.0, False, 0, {})

    def mean(hits):
        st = FrameState(0.5, 0, 0, [LaneState(voice, 0, {}, 1.0, "normal")], {}, ["#000000", "#808080", "#ffffff"], {}, hits)
        r.render(st)
        return r.read().mean()

    plain = mean({})
    assert mean({"brightness": (-0.8, 0.1, 0.5)}) < plain * 0.6 < plain < mean({"brightness": (0.8, 0.1, 0.5)})


# ---- One layer effects: the colour lens and the mirror ------------------------------------------------------

def layered(shard_fx, lanes=3, seed=3):
    e = engine(shard_fx, seed)
    e.project.lanes = [Lane(f"L{i}") for i in range(lanes)]
    e.project.patterns = [Pattern("A", 16, [[Step(on=True) for _ in range(16)] for _ in range(lanes)])]
    e.reset()
    return e


def struck(e, name, t):
    """The layers a One layer effect is on at t, and their shader settings."""
    key = "lens" if name == "lens" else "fold"
    return {li: lane.strike for li, lane in enumerate(e.state(t).lanes) if key in lane.strike}


def test_layer_effects_are_not_drawn_on_the_whole_picture():
    assert set(events.LAYER) == {"lens", "mirror"}
    assert events.PICTURE == events.NAMES[:16]          # the whole-picture shader's indices are unchanged
    assert all(events.EVENTS[n].group == "One layer" for n in events.LAYER)


def test_the_lens_lands_on_one_overlay_layer_in_a_new_colour_each_time():
    e = layered({"lens": {"rate": 1.0, "strength": 0.9}})
    colours, layers = set(), set()
    for i in range(1, 600):
        on = struck(e, "lens", i / 30)
        if not on:
            continue
        assert len(on) == 1
        (li, s), = on.items()
        assert li > 0                                       # an overlay, not the base layer
        assert 0.0 < s["lens"] <= 0.9
        colours.add((round(s["lens_r"], 3), round(s["lens_g"], 3), round(s["lens_b"], 3)))
        layers.add(li)
    assert len(colours) > 3 and layers == {1, 2}
    for r, g, b in colours:
        assert max(r, g, b) == pytest.approx(1.0) and min(r, g, b) < 0.5     # a strong, single colour


def test_the_lens_fades_in():
    e = engine({})
    e.events.fire("lens", 2.0)
    amounts = [e.state(2.0 + k * 0.1).events["lens"][0] for k in range(1, 12)]
    assert amounts == sorted(amounts) and amounts[0] < 0.25 * amounts[-1]   # rises over the first 1.2 s
    assert struck(e, "lens", 2.5) == {0: struck(e, "lens", 2.5)[0]}          # one layer only: it goes on that


def test_a_lone_layer_still_gets_the_lens():
    e = layered({}, lanes=1)
    e.events.fire("lens", 1.0)
    assert list(struck(e, "lens", 1.5)) == [0]


def test_the_mirror_folds_as_many_layers_as_asked():
    for n in (1, 2, 3, 5):
        e = layered({"mirror": {"rate": 1.0, "layers": n}}, lanes=4)
        counts = {len(struck(e, "mirror", i / 10)) for i in range(5, 200)} - {0}
        assert counts == {min(n, 4)}, n


def test_muted_layers_are_left_alone():
    e = layered({"mirror": {"rate": 1.0, "layers": 3}, "lens": {"rate": 1.0}})
    e.project.lanes[1].mute = True
    for i in range(5, 200):
        assert 1 not in struck(e, "mirror", i / 10) and 1 not in struck(e, "lens", i / 10)


def test_the_mirror_moves_on_the_beat():
    # A beat every 0.5 s from 0.37 s. On the beat: zoom is deepest right after each beat and the turn
    # only changes across a beat. Off it: both glide.
    def run(beat, zoom="middle"):
        e = layered({}, lanes=1)
        e.project.shard_fx = {"mirror": {"style": "kaleido", "segments": 8, "zoom": zoom, "depth": 0.8, "beat": beat}}
        e.events.fire("mirror", 1.0)
        return [(t, struck(e, "mirror", t)[0]) for t in np.arange(1.02, 4.9, 0.02)]

    on = run(True)
    beat_of = lambda t: math.floor((t - 0.37) * 2 + 1e-6)   # noqa: E731
    for (t0, a), (t1, b) in zip(on, on[1:]):
        if beat_of(t0) == beat_of(t1):
            assert a["fold_turn"] == pytest.approx(b["fold_turn"])
            assert b["fold_zoom"] <= a["fold_zoom"] + 1e-9           # eases off between beats
        else:
            assert (b["fold_turn"] - a["fold_turn"]) % math.tau == pytest.approx(math.pi / 8)   # one notch
            assert b["fold_zoom"] > 0.6                              # punches in on the beat
    off = run(False)
    turns = [s["fold_turn"] for _, s in off]
    assert len({round(x, 6) for x in turns}) > 0.9 * len(turns)      # turning all the time
    assert all(s["fold_zoom"] >= 0.0 for _, s in off)
    assert all(s["fold_zoom"] <= 0.0 for _, s in run(True, "edges")) # from the edges: copies come in
    assert all(s["fold_zoom"] == 0.0 for _, s in run(True, "none"))


def test_layer_effects_are_the_same_every_time_and_from_any_start():
    fx = {"lens": {"rate": 0.7}, "mirror": {"rate": 0.6, "layers": 2, "beat": False}}
    times = [7.3, 2.0, 11.9, 0.4, 7.3, 9.95]
    jumping = layered(fx)

    def strikes_at(e, t):
        return [lane.strike for lane in e.state(t).lanes]
    fresh = [strikes_at(layered(fx), t) for t in times]
    assert [strikes_at(jumping, t) for t in times] == fresh
    assert any(any(s) for s in fresh)


def lane_picture(r, lanes, t=0.5):
    st = FrameState(t, 0, 0, lanes, {}, ["#000000", "#808080", "#ffffff"], {}, {})
    r.render(st)
    r.render(st)
    return r.read().astype(int)


def test_the_lens_colours_only_its_layer(ctx):  # noqa: F811
    from shardloom.renderer import Renderer

    r = Renderer(ctx, W, H, Frames())
    voice = Voice(0, 0, 0, 0.0, 99.0, 1.0, False, 0, {})
    green = {"lens": 1.0, "lens_r": 0.0, "lens_g": 1.0, "lens_b": 0.0}
    plain = lane_picture(r, [LaneState(voice, 0, {}, 1.0, "normal")])
    lensed = lane_picture(r, [LaneState(voice, 0, {}, 1.0, "normal", green)])
    assert lensed[..., 1].mean() > 40 and lensed[..., 0].max() <= 2 and lensed[..., 2].max() <= 2
    assert np.abs(lensed - plain).mean() > 20
    # Under a solid layer the lens does not show; on top it does.
    top = LaneState(voice, 0, {}, 1.0, "normal")
    assert np.array_equal(lane_picture(r, [LaneState(voice, 0, {}, 1.0, "normal", green), top]), plain)
    assert np.array_equal(lane_picture(r, [top, LaneState(voice, 0, {}, 1.0, "normal", green)]), lensed)


def test_the_mirror_folds_the_layer(ctx):  # noqa: F811
    from shardloom.renderer import Renderer

    r = Renderer(ctx, W, H, Frames())
    voice = Voice(0, 0, 0, 0.0, 99.0, 1.0, False, 0, {})

    def folded(**kw):
        s = {"fold": 1.0, "fold_style": 0.0, "fold_segments": 6.0, "fold_zoom": 0.0, "fold_turn": 0.0, **kw}
        return lane_picture(r, [LaneState(voice, 0, {}, 1.0, "normal", s)])

    plain = lane_picture(r, [LaneState(voice, 0, {}, 1.0, "normal")])
    assert np.abs(plain - plain[:, ::-1]).mean() > 30                 # the test picture is a left-right gradient
    m = folded()
    assert np.abs(m - m[:, ::-1]).mean() < 3                          # mirrored: the same both ways
    q = folded(fold_style=1.0)
    assert np.abs(q - q[:, ::-1]).mean() < 3 and np.abs(q - q[::-1]).mean() < 3
    k = folded(fold_style=2.0, fold_segments=6.0)
    assert np.abs(k - plain).mean() > 10 and np.abs(k - m).mean() > 3
    assert np.abs(folded(fold_zoom=0.8) - m).mean() > 3               # zooming changes it
    assert np.abs(folded(fold_zoom=-0.8) - folded(fold_zoom=0.8)).mean() > 3
    half = lane_picture(r, [LaneState(voice, 0, {}, 1.0, "normal", {"fold": 0.5, "fold_style": 0.0})])
    assert np.abs(half - (plain + m) / 2).mean() < 3                  # half way: a cross-fade
