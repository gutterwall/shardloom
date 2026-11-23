"""The trim editor: the parts of a video shards are cut from, editing them, and decoding for the window."""

import json
import time

import numpy as np
import pytest

from shardloom import library
from shardloom.media import MediaError
from shardloom.sequencer import Project, Source, starter_project
from shardloom.trim import FramePlayer, Filmstrip, TrimWindow, add_part, drag_end, mark_in, mark_out


# ---- the parts of a Source ---------------------------------------------------

def test_without_ranges_the_from_to_is_the_one_part():
    assert Source(start=1.0, end=4.0).spans(10.0) == [(1.0, 4.0)]
    assert Source().spans(10.0) == [(0.0, 10.0)]
    assert Source(start=12.0).spans(10.0) == [(10.0, 10.0)]


def test_ranges_are_sorted_clipped_and_merged():
    s = Source(ranges=[[6.0, 8.0], [1.0, 2.0], [7.5, 9.0], [-1.0, 0.5], [11.0, 12.0], [3.0, 3.0]])
    assert s.spans(10.0) == [(0.0, 0.5), (1.0, 2.0), (6.0, 9.0)]


def test_set_spans_keeps_one_part_in_from_to_and_several_in_ranges():
    s = Source()
    s.set_spans([[2.0, 5.0]], 10.0)
    assert (s.start, s.end, s.ranges) == (2.0, 5.0, [])
    s.set_spans([[2.0, 10.0]], 10.0)
    assert (s.start, s.end, s.ranges) == (2.0, None, [])
    s.set_spans([[6.0, 7.0], [1.0, 2.5]], 10.0)
    assert s.ranges == [[1.0, 2.5], [6.0, 7.0]]
    assert (s.start, s.end) == (1.0, 2.5)         # what an older Shardloom uses
    s.set_spans([], 10.0)
    assert (s.start, s.end, s.ranges) == (0.0, None, [])


def test_ranges_are_saved_and_older_files_still_load(tmp_path):
    p = Project(sources=[Source("media/a.mp4", id="s1", ranges=[[1.0, 2.0], [4.0, 6.0]], start=1.0, end=2.0)])
    p.save(tmp_path)
    back = Project.load(tmp_path)
    assert back.sources[0].ranges == [[1.0, 2.0], [4.0, 6.0]]
    d = json.loads((tmp_path / "project.json").read_text())
    del d["sources"][0]["ranges"]
    old = Project.from_dict(d)
    assert old.sources[0].ranges == [] and old.sources[0].spans(10.0) == [(1.0, 2.0)]


# ---- editing parts ------------------------------------------------------------

def test_add_part_fills_the_room_up_to_the_next_part():
    spans = [[4.0, 6.0]]
    assert add_part(spans, 1.0, 10.0) == 0
    assert spans == [[1.0, 4.0], [4.0, 6.0]]
    assert add_part(spans, 5.0, 10.0) == 1          # already in a part: that part
    assert add_part(spans, 9.95, 10.0) == 2         # no room after: it ends at the end
    assert spans[2] == [6.0, 10.0]
    assert add_part([[0.0, 10.0]], 3.0, 10.0) == 0


def test_mark_in_and_out():
    spans = [[0.0, 10.0]]
    assert mark_in(spans, 0, 2.0, 10.0) == 0
    assert mark_out(spans, 0, 4.0, 10.0) == 0
    assert spans == [[2.0, 4.0]]
    # Just after the selected part, the stop moves out to the playhead.
    assert mark_out(spans, 0, 4.5, 10.0) == 0
    assert spans == [[2.0, 4.5]]
    # Far from it (another part is nearer), a new part starts.
    assert mark_in(spans, None, 7.0, 10.0) == 1
    assert spans == [[2.0, 4.5], [7.0, 10.0]]
    # Before the selected part, its start moves back, but not into the part before.
    assert mark_in(spans, 1, 5.0, 10.0) == 1
    assert spans[1] == [5.0, 10.0]
    # A stop where no part is: a new part ending there.
    spans = [[0.0, 1.0]]
    assert mark_out(spans, None, 8.0, 10.0) == 1
    assert spans == [[0.0, 1.0], [3.0, 8.0]]
    # A start past the stop keeps the part at least MIN_PART long.
    spans = [[2.0, 3.0]]
    mark_in(spans, 0, 3.0, 10.0)
    assert spans == [[2.9, 3.0]]


def test_dragging_an_end_stops_at_the_neighbours():
    spans = [[1.0, 2.0], [3.0, 4.0]]
    drag_end(spans, 0, 1, 3.5, 10.0)
    assert spans[0] == [1.0, 3.0]
    drag_end(spans, 1, 0, 0.0, 10.0)
    assert spans[1] == [3.0, 4.0]
    drag_end(spans, 1, 1, 2.0, 10.0)
    assert spans[1] == [3.0, 3.1]
    drag_end(spans, 0, 0, -5.0, 10.0)
    assert spans[0] == [0.0, 3.0]


# ---- cutting from parts ------------------------------------------------------------

def project_with(tmp_path, videos, song):
    d = tmp_path / "proj"
    p = starter_project("", "pool", 2, 4)
    p.cut.update(count=12, min=0.2, max=0.4, seed=5)
    p.music = library.import_file(d, song)
    library.add_source(p, d, videos["ball"])
    return d, p


def test_shards_are_cut_only_from_the_parts(tmp_path, videos, song):
    d, p = project_with(tmp_path, videos, song)
    p.sources[0].set_spans([[0.2, 1.0], [1.8, 2.8]], 3.0)
    shards = library.build_pool(p, d, 64, 36)
    assert shards
    inside = [any(a - 1e-6 <= s.start and s.start + s.duration <= b + 1e-6 for a, b in ((0.2, 1.0), (1.8, 2.8)))
              for s in shards]
    assert all(inside)
    assert {s.start < 1.4 for s in shards} == {True, False}      # both parts are cut from


def test_one_part_cuts_as_before_and_parts_change_the_recipe(tmp_path, videos, song):
    d, p = project_with(tmp_path, videos, song)
    p.sources[0].start, p.sources[0].end = 0.5, 2.5
    before = library.recipe(p, 64, 36)
    first = [(s.start, s.duration) for s in library.build_pool(p, d, 64, 36)]
    p.sources[0].set_spans([[0.5, 2.5]], 3.0)
    assert library.recipe(p, 64, 36) == before
    assert [(s.start, s.duration) for s in library.build_pool(p, d, 64, 36)] == first
    p.sources[0].set_spans([[0.5, 1.5], [2.0, 2.5]], 3.0)
    assert library.recipe(p, 64, 36) != before


# ---- decoding for the window --------------------------------------------------------

def wait(cond, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        got = cond()
        if got:
            return got
        time.sleep(0.01)
    raise AssertionError("timed out")


def test_the_player_seeks_and_plays(videos):
    p = FramePlayer(videos["ball"], 160, 160)
    try:
        assert (p.width, p.height) == (160, 120) and p.duration == pytest.approx(3.0, abs=0.1)
        t, first = wait(p.take)
        assert first.shape == (120, 160, 3) and t == 0.0
        p.seek(1.5)
        t, img = wait(p.take)
        assert t == 1.5 and not np.array_equal(img, first)
        p.play(until=2.0)
        wait(lambda: not p.playing, 5.0)
        assert 1.8 <= p.position < 2.0
        p.seek(p.duration)
        wait(p.take)
        p.play()                           # at the end: plays from the start
        wait(lambda: 0.0 < p.position < 1.0)
        p.pause()
    finally:
        p.close()
    assert not p._thread.is_alive()


def test_a_missing_video_says_so(tmp_path):
    with pytest.raises(MediaError):
        FramePlayer(tmp_path / "missing.mp4")


def test_the_filmstrip(videos):
    st = Filmstrip(videos["ball"], 3.0, 6, height=30, aspect=4 / 3)
    try:
        wait(lambda: len(st.frames) == 6)
        assert st.frames[0].shape == (30, 40, 3)
    finally:
        st.close()


def test_the_window_keeps_its_parts_in_the_source(tmp_path, videos, song):
    d, p = project_with(tmp_path, videos, song)
    src = p.sources[0]
    w = TrimWindow(None, d, src, shortest=0.2)
    try:
        assert w.spans == [[0.0, w.duration]]
        w.seek(0.5)
        w.mark(0)
        w.seek(1.0)
        w.mark(1)
        assert (src.start, src.end, src.ranges) == (0.5, 1.0, [])
        w.seek(2.0)
        w.add()
        assert len(src.ranges) == 2 and src.ranges[0] == [0.5, 1.0] and src.ranges[1][0] == 2.0
        w.delete()
        assert (src.start, src.end, src.ranges) == (0.5, 1.0, [])
        w.whole()
        assert (src.start, src.end, src.ranges) == (0.0, None, [])
    finally:
        w.close()
