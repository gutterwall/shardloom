"""Offline render of a project to an MP4 (H.264 + AAC), frame-exact: the song section with its titles and fades."""

from __future__ import annotations

import os
import time

import numpy as np

from .compose import Composer, Program
from .media import Mp4Writer, decode_audio

AUDIO_RATE = 44100
AAC_FRAME = 1024


class Exporter:
    """Renders a program frame by frame; step() lets a window keep drawing meanwhile.

    Without a program, renders [start, end) seconds of the song with no titles.
    """

    def __init__(self, engine, renderer, out_path: str | os.PathLike, start: float = 0.0, end: float | None = None,
                 fps: float | None = None, music: str | None = None, bitrate: int = 8_000_000,
                 program: Program | None = None, directory: str | os.PathLike = ".", preset: str = "medium"):
        self.engine, self.renderer, self.path = engine, renderer, str(out_path)
        self.fps = fps or engine.project.fps
        duration = engine.analysis.duration
        if program is None:
            if end is None:
                end = duration
            elif duration:
                end = min(end, duration)
            if end <= start:
                raise ValueError("nothing to render: end is before start")
            program = Program.plain(start, end)
        self.program = program
        self.composer = Composer(engine, renderer, program, directory)
        self.start, self.end = 0.0, program.duration
        self.frames = int(round(program.duration * self.fps))
        self.done = 0
        music = music if music is not None else engine.project.music
        self.audio = None
        if music:
            self.audio = program.audio(decode_audio(music, AUDIO_RATE, 2), AUDIO_RATE)
            want = int(round(self.frames / self.fps * AUDIO_RATE))
            if len(self.audio) < want:
                self.audio = np.concatenate([self.audio, np.zeros((want - len(self.audio), 2), np.float32)])
            self.audio = self.audio[:want]
        self.writer = Mp4Writer(out_path, renderer.width, renderer.height, self.fps, bitrate,
                                AUDIO_RATE if self.audio is not None else None, preset)
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
                self.composer.render(i / self.fps)
                self.writer.write_video(self.composer.read())
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


def project_exporter(session, ctx, out_path: str | os.PathLike, size: tuple[int, int] | None = None,
                     fps: float | None = None, quality: str | None = None, engine=None,
                     start: float | None = None, end: float | None = None, titles: bool = True) -> Exporter:
    """An exporter for a project's finished video at its output settings (or the size, fps and quality given)."""
    import copy

    from . import formats
    from .renderer import Renderer
    from .sequencer import Engine

    p = session.project
    if engine is None:
        pool = session.pool
        engine = Engine(copy.deepcopy(p), pool.shards, session.analysis,
                        {s.id: pool.length(s.id) for s in pool.shards}, pool.fps)
    w, h = size or (p.width, p.height)
    fps = fps or p.fps
    quality = quality or p.output.get("quality", "good")
    program = Program(p, session.analysis.duration, start, end, titles)
    renderer = Renderer(ctx, w, h, session.pool)
    return Exporter(engine, renderer, out_path, fps=fps, music=str(session.music_path),
                    bitrate=formats.bitrate(w, h, fps, quality), program=program, directory=session.dir,
                    preset=formats.preset(quality))


def export(engine, renderer, out_path: str | os.PathLike, start: float = 0.0, end: float | None = None,
           fps: float | None = None, music: str | None = None, bitrate: int = 8_000_000, progress=None,
           program: Program | None = None, directory: str | os.PathLike = ".", preset: str = "medium") -> dict:
    """Renders the program (or [start, end) seconds of the song) to out_path; returns timing figures."""
    job = Exporter(engine, renderer, out_path, start, end, fps, music, bitrate, program, directory, preset)
    return run(job, progress)


def run(job: Exporter, progress=None) -> dict:
    while not job.step(30):
        if progress:
            progress(job.done, job.frames)
    if progress:
        progress(job.frames, job.frames)
    return job.info()
