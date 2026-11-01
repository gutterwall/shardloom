import numpy as np
import pytest

from shardloom import pace
from shardloom.analysis import analyse_samples
from shardloom.sequencer import Engine, Lane, Pattern, Project, Step
from shardloom.slicer import Shard

from conftest import click_track


def engine(**settings):
    p = Project(seed=5)
    p.lanes = [Lane("A"), Lane("B", choice="random")]
    p.patterns = [Pattern("A", 16, [[Step(on=True) for _ in range(16)] for _ in range(2)])]
    p.pace = {**pace.default_pace(), "on": True, **settings}
    shards = [Shard(i, "", "", 0, 1, 10) for i in range(8)]
    return Engine(p, shards, analyse_samples(click_track(120, 40.0)).with_grid(120.0, 0.37))


def voices(e, seconds=30.0, lane=0):
    """Every voice lane started, in order."""
    seen, out = set(), []
    for i in range(int(seconds * 30)):
        e.state(i / 30)
        for v in e._voices[lane]:
            if (v.step, v.shard) not in seen:
                seen.add((v.step, v.shard))
                out.append(v)
    return out


def test_a_phrase_repeats_one_shard_from_the_same_frame():
    e = engine(weights=[0, 0, 0, 1, 0, 0, 0], repeats=4)            # always 2 beats, 4 times
    vs = voices(e)
    groups = [vs[i:i + 4] for i in range(0, len(vs) - 3, 4)]
    assert len(groups) > 5
    for grp in groups:
        assert len({v.shard for v in grp}) == 1 and len({v.start for v in grp}) == 1
        steps = [v.step for v in grp]
        assert np.diff(steps).tolist() == [8, 8, 8]                    # 2 beats of 4 steps
        assert grp[0].t1 == pytest.approx(e.time_of_step(grp[0].step + 8))
    assert len({grp[0].shard for grp in groups}) > 2                  # and then it moves on


def test_only_the_lengths_with_weight():
    e = engine(weights=[0, 1, 0, 0, 0, 0, 1], repeats=1, intensity=0.0)
    lengths = {e.phrase(0, g)[4] for g in range(0, 400)}
    assert lengths == {0.5, 8.0}


def test_speed_and_intensity_lean_the_lengths():
    flat = {**pace.default_pace(), "weights": [1] * 7, "intensity": 0.0}
    mean = lambda s: sum(n * x for n, x in zip(pace.LENGTHS, s))  # noqa: E731
    assert mean(pace.shares({**flat, "tilt": -1.0})) < mean(pace.shares(flat)) < mean(pace.shares({**flat, "tilt": 1.0}))
    loud = {**flat, "intensity": 1.0}
    assert mean(pace.shares(loud, intensity=1.0)) < mean(pace.shares(loud, intensity=0.5)) < mean(pace.shares(loud, 0.0))
    assert pace.shares(flat, 0.3) == pace.shares(flat, 0.9)            # intensity 0: the music doesn't matter
    assert pace.shares({**flat, "weights": [0] * 7}) == [0, 0, 1, 0, 0, 0, 0]


def test_the_same_from_any_start():
    a = engine(weights=[1] * 7, repeats=3)
    b = engine(weights=[1] * 7, repeats=3)
    a.state(0.0)
    late = [a.phrase(1, g) for g in range(300, 320)]
    b.state(20.0)                                                       # a render that starts later
    assert [b.phrase(1, g) for g in range(300, 320)] == late


def test_off_keeps_the_sequencer():
    e = engine(weights=[0, 0, 0, 0, 0, 0, 1], repeats=8)
    e.project.pace["on"] = False
    vs = voices(e, 4.0)
    assert np.diff([v.step for v in vs]).max() == 1                    # a cut on every step, as the pattern says
