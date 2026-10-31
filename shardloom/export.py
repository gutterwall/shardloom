"""Offline render of a project to an MP4 (H.264 + AAC), frame-exact."""

from __future__ import annotations

import os
import time

import numpy as np

from .media import Mp4Writer, decode_audio

AUDIO_RATE = 44100
AAC_FRAME = 1024


class Exporter:
    """Renders [start, end) of a project frame by frame; step() lets a window keep drawing meanwhile."""

    def __init__(self, engine, renderer, out_path: str | os.PathLike, start: float = 0.0, end: float | None = None,
                 fps: float | None = None, music: str | None = None, bitrate: int = 8_000_000):
        self.engine, self.renderer, self.path = engine, renderer, str(out_path)
        self.fps = fps or engine.project.fps
        duration = engine.analysis.duration
        if end is None:
            end = duration
        elif duration:
            end = min(end, duration)
        if end <= start:
            raise ValueError("nothing to render: end is before start")
        self.start, self.end = start, end
        self.frames = int(round((end - start) * self.fps))
        self.done = 0
        music = music if music is not None else engine.project.music
        self.audio = None
        if music:
            pcm = decode_audio(music, AUDIO_RATE, 2)
            a0, a1 = int(start * AUDIO_RATE), int(end * AUDIO_RATE)
            self.audio = pcm[a0:a1]
            if len(self.audio) < a1 - a0:
                self.audio = np.concatenate([self.audio, np.zeros((a1 - a0 - len(self.audio), 2), np.float32)])
        self.writer = Mp4Writer(out_path, renderer.width, renderer.height, self.fps, bitrate,
                                AUDIO_RATE if self.audio is not None else None)
        self._apos = 0
        self.started = time.perf_counter()
        self.finished = False
        engine.reset()

    def step(self, frames: int = 1) -> bool:
        """Renders up to frames more; True when the file is complete."""
        try:
            for _ in range(frames):
                if self.done >= self.frames:
                    self._finish()
                    return True
                i = self.done
                self.renderer.render(self.engine.state(self.start + i / self.fps))
                self.writer.write_video(self.renderer.read())
                if self.audio is not None:
                    # Keep the audio level with the video so the file interleaves well.
                    want = min(len(self.audio), int((i + 1) / self.fps * AUDIO_RATE))
                    while self._apos + AAC_FRAME <= want:
                        self.writer.write_audio(self.audio[self._apos:self._apos + AAC_FRAME])
                        self._apos += AAC_FRAME
                self.done += 1
            if self.done >= self.frames:
                self._finish()
                return True
            return False
        except BaseException:
            self.cancel()
            raise

    def _finish(self) -> None:
        if self.finished:
            return
        if self.audio is not None:
            while self._apos < len(self.audio):
                self.writer.write_audio(self.audio[self._apos:self._apos + AAC_FRAME])
                self._apos += AAC_FRAME
        self.writer.close()
        self.finished = True

    def cancel(self) -> None:
        """Stops and removes the unfinished file."""
        if self.finished:
            return
        self.finished = True
        try:
            self.writer.container.close()
        except Exception:
            pass
        try:
            os.remove(self.path)
        except OSError:
            pass

    @property
    def progress(self) -> float:
        return self.done / max(1, self.frames)

    def info(self) -> dict:
        took = time.perf_counter() - self.started
        return {"frames": self.done, "seconds": self.end - self.start, "took": took,
                "fps": self.done / took if took else 0.0}


def export(engine, renderer, out_path: str | os.PathLike, start: float = 0.0, end: float | None = None,
           fps: float | None = None, music: str | None = None, bitrate: int = 8_000_000, progress=None) -> dict:
    """Renders [start, end) seconds of the project to out_path; returns timing figures."""
    job = Exporter(engine, renderer, out_path, start, end, fps, music, bitrate)
    while not job.step(30):
        if progress:
            progress(job.done, job.frames)
    if progress:
        progress(job.frames, job.frames)
    return job.info()
