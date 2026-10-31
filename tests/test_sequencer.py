from collections import Counter

import pytest

from shardloom.analysis import analyse_samples
from shardloom.sequencer import Engine, Lane, Modulation, Pattern, Project, Step, rand, starter_project
from shardloom.slicer import Shard

from conftest import click_track

ANALYSIS = analyse_samples(click_track(120, 60.0, loud_from=30.0)).with_grid(120.0, 0.0)


def shards(n=8, frames=30):
    out = [Shard(i, f"{i:04d}.npy", "src", 0.0, 1.0, frames, motion=i / n) for i in range(n)]
    for i, s in enumerate(out):
        s.energy = i / (n - 1)
    return out


def project(lanes=1, steps=None, choice="random", length=16):
    p = Project(seed=5)
    p.lanes = [Lane(f"L{i}", choice=choice) for i in range(lanes)]
    p.patterns = [Pattern("A", length, [[Step(**(steps or {"on": True})) for _ in range(length)] for _ in range(lanes)])]
    p.normalise()
    return p


def engine(p, n=8):
    return Engine(p, shards(n), ANALYSIS, pool_fps=30.0)


def triggers(e, seconds=30.0, lane=0):
    """(step, shard) of every voice started in the first seconds."""
    out = []
    for g in range(e.step_at(seconds)):
        e.advance(e.time_of_step(g) + 1e-6)
        for v in e._voices[lane]:
            if v.step == g:
                out.append((g, v.shard))
    return out


def test_rand_is_repeatable_and_uniform():
    assert rand(1, 2, 3) == rand(1, 2, 3) != rand(1, 2, 4)
    values = [rand(9, i) for i in range(20000)]
    assert 0.0 <= min(values) and max(values) < 1.0
    assert abs(sum(values) / len(values) - 0.5) < 0.01


def test_steps_follow_the_beat_grid():
    e = engine(project())
    # 120 BPM, 4 steps a beat: a step every 0.125 s.
    assert e.step_at(0.0) == 0 and e.step_at(0.124) == 0 and e.step_at(0.126) == 1
    assert e.time_of_step(8) == pytest.approx(1.0)


def test_same_project_same_picture():
    a, b = engine(project()), engine(project())
    for t in [0.1, 1.3, 7.7, 12.25, 29.9]:
        sa, sb = a.state(t), b.state(t)
        assert [(l.voice and l.voice.shard, l.frame) for l in sa.lanes] == [(l.voice and l.voice.shard, l.frame) for l in sb.lanes]


def test_seeking_back_replays_identically():
    e = engine(project(choice="bag"))
    forward = [(e.state(t).lanes[0].voice.shard, e.state(t).lanes[0].frame) for t in [1.0, 2.0, 3.0]]
    e.state(20.0)
    again = [(e.state(t).lanes[0].voice.shard, e.state(t).lanes[0].frame) for t in [1.0, 2.0, 3.0]]
    assert forward == again


def test_probability():
    e = engine(project(steps={"on": True, "prob": 0.3}))
    fired = len(triggers(e, 30.0))
    steps = e.step_at(30.0)
    assert fired / steps == pytest.approx(0.3, abs=0.05)


def test_fill_forces_every_step():
    e = engine(project(steps={"on": True, "prob": 0.1}))
    e.fill = True
    assert len(triggers(e, 5.0)) == e.step_at(5.0)


def test_bag_uses_every_shard_before_repeating():
    e = engine(project(choice="bag"), n=8)
    picks = [s for _, s in triggers(e, 12.0)]
    for i in range(0, len(picks) - 8, 8):
        assert sorted(picks[i:i + 8]) == list(range(8))
    # And no shard twice in a row across rounds.
    assert all(a != b for a, b in zip(picks, picks[1:]))


def test_no_repeat():
    e = engine(project(choice="no_repeat"), n=3)
    picks = [s for _, s in triggers(e, 20.0)]
    assert all(a != b for a, b in zip(picks, picks[1:]))
    assert set(picks) == {0, 1, 2}


def test_intensity_picks_busier_shards_when_the_music_is_louder():
    e = engine(project(choice="intensity"), n=10)
    picks = triggers(e, 60.0)
    quiet = [e.shards[s].energy for g, s in picks if 5.0 < e.time_of_step(g) < 25.0]
    loud = [e.shards[s].energy for g, s in picks if 35.0 < e.time_of_step(g) < 55.0]
    assert sum(loud) / len(loud) > sum(quiet) / len(quiet) + 0.3


def test_fixed_shard_and_gate():
    p = project(steps={"on": False})
    p.patterns[0].steps[0][0] = Step(on=True, shard=6, gate=2.0, speed=2.0, jitter=0.0)
    e = engine(p)
    st = e.state(0.01)
    assert st.lanes[0].voice.shard == 6 and st.lanes[0].frame == 0
    # Speed 2: 0.2 s in is 12 frames at 30 fps.
    assert e.state(0.2).lanes[0].frame == 12
    # Gate of 2 steps (0.25 s): silent afterwards until the pattern comes round.
    assert e.state(0.24).lanes[0].voice is not None
    assert e.state(0.26).lanes[0].voice is None


def test_reverse_plays_backwards():
    p = project(steps={"on": False})
    p.patterns[0].steps[0][0] = Step(on=True, shard=1, gate=4, reverse=True, jitter=0.0)
    e = engine(p, n=3)
    frames = [e.state(t).lanes[0].frame for t in (0.0, 0.1, 0.2)]
    assert frames == [29, 26, 23]


def test_step_and_lane_fx_and_modulation_add_up():
    p = project(steps={"on": True, "fx": {"invert": 0.5}})
    p.lanes[0].fx = {"invert": 0.2, "hue": 0.1}
    p.modulations = [Modulation("beat", "lane0.invert", 1.0), Modulation("bass", "master.zoom", 0.5)]
    e = engine(p)
    on_beat = e.state(ANALYSIS.beat_times()[4] + 0.001)
    assert on_beat.lanes[0].fx["invert"] == 1.0          # 0.5 + 0.2 + ~1, clamped
    assert on_beat.lanes[0].fx["hue"] == pytest.approx(0.1)
    between = e.state(ANALYSIS.beat_times()[4] + 0.45)
    assert between.lanes[0].fx["invert"] == pytest.approx(0.7, abs=0.02)
    assert "zoom" in between.master


def test_song_arrangement():
    p = project()
    p.patterns.append(Pattern("B", 8, [[Step(on=True) for _ in range(8)]]))
    p.song = [{"pattern": 0, "repeats": 2}, {"pattern": 1, "repeats": 1}]
    e = engine(p)
    assert [e.pattern_for_step(g)[0] for g in (0, 31, 32, 39, 40)] == [0, 0, 1, 1, 0]


def test_live_pattern_switch_happens_at_the_pattern_end_and_records():
    p = project()
    p.patterns.append(Pattern("B", 16, [[Step(on=True) for _ in range(16)]]))
    e = engine(p)
    e.recording = True
    e.state(e.time_of_step(5))
    e.queue_pattern(1)
    assert e.state(e.time_of_step(15)).pattern == 0
    assert e.state(e.time_of_step(16)).pattern == 1
    assert p.performance == [{"step": 16, "type": "pattern", "value": 1}]
    # Played back (not recording), a fresh engine follows the recording.
    e2 = engine(p)
    assert e2.state(e2.time_of_step(15)).pattern == 0 and e2.state(e2.time_of_step(17)).pattern == 1


def test_recorded_punch_in_plays_back():
    p = project()
    e = engine(p)
    e.recording = True
    e.record_punch(2.0, "invert", True)
    e.record_punch(3.0, "invert", False)
    e2 = engine(p)
    assert "invert" not in e2.state(1.9).master
    assert e2.state(2.5).master["invert"] == 1.0
    assert "invert" not in e2.state(3.1).master


def test_palette_changes_when_the_music_gets_louder():
    p = project()
    e = engine(p)
    quiet = {tuple(e.state(t).palette) for t in (4.0, 8.0, 12.0)}
    loud = {tuple(e.state(t).palette) for t in (40.0, 44.0, 48.0)}
    assert len(quiet) == 1 and len(loud) == 1 and quiet != loud


def test_project_round_trip(tmp_path):
    p = starter_project("/music.mp3", "pool", lanes=3)
    p.performance = [{"step": 16, "type": "pattern", "value": 1}]
    p.save(tmp_path)
    q = Project.load(tmp_path)
    assert q.to_dict() == p.to_dict()


def test_starter_project_plays_something():
    p = starter_project("/music.mp3", "pool", lanes=3)
    e = engine(p, n=12)
    active = Counter()
    for t in [i * 0.37 for i in range(100)]:
        for li, lane in enumerate(e.state(t).lanes):
            if lane.voice:
                active[li] += 1
    assert active[0] > 90 and active[1] > 5 and active[2] > 5
