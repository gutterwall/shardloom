import numpy as np
import pytest

from shardloom.analysis import Analysis, analyse_file, analyse_samples

from conftest import click_track


@pytest.mark.parametrize("bpm", [90, 120, 128, 140, 174])
def test_tempo_and_beats_follow_a_click_track(bpm):
    a = analyse_samples(click_track(bpm))
    assert a.tempo == pytest.approx(bpm, rel=0.015)
    period = 60.0 / bpm
    beats = a.beat_times()
    expected = int((20.0 - 0.37) / period) + 1
    assert abs(len(beats) - expected) <= 1
    # Every beat within 20 ms of a click (less than a frame at 30 fps).
    phase = (beats - 0.37) / period
    assert np.abs(phase - np.round(phase)).max() * period < 0.020


def test_beat_position_and_time_are_inverse():
    a = analyse_samples(click_track(120))
    for t in [0.1, 0.5, 3.33, 10.0, 19.9, 25.0]:
        assert a.time_at_beat(a.beat_position(t)) == pytest.approx(t, abs=1e-6)


def test_fixed_grid():
    a = analyse_samples(click_track(120)).with_grid(100.0, offset=0.5)
    assert a.tempo == 100.0
    assert a.beat_position(0.5) == pytest.approx(0.0)
    assert a.beat_position(0.5 + 6 * 0.6) == pytest.approx(6.0)


def test_curves_are_normalised_and_intensity_rises_with_the_music():
    a = analyse_samples(click_track(120, 24.0, loud_from=12.0))
    for name, curve in a.curves.items():
        assert curve.min() >= 0.0 and curve.max() <= 1.0, name
    quiet = np.mean([a.at(t)["intensity"] for t in np.arange(2, 10, 0.25)])
    loud = np.mean([a.at(t)["intensity"] for t in np.arange(14, 22, 0.25)])
    assert loud > quiet + 0.4


def test_beat_envelope_peaks_on_beats():
    a = analyse_samples(click_track(120))
    on = [a.at(t)["beat"] for t in a.beat_times()[2:10]]
    off = [a.at(t + 0.25)["beat"] for t in a.beat_times()[2:10]]
    assert min(on) > 0.95 and max(off) < 0.1


def test_save_and_load(tmp_path, song):
    a = analyse_file(song)
    a.save(tmp_path / "analysis")
    b = Analysis.load(tmp_path / "analysis")
    assert b.tempo == a.tempo and b.duration == a.duration
    assert np.array_equal(b.beats, a.beats)
    assert set(b.curves) == set(a.curves)
    assert a.duration == pytest.approx(24.0, abs=0.05)
