"""Draws a FrameState with OpenGL (moderngl): lanes through the effects, blended, then the master pass."""

from __future__ import annotations

import sys

import numpy as np
import moderngl

from . import events as eventlib
from . import fx as fxlib
from . import shaders
from . import visuals as visuallib
from .sequencer import FrameState

# The One layer shard effects' uniforms in shaders.EFFECTS, besides lens_rgb (events.layer_strikes).
LAYER_UNIFORMS = ("lens", "fold", "fold_style", "fold_segments", "fold_zoom", "fold_turn", "fold_leaves",
                  "fold_seed")


def headless_context() -> moderngl.Context:
    """An offscreen OpenGL 3.3 context, for export and tests: EGL on Linux (no display needed),
    the platform default elsewhere."""
    if sys.platform.startswith("linux"):
        try:
            return moderngl.create_standalone_context(require=330, backend="egl")
        except Exception:
            pass
    return moderngl.create_standalone_context(require=330)


def _array(prog: moderngl.Program, name: str, values: list[float]) -> None:
    """Sets a float array uniform. Drivers may drop the end of an array the shader never reads (the visuals'
    warp reads only the first 20 of its 24 preset numbers), so write only what is there."""
    u = prog.get(name, None)
    if u is not None:
        vals = [float(x) for x in values][:u.array_length]
        u.value = vals if len(vals) > 1 else vals[0]


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
        self.visual_warp = ctx.program(vertex_shader=shaders.VERTEX, fragment_shader=shaders.VISUAL_WARP)
        self.visual_comp = ctx.program(vertex_shader=shaders.VERTEX, fragment_shader=shaders.VISUAL_COMP)
        self.vao = {name: ctx.vertex_array(prog, [(quad, "2f", "pos")])
                    for name, prog in (("effects", self.effects), ("blend", self.blend), ("master", self.master),
                                       ("events", self.events), ("visual_warp", self.visual_warp),
                                       ("visual_comp", self.visual_comp))}
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
        # The MilkDrop-style visual: its feedback pictures (made when first needed), which strike they hold and
        # how many steps of it they have taken.
        self.feed: list | None = None
        self._feed = 0
        self._visual_key = None
        self._visual_steps = 0
        self.visual = target()         # the visual, made ready to mix in

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
                 flip: bool, seed: float, strike: dict | None = None) -> None:
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
        strike = strike or {}
        for name in LAYER_UNIFORMS:
            u = prog.get(name, None)
            if u is not None:
                u.value = float(strike.get(name, 0.0))
        u = prog.get("lens_rgb", None)
        if u is not None:
            u.value = (float(strike.get("lens_r", 0.0)), float(strike.get("lens_g", 0.0)), float(strike.get("lens_b", 0.0)))
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
        hits = [state.events.get(name, (0.0, 0.0, 0.0)) for name in eventlib.PICTURE]
        for k, name in enumerate(("amt", "age", "sd")):
            # Drivers may drop the end of an array the shader never reads, so write only what is there.
            u = prog.get(name, None)
            if u is not None:
                vals = [float(h[k]) for h in hits][:u.array_length]
                u.value = vals if len(vals) > 1 else vals[0]
        self.vao["events"].render(moderngl.TRIANGLE_STRIP)

    def _visual(self, v: dict) -> None:
        """Steps the visual's feedback loop up to this frame (from the strike's start when it is new or the
        frame is earlier than the last), then draws it at the output's size into self.visual."""
        ctx = self.ctx
        if self.feed is None:
            fw, fh = visuallib.feed_size(self.width, self.height)
            self.feed = []
            for _ in range(2):
                tex = ctx.texture((fw, fh), 3)
                tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
                tex.repeat_x = tex.repeat_y = False
                self.feed.append((tex, ctx.framebuffer([tex])))
        pre = visuallib.pack(v["preset"])
        key = (v["seed"], v["preset"])
        if key != self._visual_key or v["steps"] + 1 < self._visual_steps:
            self._visual_key, self._visual_steps = key, 0
        w = self.visual_warp
        fw, fh = self.feed[0][0].size
        _array(w, "pre", pre)
        w["res"].value = (float(fw), float(fh))
        w["vseed"].value = float(v["seed"])
        u = w.get("prev", None)
        if u is not None:
            u.value = 0
        music = v["music"]
        while self._visual_steps <= v["steps"]:
            k = self._visual_steps
            src = self.feed[self._feed][0]
            self._feed ^= 1
            self.feed[self._feed][1].use()
            src.use(0)
            heard = music[min(k, len(music) - 1)]
            w["first"].value = k == 0
            w["tau"].value = k / visuallib.RATE
            w["music"].value = tuple(float(x) for x in heard[:4])
            w["beat"].value = float(heard[4])
            self.vao["visual_warp"].render(moderngl.TRIANGLE_STRIP)
            self._visual_steps += 1
        c = self.visual_comp
        self.visual[1].use()
        self.feed[self._feed][0].use(0)
        c["feed"].value = 0
        c["res"].value = (float(self.width), float(self.height))
        u = c.get("tau", None)
        if u is not None:
            u.value = v["steps"] / visuallib.RATE
        _array(c, "pre", pre)
        self.vao["visual_comp"].render(moderngl.TRIANGLE_STRIP)

    def _blend(self, base: moderngl.Texture, layer: moderngl.Texture, fbo: moderngl.Framebuffer, mode: int,
               opacity: float) -> None:
        fbo.use()
        base.use(0)
        layer.use(1)
        self.blend["base"].value = 0
        self.blend["layer"].value = 1
        self.blend["mode"].value = mode
        self.blend["opacity"].value = float(opacity)
        self.vao["blend"].render(moderngl.TRIANGLE_STRIP)

    # ---- drawing -------------------------------------------------------

    def render(self, state: FrameState) -> moderngl.Texture:
        ctx = self.ctx
        ctx.disable(moderngl.BLEND | moderngl.DEPTH_TEST)
        a, b = self.accum
        a[1].use()
        ctx.clear(0.0, 0.0, 0.0)
        v = state.visual
        if v is not None:
            self._visual(v)
            v_mode = visuallib.BLENDS[v["blend"]][2]
        for li, lane in enumerate(state.lanes):
            if v is not None and li > 0 and v["after"] == li - 1 and v["blend"] != "behind":
                # The visual goes over the layers below this one.
                self._blend(a[0], self.visual[0], b[1], v_mode, v["amount"])
                a, b = b, a
            if lane.voice is None or lane.opacity <= 0.0:
                continue
            tex = self._input(li)
            key = (lane.voice.shard, lane.frame)
            if self._uploaded[li] != key:
                frame = np.ascontiguousarray(self.frames.frame(lane.voice.shard, lane.frame))
                tex.write(frame.tobytes())
                self._uploaded[li] = key
            self._effects(tex, self.layer[1], lane.fx, state, True, seed=li * 17.0 + lane.voice.step,
                          strike=lane.strike)
            # Blend the lane over what is there so far.
            mode = fxlib.BLEND_MODES.index(lane.blend) if lane.blend in fxlib.BLEND_MODES else 0
            self._blend(a[0], self.layer[0], b[1], mode, lane.opacity)
            a, b = b, a
        if v is not None and (v["blend"] == "behind" or v["after"] >= len(state.lanes) - 1 or v["after"] < 0):
            # Over the top layer, or behind the whole picture.
            self._blend(a[0], self.visual[0], b[1], v_mode, v["amount"])
            a, b = b, a
        self._effects(a[0], self.mastered[1], state.master, state, False, seed=state.step)
        prev = self.out[self._out]
        picture = self.mastered[0]
        if any(name in state.events for name in eventlib.PICTURE):
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
