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
    for name in events.NAMES:
        img = picture({name: (1.0, 0.4, 0.37)})
        assert np.abs(img - plain).mean() > 2.0, name
    assert np.array_equal(picture({"tear": (0.0, 0.4, 0.37)}), plain)   # struck with no strength: untouched
