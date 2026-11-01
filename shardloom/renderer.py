"""Draws a FrameState with OpenGL (moderngl): lanes through the effects, blended, then the master pass."""

from __future__ import annotations

import sys

import numpy as np
import moderngl

from . import events as eventlib
from . import fx as fxlib
from . import shaders
from .sequencer import FrameState


def headless_context() -> moderngl.Context:
    """An offscreen OpenGL 3.3 context, for export and tests: EGL on Linux (no display needed),
    the platform default elsewhere."""
    if sys.platform.startswith("linux"):
        try:
            return moderngl.create_standalone_context(require=330, backend="egl")
        except Exception:
            pass
    return moderngl.create_standalone_context(require=330)


class Renderer:
    def __init__(self, ctx: moderngl.Context, width: int, height: int, frames):
        """frames: an object with frame(shard_id, index) -> uint8 (h, w, 3), such as slicer.PoolFrames."""
        self.ctx = ctx
        self.width, self.height = width, height
        self.frames = frames
        quad = ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype="f4").tobytes())
        self.effects = ctx.program(vertex_shader=shaders.VERTEX, fragment_shader=shaders.EFFECTS)
        self.blend = ctx.program(vertex_shader=shaders.VERTEX, fragment_shader=shaders.BLEND)
        self.master = ctx.program(vertex_shader=shaders.VERTEX, fragment_shader=shaders.MASTER)
        self.events = ctx.program(vertex_shader=shaders.VERTEX, fragment_shader=shaders.EVENTS)
        self.vao = {name: ctx.vertex_array(prog, [(quad, "2f", "pos")])
                    for name, prog in (("effects", self.effects), ("blend", self.blend), ("master", self.master),
                                       ("events", self.events))}
        self._quad = quad

        def target():
            tex = ctx.texture((width, height), 3)
            tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            return tex, ctx.framebuffer([tex])

        self.layer = target()          # one lane after its effects
        self.accum = [target(), target()]
        self.mastered = target()       # the blend after the master effects
        self.struck = target()         # ... and after the shard effects
        self.out = [target(), target()]  # final, ping-pong for trails
        self._out = 0
        self.inputs: list[moderngl.Texture] = []
        self._uploaded: list[tuple[int, int] | None] = []
        self.black = ctx.texture((1, 1), 3, bytes(3))
        self.frame_count = 0

    # ---- helpers -------------------------------------------------------

    def _input(self, lane: int) -> moderngl.Texture:
        while len(self.inputs) <= lane:
            fw = getattr(self.frames, "width", self.width)
            fh = getattr(self.frames, "height", self.height)
            tex = self.ctx.texture((fw, fh), 3)
            tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            self.inputs.append(tex)
            self._uploaded.append(None)
        return self.inputs[lane]

    def _crop(self, source: moderngl.Texture) -> tuple[float, float]:
        """Scale of the source's texture coordinates so it covers the output without stretching."""
        src = source.width / source.height
        out = self.width / self.height
        return (out / src, 1.0) if out < src else (1.0, src / out)

    def _effects(self, source: moderngl.Texture, fbo: moderngl.Framebuffer, amounts: dict, state: FrameState,
                 flip: bool, seed: float) -> None:
        prog = self.effects
        fbo.use()
        source.use(0)
        prog["tex"].value = 0
        prog["crop"].value = self._crop(source)
        prog["res"].value = (float(self.width), float(self.height))
        prog["time"].value = float(state.t)
        prog["seed"].value = float(seed)
        prog["flip"].value = flip
        for i, colour in enumerate(state.palette[:3]):
            prog[f"pal{i}"].value = fxlib.hex_rgb(colour)
        for name in fxlib.FX:
            prog[name].value = float(amounts.get(name, 0.0))
        self.vao["effects"].render(moderngl.TRIANGLE_STRIP)

    def _shard_effects(self, source: moderngl.Texture, previous: moderngl.Texture, state: FrameState) -> None:
        prog = self.events
        self.struck[1].use()
        source.use(0)
        previous.use(1)
        prog["tex"].value = 0
        prog["prev"].value = 1
        prog["res"].value = (float(self.width), float(self.height))
        prog["time"].value = float(state.t)
        hits = [state.events.get(name, (0.0, 0.0, 0.0)) for name in eventlib.NAMES]
        for k, name in enumerate(("amt", "age", "sd")):
            # Drivers may drop the end of an array the shader never reads, so write only what is there.
            u = prog.get(name, None)
            if u is not None:
                vals = [float(h[k]) for h in hits][:u.array_length]
                u.value = vals if len(vals) > 1 else vals[0]
        self.vao["events"].render(moderngl.TRIANGLE_STRIP)

    # ---- drawing -------------------------------------------------------

    def render(self, state: FrameState) -> moderngl.Texture:
        ctx = self.ctx
        ctx.disable(moderngl.BLEND | moderngl.DEPTH_TEST)
        a, b = self.accum
        a[1].use()
        ctx.clear(0.0, 0.0, 0.0)
        for li, lane in enumerate(state.lanes):
            if lane.voice is None or lane.opacity <= 0.0:
                continue
            tex = self._input(li)
            key = (lane.voice.shard, lane.frame)
            if self._uploaded[li] != key:
                frame = np.ascontiguousarray(self.frames.frame(lane.voice.shard, lane.frame))
                tex.write(frame.tobytes())
                self._uploaded[li] = key
            self._effects(tex, self.layer[1], lane.fx, state, True, seed=li * 17.0 + lane.voice.step)
            # Blend the lane over what is there so far.
            b[1].use()
            a[0].use(0)
            self.layer[0].use(1)
            self.blend["base"].value = 0
            self.blend["layer"].value = 1
            mode = fxlib.BLEND_MODES.index(lane.blend) if lane.blend in fxlib.BLEND_MODES else 0
            self.blend["mode"].value = mode
            self.blend["opacity"].value = float(lane.opacity)
            self.vao["blend"].render(moderngl.TRIANGLE_STRIP)
            a, b = b, a
        self._effects(a[0], self.mastered[1], state.master, state, False, seed=state.step)
        prev = self.out[self._out]
        picture = self.mastered[0]
        if state.events:
            self._shard_effects(picture, prev[0], state)
            picture = self.struck[0]
        self._out ^= 1
        cur = self.out[self._out]
        cur[1].use()
        picture.use(0)
        prev[0].use(1)
        m = self.master
        m["current"].value = 0
        m["previous"].value = 1
        m["trails"].value = float(state.master.get("trails", 0.0))
        m["flash"].value = float(state.master.get("flash", 0.0))
        m["strobe"].value = float(state.master.get("strobe", 0.0))
        steps = state.music.get("beat_phase", 0.0) * 4.0
        m["step_phase"].value = float(steps - np.floor(steps))
        self.vao["master"].render(moderngl.TRIANGLE_STRIP)
        self.frame_count += 1
        return cur[0]

    @property
    def output(self) -> moderngl.Texture:
        return self.out[self._out][0]

    def read(self) -> np.ndarray:
        """The last rendered picture: uint8 (height, width, 3), top row first."""
        data = self.out[self._out][1].read(components=3, alignment=1)
        return np.flipud(np.frombuffer(data, np.uint8).reshape(self.height, self.width, 3))
