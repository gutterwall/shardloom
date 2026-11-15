"""Media through FFmpeg (PyAV) and miniaudio: probe, decode video segments to frames, decode audio to
samples, play the soundtrack, and write the exported MP4. The same code runs on Linux and Windows.
"""

from __future__ import annotations

import os
import threading
import time
from fractions import Fraction

import av
import numpy as np


class MediaError(RuntimeError):
    pass


def _open(path):
    try:
        return av.open(str(path))
    except (av.error.FFmpegError, OSError) as e:
        raise MediaError(f"{path}: {e}") from e


def probe(path: str | os.PathLike) -> dict:
    """Duration (seconds) and, for video, width, height and frame rate."""
    with _open(path) as c:
        duration = c.duration / av.time_base if c.duration else 0.0
        out = {"duration": duration, "video": None, "audio": bool(c.streams.audio)}
        if c.streams.video:
            v = c.streams.video[0]
            rate = v.average_rate or v.guessed_rate or 25
            if not duration and v.duration and v.time_base:
                out["duration"] = float(v.duration * v.time_base)
            out["video"] = {"width": v.codec_context.width, "height": v.codec_context.height, "fps": float(rate)}
    return out


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
        self.container = _open(path)
        self.stream = self.container.streams.video[0]
        self.stream.thread_type = "AUTO"

    def _rgb(self, frame) -> np.ndarray:
        img = frame.reformat(width=self._sw, height=self._sh, format="rgb24").to_ndarray()
        x0 = (self._sw - self.width) // 2
        y0 = (self._sh - self.height) // 2
        return np.ascontiguousarray(img[y0:y0 + self.height, x0:x0 + self.width])

    def segment(self, start: float, duration: float) -> np.ndarray:
        """Frames from start for duration seconds: uint8 array (frames, height, width, 3).

        For short segments only: it holds them all. iter_segment() gives them one at a time."""
        return np.stack(list(self.iter_segment(start, duration)))

    def segment_length(self, duration: float) -> int:
        """How many frames iter_segment() gives for duration seconds."""
        return max(1, int(round(duration * self.fps)))

    def iter_segment(self, start: float, duration: float):
        """The frames of segment(), one at a time (uint8, height x width x 3), holding only the current one.

        A frame shown more than once (a source slower than the project) comes as the same array again."""
        count = self.segment_length(duration)
        tb = self.stream.time_base
        try:
            self.container.seek(int(start / tb), stream=self.stream, backward=True, any_frame=False)
        except av.error.FFmpegError as e:
            raise MediaError(f"{self.path}: seek to {start:.2f}s failed: {e}") from e
        # Sample the source at the project frame rate: each output frame shows the
        # latest source frame at or before its time.
        targets = [start + k / self.fps for k in range(count)]
        eps = 0.25 / self.fps
        given = 0
        prev = None
        shown = (None, None)          # the last source frame given, and its RGB
        decoded = self.container.decode(self.stream)

        def rgb(frame):
            nonlocal shown
            if shown[0] is not frame:
                shown = (frame, self._rgb(frame))
            return shown[1]

        while given < count:
            try:
                frame = next(decoded, None)
            except av.error.FFmpegError as e:
                raise MediaError(f"{self.path}: {e}") from e
            if frame is None:
                break
            if frame.pts is None:
                continue
            t = float(frame.pts * tb)
            # Every target this frame comes after shows the frame before it.
            while given < count and t > targets[given] + eps:
                yield rgb(prev if prev is not None else frame)
                given += 1
            if given >= count:
                break
            prev = frame
        if prev is None and given == 0:
            raise MediaError(f"{self.path}: no frames at {start:.2f}s")
        while given < count:
            yield rgb(prev if prev is not None else shown[0])
            given += 1

    def close(self) -> None:
        self.container.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class Decoders:
    """VideoDecoders for several videos, with only one open at a time.

    A decoder that has been used holds about 100 MB of frames (for 1080p), so a pool cut from dozens of
    videos with all of them open runs out of memory. Each file is checked and its length read up front."""

    def __init__(self, paths, width: int, height: int, fps: float):
        self.paths = [str(p) for p in paths]
        self.size = (width, height, fps)
        self.durations = []
        for p in self.paths:
            with VideoDecoder(p, width, height, fps) as d:
                self.durations.append(d.duration)
        self._open: tuple[int, VideoDecoder] | None = None

    def __getitem__(self, i: int) -> VideoDecoder:
        if self._open is None or self._open[0] != i:
            self.close()
            self._open = (i, VideoDecoder(self.paths[i], *self.size))
        return self._open[1]

    def close(self) -> None:
        if self._open is not None:
            self._open[1].close()
            self._open = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _fraction(fps: float) -> tuple[int, int]:
    f = Fraction(fps).limit_denominator(1001)
    return f.numerator, f.denominator


def decode_audio(path: str | os.PathLike, rate: int = 22050, channels: int = 1) -> np.ndarray:
    """The whole file as float32 samples at rate: (samples,) for mono, (samples, channels) otherwise."""
    layout = "mono" if channels == 1 else "stereo"
    chunks = []
    with _open(path) as c:
        if not c.streams.audio:
            raise MediaError(f"{path}: no audio")
        resampler = av.AudioResampler(format="flt", layout=layout, rate=rate)
        try:
            for frame in c.decode(c.streams.audio[0]):
                for out in resampler.resample(frame):
                    chunks.append(out.to_ndarray().reshape(-1))
            for out in resampler.resample(None):
                chunks.append(out.to_ndarray().reshape(-1))
        except av.error.FFmpegError as e:
            raise MediaError(f"{path}: {e}") from e
    if not chunks:
        raise MediaError(f"{path}: no audio")
    samples = np.concatenate(chunks).astype(np.float32)
    return samples if channels == 1 else samples.reshape(-1, channels)


class AudioPlayer:
    """Plays the soundtrack; position() is the clock the picture follows.

    The song is decoded once; the position counts the samples the sound card
    has taken, less its buffer, and runs smoothly between buffers.
    """

    RATE = 48000
    BUFFER_MS = 30

    def __init__(self, path: str | os.PathLike | None = None, samples: np.ndarray | None = None):
        """The song at path, or samples already decoded at RATE: float32 (n, 2)."""
        import miniaudio

        self._ma = miniaudio
        self.samples = samples if samples is not None else decode_audio(path, self.RATE, 2)
        self.duration = len(self.samples) / self.RATE
        self.playing = False
        self._lock = threading.Lock()
        self._pos = 0            # next sample to hand to the device
        self._stamp = (0, time.monotonic())
        self.device = None
        self._ended = False

    def _stream(self):
        frames = yield b""
        while True:
            with self._lock:
                start = self._pos
                chunk = self.samples[start:start + frames]
                self._pos = start + len(chunk)
                self._stamp = (start, time.monotonic())
            if len(chunk) < frames:
                self._ended = True
                chunk = np.concatenate([chunk, np.zeros((frames - len(chunk), 2), np.float32)])
            frames = yield chunk.astype(np.float32).tobytes()

    def play(self) -> None:
        if self.playing:
            return
        if self._pos >= len(self.samples):
            self._pos = 0
        self._ended = False
        ma = self._ma
        try:
            self.device = ma.PlaybackDevice(output_format=ma.SampleFormat.FLOAT32, nchannels=2,
                                            sample_rate=self.RATE, buffersize_msec=self.BUFFER_MS)
            gen = self._stream()
            next(gen)
            self.device.start(gen)
        except ma.MiniaudioError as e:
            self.device = None
            raise MediaError(f"no sound output: {e}") from e
        with self._lock:
            self._stamp = (self._pos, time.monotonic())
        self.playing = True

    def pause(self) -> None:
        if not self.playing:
            return
        pos = self.position()
        if self.device:
            self.device.close()
            self.device = None
        self.playing = False
        with self._lock:
            self._pos = int(pos * self.RATE)
            self._stamp = (self._pos, time.monotonic())

    def toggle(self) -> None:
        self.pause() if self.playing else self.play()

    def seek(self, seconds: float) -> None:
        with self._lock:
            self._pos = int(max(0.0, min(seconds, self.duration)) * self.RATE)
            self._stamp = (self._pos, time.monotonic())
            self._ended = False

    def position(self) -> float:
        with self._lock:
            pos, stamp = self._stamp
        if not self.playing:
            return pos / self.RATE
        # The samples handed over at stamp start sounding after the buffer.
        t = pos / self.RATE + (time.monotonic() - stamp) - self.BUFFER_MS / 1000.0
        return max(0.0, min(t, self.duration))

    def finished(self) -> bool:
        return self._ended and self.position() >= self.duration - 0.05

    def close(self) -> None:
        if self.device:
            self.device.close()
            self.device = None
        self.playing = False


def _video_codec() -> str:
    for name in ("libx264", "h264", "libopenh264", "mpeg4"):
        try:
            av.Codec(name, "w")
            return name
        except Exception:
            continue
    raise MediaError("no H.264 or MPEG-4 encoder in this FFmpeg")


# Shards are stored as H.264 with every frame a keyframe: 50-70x smaller than raw frames, and any frame
# decodes on its own, so shards still play from any frame, at any speed, backwards. Four slices a frame let
# four threads decode it (about 2 ms for 720p). Fixed encoder threads keep the files the same on every machine.
SHARD_X264 = {"crf": "20", "g": "1", "bf": "0", "preset": "veryfast", "threads": "4", "slices": "4"}


class ShardWriter:
    """Writes a shard's RGB frames, one at a time, to a small video file (H.264, every frame a keyframe)."""

    def __init__(self, path: str | os.PathLike, width: int, height: int, fps: float):
        self.container = av.open(str(path), "w", format="mp4")
        num, den = _fraction(fps)
        try:
            av.Codec("libx264", "w")
            codec, options = "libx264", SHARD_X264
        except Exception:
            # An FFmpeg without x264: MPEG-4 Part 2, still every frame a keyframe.
            codec, options = "mpeg4", {"g": "1", "qscale": "3"}
        self.video = self.container.add_stream(codec, rate=Fraction(num, den))
        self.video.width, self.video.height = width, height
        self.video.pix_fmt = "yuv420p"
        self.video.options = dict(options)
        if codec == "mpeg4":
            self.video.codec_context.qscale = 3
        self.frames = 0

    def write(self, rgb: np.ndarray) -> None:
        frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
        frame.pts = self.frames
        self.frames += 1
        for packet in self.video.encode(frame):
            self.container.mux(packet)

    def close(self) -> None:
        if self.container is None:
            return
        try:
            for packet in self.video.encode(None):
                self.container.mux(packet)
        finally:
            self.container.close()
            self.container = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class ShardClip:
    """A shard written by ShardWriter, held compressed in memory; frame(i) decodes any one frame.

    The file is read once and closed, so it can be replaced while the clip is in use."""

    def __init__(self, path: str | os.PathLike):
        with _open(path) as c:
            if not c.streams.video:
                raise MediaError(f"{path}: no video stream")
            stream = c.streams.video[0]
            try:
                self.packets = [bytes(p) for p in c.demux(stream) if p.size]
            except av.error.FFmpegError as e:
                raise MediaError(f"{path}: {e}") from e
            self._ctx = av.CodecContext.create(stream.codec_context.name, "r")
            self._ctx.extradata = stream.codec_context.extradata
        self._ctx.thread_type = "SLICE"
        self._ctx.thread_count = 4
        self.path = str(path)
        self.nbytes = sum(map(len, self.packets))

    def __len__(self) -> int:
        return len(self.packets)

    def frame(self, index: int) -> np.ndarray:
        """Frame index as uint8 (height, width, 3)."""
        try:
            out = self._ctx.decode(av.Packet(self.packets[index]))
            if not out:
                # A decoder that holds frames back gives this one up when drained.
                out = self._ctx.decode(None)
                self._ctx.flush_buffers()
        except av.error.FFmpegError as e:
            raise MediaError(f"{self.path}: frame {index}: {e}") from e
        if not out:
            raise MediaError(f"{self.path}: frame {index} did not decode")
        return out[-1].to_ndarray(format="rgb24")


class Mp4Writer:
    """An MP4 with H.264 video from RGB frames and AAC audio from float samples."""

    def __init__(self, path: str | os.PathLike, width: int, height: int, fps: float, bitrate: int = 8_000_000,
                 audio_rate: int | None = None, preset: str = "medium"):
        # faststart: the index at the front, so uploads (YouTube, Instagram) can start processing early.
        self.container = av.open(str(path), "w", format="mp4", options={"movflags": "+faststart"})
        num, den = _fraction(fps)
        codec = _video_codec()
        self.video = self.container.add_stream(codec, rate=Fraction(num, den))
        self.video.width, self.video.height = width, height
        self.video.pix_fmt = "yuv420p"
        self.video.bit_rate = bitrate
        if codec == "libx264":
            self.video.options = {"preset": preset, "g": str(int(round(fps * 2)))}
        self.audio = None
        if audio_rate:
            self.audio = self.container.add_stream("aac", rate=audio_rate)
            self.audio.layout = "stereo"
            self.audio.bit_rate = 192000
            self._apts = 0
        self._frames = 0

    def write_video(self, rgb: np.ndarray) -> None:
        frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
        frame.pts = self._frames
        self._frames += 1
        for packet in self.video.encode(frame):
            self.container.mux(packet)

    def write_audio(self, samples: np.ndarray) -> None:
        """samples: float32 (n, 2)."""
        frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(samples.T, dtype=np.float32), format="fltp",
                                           layout="stereo")
        frame.sample_rate = self.audio.rate
        frame.pts = self._apts
        self._apts += len(samples)
        for packet in self.audio.encode(frame):
            self.container.mux(packet)

    def close(self) -> None:
        for packet in self.video.encode(None):
            self.container.mux(packet)
        if self.audio is not None:
            for packet in self.audio.encode(None):
                self.container.mux(packet)
        self.container.close()
