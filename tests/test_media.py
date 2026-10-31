import numpy as np
import pytest

from shardloom.media import AudioPlayer, MediaError, Mp4Writer, VideoDecoder, decode_audio, probe

from conftest import make_wav


def bars(n, w=64, h=48):
    """Frame n as seven black / white bars, its number in binary (bit 0 leftmost)."""
    img = np.zeros((h, w, 3), np.uint8)
    for bit in range(7):
        if n >> bit & 1:
            img[:, bit * w // 7:(bit + 1) * w // 7] = 255
    return img


@pytest.fixture(scope="module")
def counter(tmp_path_factory):
    """4 s at 25 fps; each frame shows its own number."""
    path = tmp_path_factory.mktemp("counter") / "counter.mp4"
    w = Mp4Writer(path, 64, 48, 25, 4_000_000)
    for n in range(100):
        w.write_video(bars(n))
    w.close()
    return str(path)


def index(frame):
    w = frame.shape[1]
    return sum(1 << bit for bit in range(7)
               if frame[:, bit * w // 7 + 2:(bit + 1) * w // 7 - 2].mean() > 128)


def test_probe(counter):
    info = probe(counter)
    assert info["duration"] == pytest.approx(4.0, abs=0.05)
    assert info["video"] == {"width": 64, "height": 48, "fps": pytest.approx(25.0)}
    assert info["audio"] is False


@pytest.mark.parametrize("start", [0.0, 1.0, 2.36, 3.5])
def test_segment_starts_on_the_right_frame(counter, start):
    with VideoDecoder(counter, 64, 48, 25) as d:
        frames = d.segment(start, 0.4)
    assert len(frames) == 10
    first = int(start * 25 + 1e-9)  # the latest frame at or before start
    assert [index(f) for f in frames] == list(range(first, first + 10))


def test_segment_at_another_frame_rate(counter):
    with VideoDecoder(counter, 64, 48, 12.5) as d:
        frames = d.segment(1.0, 0.8)
    # Every other source frame.
    assert frames.shape == (10, 48, 64, 3)
    assert [index(f) for f in frames] == list(range(25, 45, 2))


def test_segment_past_the_end_repeats_the_last_frame(counter):
    with VideoDecoder(counter, 64, 48, 25) as d:
        frames = d.segment(3.8, 0.4)
    assert [index(f) for f in frames] == [95, 96, 97, 98, 99, 99, 99, 99, 99, 99]


def test_cover_crop_keeps_the_centre(tmp_path):
    path = tmp_path / "wide.mp4"
    w = Mp4Writer(path, 128, 32, 25)
    img = np.zeros((32, 128, 3), np.uint8)
    img[:, 48:80] = 250  # a bright centre band, a quarter of the width
    for _ in range(5):
        w.write_video(img)
    w.close()
    with VideoDecoder(path, 32, 32, 25) as d:
        frame = d.segment(0.0, 0.04)[0]
    # Scaled to cover 32x32 (128x32 stays), cropped to the middle 32 columns: all bright.
    assert frame.mean() > 200


def test_bad_file(tmp_path):
    p = tmp_path / "not.mp4"
    p.write_bytes(b"not a video")
    with pytest.raises(MediaError):
        probe(p)


def test_decode_audio_mono_and_stereo(tmp_path):
    t = np.arange(22050) / 22050
    path = make_wav(tmp_path / "tone.wav", 0.5 * np.sin(2 * np.pi * 440 * t))
    mono = decode_audio(path, 22050)
    assert mono.shape == (22050,) and abs(mono).max() == pytest.approx(0.5, abs=0.01)
    stereo = decode_audio(path, 44100, 2)
    assert stereo.shape == (44100, 2) and np.allclose(stereo[:, 0], stereo[:, 1])


def test_audio_player_clock(tmp_path):
    path = make_wav(tmp_path / "quiet.wav", np.zeros(22050 * 3))
    player = AudioPlayer(path)
    assert player.duration == pytest.approx(3.0, abs=0.01)
    player.seek(1.0)
    assert player.position() == pytest.approx(1.0)
    try:
        player.play()
    except MediaError as e:
        pytest.skip(f"no sound output here: {e}")
    import time

    time.sleep(0.5)
    pos = player.position()
    player.pause()
    assert pos == pytest.approx(1.5, abs=0.1)
    # Paused, the clock stands still.
    held = player.position()
    time.sleep(0.2)
    assert player.position() == held
    player.close()
