"""GStreamer helpers: probe, decode video segments to frames, decode audio to samples."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstApp", "1.0")
gi.require_version("GstPbutils", "1.0")
from gi.repository import Gst, GstApp, GstPbutils  # noqa: E402

Gst.init(None)

SECOND = Gst.SECOND


class MediaError(RuntimeError):
    pass


def uri(path: str | os.PathLike) -> str:
    return Gst.filename_to_uri(str(Path(path).resolve()))


def probe(path: str | os.PathLike) -> dict:
    """Duration (seconds) and, for video, width, height and frame rate."""
    discoverer = GstPbutils.Discoverer.new(20 * SECOND)
    try:
        info = discoverer.discover_uri(uri(path))
    except gi.repository.GLib.Error as e:
        raise MediaError(f"{path}: {e.message}") from e
    out = {"duration": info.get_duration() / SECOND, "video": None, "audio": bool(info.get_audio_streams())}
    streams = info.get_video_streams()
    if streams:
        v = streams[0]
        num, den = v.get_framerate_num(), v.get_framerate_denom()
        out["video"] = {
            "width": v.get_width(),
            "height": v.get_height(),
            "fps": num / den if num and den else 25.0,
        }
    return out


def _check_bus(pipeline: Gst.Pipeline, what: str) -> None:
    msg = pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR)
    if msg:
        err, dbg = msg.parse_error()
        raise MediaError(f"{what}: {err.message}")


def _wait_state(pipeline: Gst.Pipeline, what: str) -> None:
    ret, _, _ = pipeline.get_state(30 * SECOND)
    if ret == Gst.StateChangeReturn.FAILURE:
        _check_bus(pipeline, what)
        raise MediaError(f"{what}: could not start decoding")


class VideoDecoder:
    """Decodes segments of one video file to RGB frames of a fixed size.

    Frames are scaled to cover width x height and cropped to the centre, at a
    fixed frame rate, so shards from different sources line up.
    """

    def __init__(self, path: str | os.PathLike, width: int, height: int, fps: float):
        self.path = str(path)
        self.width, self.height, self.fps = width, height, fps
        info = probe(path)
        if not info["video"]:
            raise MediaError(f"{path}: no video stream")
        self.duration = info["duration"]
        sw, sh = info["video"]["width"], info["video"]["height"]
        scale = max(width / sw, height / sh)
        # Even sizes keep every converter happy.
        self._sw = max(width, int(round(sw * scale / 2)) * 2)
        self._sh = max(height, int(round(sh * scale / 2)) * 2)
        num, den = _fraction(fps)
        self.pipeline = Gst.parse_launch(
            "uridecodebin3 name=src ! videoconvert ! videoscale ! videorate ! "
            f"video/x-raw,format=RGB,width={self._sw},height={self._sh},pixel-aspect-ratio=1/1,framerate={num}/{den} ! "
            "appsink name=sink sync=false max-buffers=64 emit-signals=false"
        )
        self.pipeline.get_by_name("src").set_property("uri", uri(path))
        self.sink: GstApp.AppSink = self.pipeline.get_by_name("sink")
        self.pipeline.set_state(Gst.State.PAUSED)
        try:
            _wait_state(self.pipeline, self.path)
        except MediaError:
            self.close()
            raise

    def segment(self, start: float, duration: float) -> np.ndarray:
        """Frames from start for duration seconds: uint8 array (frames, height, width, 3)."""
        count = max(1, int(round(duration * self.fps)))
        stop = min(self.duration, start + duration + 2.0 / self.fps)
        ok = self.pipeline.seek(
            1.0,
            Gst.Format.TIME,
            Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE,
            Gst.SeekType.SET,
            int(start * SECOND),
            Gst.SeekType.SET,
            int(stop * SECOND),
        )
        if not ok:
            raise MediaError(f"{self.path}: seek to {start:.2f}s failed")
        self.pipeline.set_state(Gst.State.PLAYING)
        frames = []
        x0 = (self._sw - self.width) // 2
        y0 = (self._sh - self.height) // 2
        while len(frames) < count:
            sample = self.sink.try_pull_sample(10 * SECOND)
            if sample is None:
                _check_bus(self.pipeline, self.path)
                break
            buf = sample.get_buffer()
            ok, info = buf.map(Gst.MapFlags.READ)
            if not ok:
                raise MediaError(f"{self.path}: could not read a frame")
            try:
                # Rows are padded to 4 bytes.
                stride = (self._sw * 3 + 3) & ~3
                arr = np.frombuffer(info.data, np.uint8, count=stride * self._sh).reshape(self._sh, stride)
                frame = arr[:, : self._sw * 3].reshape(self._sh, self._sw, 3)
                frames.append(frame[y0 : y0 + self.height, x0 : x0 + self.width].copy())
            finally:
                buf.unmap(info)
        self.pipeline.set_state(Gst.State.PAUSED)
        if not frames:
            raise MediaError(f"{self.path}: no frames at {start:.2f}s")
        while len(frames) < count:
            frames.append(frames[-1])
        return np.stack(frames)

    def close(self) -> None:
        self.pipeline.set_state(Gst.State.NULL)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _fraction(fps: float) -> tuple[int, int]:
    from fractions import Fraction

    f = Fraction(fps).limit_denominator(1001)
    return f.numerator, f.denominator


def decode_audio(path: str | os.PathLike, rate: int = 22050, channels: int = 1) -> np.ndarray:
    """The whole file as float32 samples at rate: (samples,) for mono, (samples, channels) otherwise."""
    pipeline = Gst.parse_launch(
        "uridecodebin3 name=src ! audioconvert ! audioresample ! "
        f"audio/x-raw,format=F32LE,channels={channels},rate={rate},layout=interleaved ! "
        "appsink name=sink sync=false emit-signals=false"
    )
    src = pipeline.get_by_name("src")
    src.set_property("uri", uri(path))
    sink = pipeline.get_by_name("sink")
    chunks = []
    try:
        pipeline.set_state(Gst.State.PLAYING)
        while True:
            sample = sink.try_pull_sample(10 * SECOND)
            if sample is None:
                _check_bus(pipeline, str(path))
                break
            buf = sample.get_buffer()
            chunks.append(np.frombuffer(buf.extract_dup(0, buf.get_size()), np.float32))
    finally:
        pipeline.set_state(Gst.State.NULL)
    if not chunks:
        raise MediaError(f"{path}: no audio")
    samples = np.concatenate(chunks)
    return samples if channels == 1 else samples.reshape(-1, channels)


class AudioPlayer:
    """Plays the soundtrack; position() is the clock the picture follows."""

    def __init__(self, path: str | os.PathLike):
        self.playbin = Gst.ElementFactory.make("playbin", None)
        if self.playbin is None:
            raise MediaError("GStreamer playbin is missing")
        self.playbin.set_property("uri", uri(path))
        # Audio only: no video window.
        self.playbin.set_property("flags", 0x2)
        self.playbin.set_state(Gst.State.PAUSED)
        try:
            _wait_state(self.playbin, str(path))
        except MediaError:
            self.close()
            raise
        self.playing = False
        self._last = 0.0

    def play(self) -> None:
        self.playbin.set_state(Gst.State.PLAYING)
        self.playing = True

    def pause(self) -> None:
        self.playbin.set_state(Gst.State.PAUSED)
        self.playing = False

    def toggle(self) -> None:
        self.pause() if self.playing else self.play()

    def seek(self, seconds: float) -> None:
        self.playbin.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE, int(max(0.0, seconds) * SECOND))
        self._last = max(0.0, seconds)

    def position(self) -> float:
        ok, pos = self.playbin.query_position(Gst.Format.TIME)
        if ok and pos >= 0:
            self._last = pos / SECOND
        return self._last

    def finished(self) -> bool:
        msg = self.playbin.get_bus().pop_filtered(Gst.MessageType.EOS | Gst.MessageType.ERROR)
        return msg is not None

    def close(self) -> None:
        self.playbin.set_state(Gst.State.NULL)
