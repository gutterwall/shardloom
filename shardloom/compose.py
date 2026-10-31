"""The finished video: the song section (trimmed), titles over it or on their own cards before and after,
and fades in and out. Program time T runs over the whole video; the song section maps it to song time.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

import moderngl
import numpy as np

from . import shaders
from .sequencer import Project, Title


@dataclass
class Placed:
    index: int          # into project.titles
    title: Title
    t0: float
    t1: float
    card: bool          # on its own time, not over the song

    def alpha(self, t: float) -> float:
        if not self.t0 <= t < self.t1:
            return 0.0
        fade = min(max(0.0, self.title.fade), (self.t1 - self.t0) / 2)
        if fade <= 0:
            return 1.0
        a = min((t - self.t0) / fade, (self.t1 - t) / fade, 1.0)
        return a * a * (3 - 2 * a)


class Program:
    def __init__(self, project: Project, song_duration: float, start: float | None = None, end: float | None = None,
                 titles: bool = True):
        out = project.output
        start = float(out.get("start") or 0.0) if start is None else start
        end = out.get("end") if end is None else end
        end = song_duration if end is None or end <= 0 else min(float(end), song_duration) if song_duration else end
        start = max(0.0, min(start, end))
        if end <= start:
            raise ValueError("nothing to render: the song section is empty")
        self.song_start, self.song_end = start, end
        self.fade_in = max(0.0, float(out.get("fade_in") or 0.0))
        self.fade_out = max(0.0, float(out.get("fade_out") or 0.0))
        length = end - start
        items = list(enumerate(project.titles)) if titles else []
        pre = [(i, t) for i, t in items if t.at == "start" and t.extra]
        over_start = [(i, t) for i, t in items if t.at == "start" and not t.extra]
        over_end = [(i, t) for i, t in items if t.at == "end" and not t.extra]
        post = [(i, t) for i, t in items if t.at == "end" and t.extra]
        self.pre = sum(max(0.1, t.duration) for _, t in pre)
        self.post = sum(max(0.1, t.duration) for _, t in post)
        self.duration = self.pre + length + self.post
        self.placed: list[Placed] = []
        t = 0.0
        for i, title in pre:
            self.placed.append(Placed(i, title, t, t + max(0.1, title.duration), True))
            t += max(0.1, title.duration)
        t = self.pre
        for i, title in over_start:
            d = min(max(0.1, title.duration), self.pre + length - t)
            if d > 0:
                self.placed.append(Placed(i, title, t, t + d, False))
                t += d
        # Titles at the end finish with the song, the last one last.
        t = self.pre + length
        for i, title in reversed(over_end):
            d = min(max(0.1, title.duration), t - self.pre)
            if d > 0:
                self.placed.append(Placed(i, title, t - d, t, False))
                t -= d
        t = self.pre + length
        for i, title in post:
            self.placed.append(Placed(i, title, t, t + max(0.1, title.duration), True))
            t += max(0.1, title.duration)

    @classmethod
    def plain(cls, start: float, end: float) -> "Program":
        p = Project()
        p.output = {"start": start, "end": end, "fade_in": 0.0, "fade_out": 0.0}
        return cls(p, end, titles=False)

    def song_time(self, T: float) -> float | None:
        """The song time shown at program time T, or None on a card before or after the song."""
        if self.pre <= T < self.pre + (self.song_end - self.song_start):
            return self.song_start + T - self.pre
        return None

    def program_time(self, song_t: float) -> float:
        return self.pre + song_t - self.song_start

    def titles_at(self, T: float) -> list[tuple[Placed, float]]:
        return [(p, a) for p in self.placed if (a := p.alpha(T)) > 0]

    def fade(self, T: float) -> float:
        """Picture brightness for the fades at the very start and end."""
        f = 1.0
        if self.fade_in > 0:
            f = min(f, T / self.fade_in)
        if self.fade_out > 0:
            f = min(f, (self.duration - T) / self.fade_out)
        return float(np.clip(f, 0.0, 1.0))

    def audio(self, song: np.ndarray, rate: int) -> np.ndarray:
        """The soundtrack for the whole program from the song's samples (n, channels): the section, faded,
        with silence under the cards."""
        a0, a1 = int(round(self.song_start * rate)), int(round(self.song_end * rate))
        section = song[a0:a1]
        if len(section) < a1 - a0:
            section = np.concatenate([section, np.zeros((a1 - a0 - len(section),) + song.shape[1:], np.float32)])
        fi, fo = int(self.fade_in * rate), int(self.fade_out * rate)
        if fi or fo:
            section = section.copy()
            if fi:
                n = min(fi, len(section))
                section[:n] *= np.linspace(0.0, 1.0, n, dtype=np.float32).reshape((-1,) + (1,) * (song.ndim - 1))
            if fo:
                n = min(fo, len(section))
                section[len(section) - n:] *= np.linspace(1.0, 0.0, n, dtype=np.float32).reshape((-1,) + (1,) * (song.ndim - 1))
        pre = np.zeros((int(round(self.pre * rate)),) + song.shape[1:], np.float32)
        post = np.zeros((int(round(self.post * rate)),) + song.shape[1:], np.float32)
        if not len(pre) and not len(post):
            return section
        return np.concatenate([pre, section, post])


class Compositor:
    """Draws the sequencer's picture, the titles over it and the fade into a picture of the output size."""

    def __init__(self, ctx: moderngl.Context, width: int, height: int, directory: str | os.PathLike = "."):
        self.ctx, self.width, self.height, self.dir = ctx, width, height, directory
        self.prog = ctx.program(vertex_shader=shaders.VERTEX, fragment_shader=shaders.OVERLAY)
        quad = ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype="f4").tobytes())
        self.vao = ctx.vertex_array(self.prog, [(quad, "2f", "pos")])
        self._quad = quad
        self.targets = []
        for _ in range(2):
            tex = ctx.texture((width, height), 3)
            tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            self.targets.append((tex, ctx.framebuffer([tex])))
        self.black = ctx.texture((1, 1), 4, bytes(4))
        self._titles: dict[str, moderngl.Texture] = {}
        self._out = 0

    def title_texture(self, title: Title, card: bool) -> moderngl.Texture:
        from .titles import render_title

        key = json.dumps([asdict(title), card])
        tex = self._titles.get(key)
        if tex is None:
            if len(self._titles) > 24:
                for t in self._titles.values():
                    t.release()
                self._titles.clear()
            img = render_title(title, self.width, self.height, self.dir, card)
            tex = self.ctx.texture((self.width, self.height), 4, np.ascontiguousarray(img).tobytes())
            tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            self._titles[key] = tex
        return tex

    def _pass(self, base, layer, alpha: float, fade: float, flip_layer: bool) -> moderngl.Texture:
        self._out ^= 1
        tex, fbo = self.targets[self._out]
        fbo.use()
        base.use(0)
        layer.use(1)
        self.prog["base"].value = 0
        self.prog["layer"].value = 1
        self.prog["alpha"].value = float(alpha)
        self.prog["fade"].value = float(fade)
        self.prog["flip_layer"].value = flip_layer
        self.vao.render(moderngl.TRIANGLE_STRIP)
        return tex

    def render(self, picture: moderngl.Texture | None, titles: list[tuple[Placed, float]], fade: float) -> moderngl.Texture:
        """picture: the sequencer's output (None for black)."""
        self.ctx.disable(moderngl.BLEND | moderngl.DEPTH_TEST)
        base = picture if picture is not None else self.black
        if not titles:
            return self._pass(base, self.black, 0.0, fade, False)
        for k, (placed, alpha) in enumerate(titles):
            last = k == len(titles) - 1
            base = self._pass(base, self.title_texture(placed.title, placed.card), alpha, fade if last else 1.0, True)
        return base

    @property
    def output(self) -> moderngl.Texture:
        return self.targets[self._out][0]

    def read(self) -> np.ndarray:
        data = self.targets[self._out][1].read(components=3, alignment=1)
        return np.flipud(np.frombuffer(data, np.uint8).reshape(self.height, self.width, 3))


class Composer:
    """Engine + renderer + compositor: the finished picture at any program time."""

    def __init__(self, engine, renderer, program: Program, directory: str | os.PathLike = "."):
        self.engine, self.renderer, self.program = engine, renderer, program
        self.compositor = Compositor(renderer.ctx, renderer.width, renderer.height, directory)

    def render(self, T: float) -> moderngl.Texture:
        t = self.program.song_time(T)
        picture = self.renderer.render(self.engine.state(t)) if t is not None else None
        return self.compositor.render(picture, self.program.titles_at(T), self.program.fade(T))

    def read(self) -> np.ndarray:
        return self.compositor.read()
