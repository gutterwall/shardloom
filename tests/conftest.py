import numpy as np
import pytest

from shardloom.analysis import RATE
from shardloom.media import Mp4Writer


def make_video(path, pattern: str, seconds: float = 3.0, fps: int = 30, size=(320, 240)) -> str:
    """A small H.264 MP4 (ball: a moving ball, lots of motion; still: one colour, none)."""
    w, h = size
    writer = Mp4Writer(path, w, h, fps, 2_000_000)
    yy, xx = np.mgrid[0:h, 0:w]
    for i in range(int(seconds * fps)):
        frame = np.zeros((h, w, 3), np.uint8)
        if pattern == "ball":
            cx = w / 2 + 0.4 * w * np.sin(i / 6.0)
            cy = h / 2 + 0.35 * h * np.cos(i / 4.0)
            frame[(xx - cx) ** 2 + (yy - cy) ** 2 < (h / 8) ** 2] = (255, 255, 255)
        else:
            frame[:] = (40, 90, 160)
        writer.write_video(frame)
    writer.close()
    return str(path)


def make_wav(path, samples: np.ndarray, rate: int = RATE) -> str:
    import wave

    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return str(path)


def click_track(bpm: float, seconds: float = 20.0, start: float = 0.37, seed: int = 1, rate: int = RATE,
                loud_from: float | None = None) -> np.ndarray:
    """Kick-like clicks on every beat over a little noise; louder after loud_from seconds."""
    n = int(seconds * rate)
    x = np.random.default_rng(seed).normal(0, 0.01, n).astype(np.float32)
    period = 60.0 / bpm
    k = 0
    while start + k * period < seconds:
        s = int((start + k * period) * rate)
        length = min(int(0.03 * rate), n - s)
        gain = (1.0 if k % 4 == 0 else 0.6) * (1.0 if loud_from is None or start + k * period >= loud_from else 0.15)
        x[s:s + length] += gain * np.sin(2 * np.pi * 80 * np.arange(length) / rate) * np.exp(-np.arange(length) / 400)
        k += 1
    return x


@pytest.fixture(scope="session")
def videos(tmp_path_factory):
    d = tmp_path_factory.mktemp("videos")
    return {"ball": make_video(d / "ball.mp4", "ball"), "still": make_video(d / "still.mp4", "still")}


@pytest.fixture(scope="session")
def song(tmp_path_factory):
    d = tmp_path_factory.mktemp("song")
    # 24 s at 120 BPM, quiet for the first half, loud for the second.
    return make_wav(d / "song.wav", click_track(120, 24.0, loud_from=12.0))
