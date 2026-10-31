import numpy as np
import pytest

from shardloom.compose import Program
from shardloom.sequencer import Project, Title
from shardloom.titles import render_title


def project(titles=(), **output):
    p = Project()
    p.titles = list(titles)
    p.output.update({"fade_in": 0.0, "fade_out": 0.0, **output})
    return p


def test_plain_program_is_the_song_section():
    prog = Program(project(start=10.0, end=40.0), 60.0)
    assert prog.duration == 30.0 and prog.pre == 0
    assert prog.song_time(0.0) == 10.0 and prog.song_time(29.9) == pytest.approx(39.9)
    assert prog.song_time(30.0) is None
    assert Program(project(end=None), 60.0).duration == 60.0
    with pytest.raises(ValueError):
        Program(project(start=50.0, end=20.0), 60.0)


def test_cards_add_time_and_over_titles_do_not():
    titles = [Title("Intro", at="start", extra=True, duration=3.0), Title("Over", at="start", duration=2.0),
              Title("Bye", at="end", duration=4.0), Title("Card", at="end", extra=True, duration=5.0)]
    prog = Program(project(titles), 20.0)
    assert prog.pre == 3.0 and prog.post == 5.0 and prog.duration == 28.0
    spans = {pl.title.text: (pl.t0, pl.t1, pl.card) for pl in prog.placed}
    assert spans == {"Intro": (0.0, 3.0, True), "Over": (3.0, 5.0, False), "Bye": (19.0, 23.0, False),
                     "Card": (23.0, 28.0, True)}
    assert prog.song_time(1.0) is None and prog.song_time(3.0) == 0.0 and prog.song_time(25.0) is None
    assert prog.program_time(0.0) == 3.0


def test_title_fades():
    prog = Program(project([Title("A", duration=4.0, fade=1.0)]), 20.0)
    pl = prog.placed[0]
    assert pl.alpha(0.0) == 0.0 and pl.alpha(0.5) == pytest.approx(0.5) and pl.alpha(2.0) == 1.0
    assert pl.alpha(4.0) == 0.0
    assert [p.title.text for p, _ in prog.titles_at(2.0)] == ["A"] and prog.titles_at(10.0) == []


def test_picture_fade_and_audio():
    rate = 100
    titles = [Title("Intro", at="start", extra=True, duration=2.0)]
    prog = Program(project(titles, start=1.0, end=5.0, fade_in=1.0, fade_out=2.0), 10.0)
    assert prog.fade(0.0) == 0.0 and prog.fade(0.5) == 0.5 and prog.fade(3.0) == 1.0 and prog.fade(prog.duration) == 0.0
    song = np.ones((10 * rate, 2), np.float32)
    audio = prog.audio(song, rate)
    assert len(audio) == 6 * rate
    assert not audio[: 2 * rate].any()                      # silence under the card
    assert audio[2 * rate + 50, 0] == pytest.approx(0.5, abs=0.02)   # the fade in, half way
    assert audio[2 * rate + 150, 0] == 1.0
    assert audio[-1, 0] == pytest.approx(0.0, abs=0.01)


def test_title_picture():
    img = render_title(Title("Hello", "world", background="none", text_colour="#ff0000"), 320, 180)
    assert img.shape == (180, 320, 4)
    assert img[0, 0, 3] == 0                                 # see-through around the text
    text = img[..., 3] > 200
    assert text.any() and img[text][:, 0].mean() > 200 and img[text][:, 1].mean() < 60
    rows = np.where(text.any(axis=1))[0]
    assert 40 < rows.mean() < 140                            # in the middle
    lower = render_title(Title("Hello", position="lower"), 320, 180)
    assert np.where((lower[..., 3] > 200).any(axis=1))[0].mean() > rows.mean() + 20
    card = render_title(Title("x", background="colour", colour="#0000ff"), 64, 36)
    assert tuple(card[0, 0]) == (0, 0, 255, 255)
    black = render_title(Title("x"), 64, 36, card=True)     # a card with no background is black
    assert tuple(black[0, 0]) == (0, 0, 0, 255)
    long = render_title(Title("a very long title that has to wrap onto more than one line", size=0.2), 320, 180)
    assert len(np.where((long[..., 3] > 200).any(axis=1))[0]) > 60
