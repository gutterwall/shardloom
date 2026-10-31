"""Offline render of a project to an MP4 (H.264 + AAC), frame-exact."""

from __future__ import annotations

import os
import time

import numpy as np

from .media import Gst, MediaError, SECOND, decode_audio

AUDIO_RATE = 44100


def _caps_video(width: int, height: int, fps: float) -> str:
    from .media import _fraction

    num, den = _fraction(fps)
    return f"video/x-raw,format=RGB,width={width},height={height},framerate={num}/{den}"


def export(engine, renderer, out_path: str | os.PathLike, start: float = 0.0, end: float | None = None,
           fps: float | None = None, music: str | None = None, bitrate: int = 8_000_000, progress=None) -> dict:
    """Renders [start, end) seconds of the project to out_path; returns timing figures."""
    project = engine.project
    fps = fps or project.fps
    duration = engine.analysis.duration
    end = duration if end is None else min(end, duration) if duration else end
    if end <= start:
        raise ValueError("nothing to render: end is before start")
    frames = int(round((end - start) * fps))
    w, h = renderer.width, renderer.height
    music = music if music is not None else project.music

    audio = None
    if music:
        pcm = decode_audio(music, AUDIO_RATE, 2)
        a0, a1 = int(start * AUDIO_RATE), int(end * AUDIO_RATE)
        audio = np.ascontiguousarray(pcm[a0:a1])
        if len(audio) < a1 - a0:
            audio = np.concatenate([audio, np.zeros((a1 - a0 - len(audio), 2), np.float32)])

    desc = (
        f"appsrc name=video format=time is-live=false block=true max-bytes={w * h * 3 * 8} caps={_caps_video(w, h, fps)} ! "
        "queue ! videoconvert ! video/x-raw,format=I420 ! "
        f"openh264enc bitrate={bitrate} rate-control=bitrate complexity=high gop-size={int(fps * 2)} ! h264parse ! "
        "queue ! mp4mux name=mux faststart=true ! filesink name=out"
    )
    if audio is not None:
        desc += (
            f" appsrc name=audio format=time is-live=false block=true "
            f"caps=audio/x-raw,format=F32LE,channels=2,rate={AUDIO_RATE},layout=interleaved ! "
            "queue ! audioconvert ! avenc_aac bitrate=192000 ! aacparse ! queue ! mux."
        )
    pipeline = Gst.parse_launch(desc)
    pipeline.get_by_name("out").set_property("location", str(out_path))
    vsrc = pipeline.get_by_name("video")
    asrc = pipeline.get_by_name("audio") if audio is not None else None
    bus = pipeline.get_bus()
    pipeline.set_state(Gst.State.PLAYING)

    def check():
        msg = bus.pop_filtered(Gst.MessageType.ERROR)
        if msg:
            err, _ = msg.parse_error()
            pipeline.set_state(Gst.State.NULL)
            raise MediaError(f"export: {err.message}")

    chunk = 2048
    apos = 0
    started = time.perf_counter()
    engine.reset()
    try:
        for i in range(frames):
            t = start + i / fps
            renderer.render(engine.state(t))
            data = renderer.read().tobytes()
            buf = Gst.Buffer.new_wrapped(data)
            buf.pts = int(i * SECOND / fps)
            buf.duration = int(SECOND / fps)
            if vsrc.emit("push-buffer", buf) != Gst.FlowReturn.OK:
                check()
                raise MediaError("export: the encoder stopped accepting frames")
            # Keep the audio a little ahead of the video so the muxer never waits.
            if asrc is not None:
                want = min(len(audio), int((i + 2) / fps * AUDIO_RATE))
                while apos < want:
                    n = min(chunk, len(audio) - apos)
                    abuf = Gst.Buffer.new_wrapped(audio[apos:apos + n].tobytes())
                    abuf.pts = int(apos * SECOND / AUDIO_RATE)
                    abuf.duration = int(n * SECOND / AUDIO_RATE)
                    asrc.emit("push-buffer", abuf)
                    apos += n
            if i % 30 == 0:
                check()
                if progress:
                    progress(i, frames)
        if asrc is not None:
            while apos < len(audio):
                n = min(chunk, len(audio) - apos)
                abuf = Gst.Buffer.new_wrapped(audio[apos:apos + n].tobytes())
                abuf.pts = int(apos * SECOND / AUDIO_RATE)
                abuf.duration = int(n * SECOND / AUDIO_RATE)
                asrc.emit("push-buffer", abuf)
                apos += n
            asrc.emit("end-of-stream")
        vsrc.emit("end-of-stream")
        msg = bus.timed_pop_filtered(600 * SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
        if msg is None:
            raise MediaError("export: the file was not finished in time")
        if msg.type == Gst.MessageType.ERROR:
            err, _ = msg.parse_error()
            raise MediaError(f"export: {err.message}")
    finally:
        pipeline.set_state(Gst.State.NULL)
    took = time.perf_counter() - started
    if progress:
        progress(frames, frames)
    return {"frames": frames, "seconds": end - start, "took": took, "fps": frames / took if took else 0.0}
