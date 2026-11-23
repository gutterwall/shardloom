"""The trim editor: a window that shows one video and the parts of it shards are cut from.

The video plays (without sound) and scrubs in the window, a strip of its frames runs under it, and the parts in use
are marked on the strip. Drag a part's ends, mark the playhead as a start (I) or a stop (O), add parts and delete
them. The parts are kept in the video's Source (Source.set_spans) as they change, so Cut shards cuts from them.

Decoding runs on two threads, one for the picture at the playhead and one for the strip, so the window never waits
for the video.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import av
import numpy as np

from .media import MediaError, probe

MIN_PART = 0.1          # the shortest part, in seconds
NEW_PART = 5.0          # how long a new part is


# ---- editing the parts ----------------------------------------------------
#
# Parts are kept as a sorted list of [start, end] that never overlap: every edit clamps to the neighbours instead of
# merging, so the selected part stays the selected part.

def _containing(spans: list[list[float]], t: float) -> int | None:
    for i, (a, b) in enumerate(spans):
        if a <= t <= b:
            return i
    return None


def _gap(spans: list[list[float]], t: float, duration: float) -> tuple[float, float]:
    """The free time around t (between the parts before and after it)."""
    lo, hi = 0.0, duration
    for a, b in spans:
        if b <= t:
            lo = max(lo, b)
        elif a > t:
            hi = min(hi, a)
    return lo, hi


def _insert(spans: list[list[float]], a: float, b: float) -> int:
    spans.append([a, b])
    spans.sort()
    return next(i for i, s in enumerate(spans) if s[0] == a and s[1] == b)


def add_part(spans: list[list[float]], t: float, duration: float, length: float = NEW_PART) -> int | None:
    """A new part from t (up to length seconds, as far as the next part); the part already at t if there is one.
    Returns its index, or None when there is no room."""
    t = min(max(0.0, t), duration)
    i = _containing(spans, t)
    if i is not None:
        return i
    lo, hi = _gap(spans, t, duration)
    a, b = t, min(t + length, hi)
    if b - a < MIN_PART:
        a = max(lo, b - length)
    if b - a < MIN_PART:
        return None
    return _insert(spans, round(a, 3), round(b, 3))


def mark_in(spans: list[list[float]], sel: int | None, t: float, duration: float) -> int | None:
    """The playhead becomes a start: of the part it is in, or of the selected part when it is in the free time just
    before it; otherwise a new part starts there. Returns the part's index."""
    t = round(min(max(0.0, t), duration), 3)
    i = _containing(spans, t)
    if i is None and sel is not None and sel < len(spans):
        lo = spans[sel - 1][1] if sel > 0 else 0.0
        if lo <= t < spans[sel][0]:
            i = sel
    if i is None:
        return add_part(spans, t, duration)
    spans[i][0] = min(t, round(spans[i][1] - MIN_PART, 3))
    return i


def mark_out(spans: list[list[float]], sel: int | None, t: float, duration: float) -> int | None:
    """The playhead becomes a stop: of the part it is in, or of the selected part when it is in the free time just
    after it; otherwise a new part ends there. Returns the part's index."""
    t = round(min(max(0.0, t), duration), 3)
    i = _containing(spans, t)
    if i is None and sel is not None and sel < len(spans):
        hi = spans[sel + 1][0] if sel + 1 < len(spans) else duration
        if spans[sel][1] < t <= hi:
            i = sel
    if i is None:
        lo, _ = _gap(spans, t, duration)
        a = max(lo, t - NEW_PART)
        if t - a < MIN_PART:
            return None
        return _insert(spans, round(a, 3), t)
    spans[i][1] = max(t, round(spans[i][0] + MIN_PART, 3))
    return i


def drag_end(spans: list[list[float]], i: int, side: int, t: float, duration: float) -> None:
    """Moves the start (side 0) or the stop (side 1) of part i to t, between its neighbours."""
    a, b = spans[i]
    if side == 0:
        lo = spans[i - 1][1] if i > 0 else 0.0
        spans[i][0] = round(min(max(t, lo), b - MIN_PART), 3)
    else:
        hi = spans[i + 1][0] if i + 1 < len(spans) else duration
        spans[i][1] = round(max(min(t, hi), a + MIN_PART), 3)


# ---- decoding ---------------------------------------------------------------

def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


class FramePlayer:
    """Decodes one video's frames on a thread: the frame at the time asked for (seek), or one after another in
    real time (play). take() gives the newest frame, once."""

    def __init__(self, path: str | Path, max_width: int = 960, max_height: int = 540):
        self.path = str(path)
        info = probe(path)
        if not info["video"]:
            raise MediaError(f"{path}: no video stream")
        v = info["video"]
        scale = min(max_width / v["width"], max_height / v["height"], 1.0)
        self.width, self.height = _even(v["width"] * scale), _even(v["height"] * scale)
        self.duration = float(info["duration"])
        self.fps = float(v["fps"] or 25.0)
        self.position = 0.0             # the time of the last frame given
        self.error = ""
        self._cv = threading.Condition()
        self._want: float | None = 0.0
        self._playing = False
        self._until: float | None = None
        self._closed = False
        self._frame: tuple[float, np.ndarray] | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    @property
    def playing(self) -> bool:
        return self._playing

    def seek(self, t: float) -> None:
        with self._cv:
            self._want = min(max(0.0, t), self.duration)
            self.position = self._want
            self._cv.notify()

    def play(self, until: float | None = None) -> None:
        with self._cv:
            if self.position >= self.duration - 1.0 / self.fps:
                self._want = 0.0
            self._playing, self._until = True, until
            self._cv.notify()

    def pause(self) -> None:
        with self._cv:
            self._playing = False
            self._cv.notify()

    def take(self) -> tuple[float, np.ndarray] | None:
        with self._cv:
            f, self._frame = self._frame, None
            return f

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._cv.notify()
        self._thread.join(timeout=2.0)

    def _give(self, t: float, img: np.ndarray) -> None:
        with self._cv:
            self._frame = (t, img)
            if self._want is None:
                self.position = t

    def _rgb(self, frame) -> np.ndarray:
        return frame.reformat(width=self.width, height=self.height, format="rgb24").to_ndarray()

    def _run(self) -> None:
        try:
            with av.open(self.path) as container:
                self._loop(container)
        except Exception as e:      # a broken file: the window says so
            self.error = str(e) or type(e).__name__
            with self._cv:
                self._playing = False

    def _loop(self, container) -> None:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        tb = stream.time_base
        eps = 0.25 / self.fps
        frames = None           # the decoder, positioned after `ahead`
        ahead = None            # a frame decoded but not yet shown
        clock = None            # (wall time, video time) of the first frame played
        while True:
            with self._cv:
                while not self._closed and self._want is None and not self._playing:
                    self._cv.wait()
                if self._closed:
                    return
                want, self._want = self._want, None
                playing = self._playing
            if want is not None:
                container.seek(int(want / tb), stream=stream, backward=True, any_frame=False)
                frames, ahead, clock = container.decode(stream), None, None
                shown = None
                for f in frames:
                    if f.pts is None:
                        continue
                    if float(f.pts * tb) > want + eps:
                        if shown is None:
                            shown = f
                        else:
                            ahead = f
                        break
                    shown = f
                if shown is not None:
                    self._give(want, self._rgb(shown))
                continue
            if not playing:
                continue
            if frames is None:
                with self._cv:
                    self._want = self.position
                continue
            f = ahead if ahead is not None else next(frames, None)
            ahead = None
            while f is not None and f.pts is None:
                f = next(frames, None)
            t = float(f.pts * tb) if f is not None else self.duration
            if f is None or (self._until is not None and t >= self._until):
                with self._cv:
                    self._playing = False
                continue
            now = time.monotonic()
            if clock is None or now - (clock[0] + t - clock[1]) > 0.25:
                clock = (now, t)        # the first frame, or fallen behind: play on from here
            due = clock[0] + t - clock[1]
            with self._cv:
                self._cv.wait_for(lambda: self._closed or self._want is not None or not self._playing,
                                  timeout=max(0.0, due - time.monotonic()))
                stop = self._closed or self._want is not None or not self._playing
            if stop:
                ahead = f
                clock = None
                continue
            self._give(t, self._rgb(f))


class Filmstrip:
    """Small frames spread over a video, decoded on a thread; frames[k] is the picture at times[k] once ready."""

    def __init__(self, path: str | Path, duration: float, count: int, height: int = 54, aspect: float = 16 / 9):
        self.path = str(path)
        self.count = count
        self.height = _even(height)
        self.width = _even(height * aspect)
        self.times = [(k + 0.5) / count * duration for k in range(count)]
        self.frames: dict[int, np.ndarray] = {}
        self._closed = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._closed = True
        self._thread.join(timeout=2.0)

    def _run(self) -> None:
        try:
            with av.open(self.path) as container:
                stream = container.streams.video[0]
                stream.thread_type = "AUTO"
                tb = stream.time_base
                for k, t in enumerate(self.times):
                    if self._closed:
                        return
                    # The key frame at or before t: near enough for a strip, and quick.
                    container.seek(int(t / tb), stream=stream, backward=True, any_frame=False)
                    for f in container.decode(stream):
                        self.frames[k] = f.reformat(width=self.width, height=self.height, format="rgb24").to_ndarray()
                        break
        except Exception:
            pass        # the strip stays empty; the picture above says what is wrong


# ---- the window -------------------------------------------------------------

def stamp(seconds: float) -> str:
    """m:ss.ss"""
    seconds = max(0.0, seconds)
    return f"{int(seconds // 60)}:{seconds % 60:05.2f}"


class TrimWindow:
    """The trim editor for one video Source of the open project (see the module's docstring)."""

    STRIP = 64
    RULER = 18

    def __init__(self, ctx, directory: str | Path, src, shortest: float = 0.0):
        from .sequencer import project_path

        self.ctx = ctx
        self.src = src
        self.shortest = shortest
        self.player = FramePlayer(project_path(directory, src.path))
        self.duration = max(self.player.duration, MIN_PART)
        self.spans = [[a, b] for a, b in src.spans(self.duration)] or [[0.0, self.duration]]
        self.sel: int | None = 0
        self.t = self.spans[0][0]
        self.player.seek(self.t)
        self.strip = Filmstrip(self.player.path, self.duration, 24, aspect=self.player.width / self.player.height)
        self.picture = None
        self.atlas = None
        self._uploaded: set[int] = set()
        self._drag = None
        self.focused = False

    @property
    def name(self) -> str:
        return Path(self.src.path).name

    def close(self) -> None:
        self.player.close()
        self.strip.close()
        for tex in (self.picture, self.atlas):
            if tex is not None:
                tex.release()
        self.picture = self.atlas = None

    # ---- edits -------------------------------------------------------------

    def commit(self) -> None:
        self.src.set_spans(self.spans, self.duration)

    def seek(self, t: float) -> None:
        self.t = min(max(0.0, t), self.duration)
        self.player.seek(self.t)

    def mark(self, side: int) -> None:
        i = (mark_in if side == 0 else mark_out)(self.spans, self.sel, self.t, self.duration)
        if i is not None:
            self.sel = i
            self.commit()

    def add(self) -> None:
        i = add_part(self.spans, self.t, self.duration)
        if i is not None:
            self.sel = i
            self.commit()

    def delete(self) -> None:
        if self.sel is None or self.sel >= len(self.spans):
            return
        del self.spans[self.sel]
        if not self.spans:
            self.spans = [[0.0, self.duration]]
        self.sel = min(self.sel, len(self.spans) - 1)
        self.commit()

    def whole(self) -> None:
        self.spans = [[0.0, self.duration]]
        self.sel = 0
        self.commit()

    def step(self, seconds: float) -> None:
        self.player.pause()
        self.seek(self.t + seconds)

    def play(self, part: int | None = None) -> None:
        if part is not None:
            a, b = self.spans[part]
            self.sel = part
            self.seek(a)
            self.player.play(until=b)
        elif self.player.playing:
            self.player.pause()
        else:
            self.player.play()

    # ---- drawing -----------------------------------------------------------

    def _upload(self) -> None:
        got = self.player.take()
        if got is not None:
            t, img = got
            if not self._drag:
                self.t = t if self.player.playing else self.t
            if self.picture is None:
                import moderngl

                self.picture = self.ctx.texture((img.shape[1], img.shape[0]), 3)
                self.picture.filter = (moderngl.LINEAR, moderngl.LINEAR)
            self.picture.write(np.ascontiguousarray(img).tobytes())
        if self.player.playing and not self._drag:
            self.t = self.player.position
        st = self.strip
        if self.atlas is None:
            import moderngl

            self.atlas = self.ctx.texture((st.width * st.count, st.height), 3)
            self.atlas.filter = (moderngl.LINEAR, moderngl.LINEAR)
        for k, img in list(st.frames.items()):
            if k not in self._uploaded:
                self.atlas.write(np.ascontiguousarray(img).tobytes(), viewport=(k * st.width, 0, st.width, st.height))
                self._uploaded.add(k)

    def draw(self) -> bool:
        """Draws the window; False once it is closed."""
        from imgui_bundle import imgui

        V2 = imgui.ImVec2
        self._upload()
        imgui.set_next_window_size(V2(920, 680), imgui.Cond_.first_use_ever)
        imgui.set_next_window_bg_alpha(1.0)
        vis, still_open = imgui.begin(f"Trim video: {self.name}###trim", True)
        if not vis:
            imgui.end()
            return bool(still_open)
        self.focused = imgui.is_window_focused(imgui.FocusedFlags_.root_and_child_windows)
        if self.focused and not imgui.get_io().want_text_input:
            self._keys()
        used = sum(b - a for a, b in self.spans)
        whole = len(self.spans) == 1 and self.spans[0][0] <= 0.0 and self.spans[0][1] >= self.duration - 0.05
        imgui.text(f"{stamp(self.duration)} long")
        imgui.same_line()
        imgui.text_disabled("   the whole video is used" if whole else
                            f"   {len(self.spans)} part{'s' if len(self.spans) != 1 else ''}, {stamp(used)} used")
        if self.player.error:
            imgui.text_colored(imgui.ImVec4(1, 0.5, 0.4, 1), f"Can't play this video: {self.player.error}")

        # The picture, as large as fits above the controls.
        avail = imgui.get_content_region_avail()
        # Below it: the transport, the strip and ruler, the table of parts, a warning and the keys.
        fh = imgui.get_frame_height_with_spacing()
        warn = any(self.shortest and b - a < self.shortest for a, b in self.spans)
        below = fh + self.STRIP + self.RULER + 6 + fh * (min(len(self.spans), 6) + 1) + 4 + fh * (2 if warn else 1) + 12
        aspect = self.player.width / self.player.height
        h = max(80.0, avail.y - below)
        w = min(avail.x, h * aspect)
        h = w / aspect
        x0 = imgui.get_cursor_pos_x() + (avail.x - w) / 2
        imgui.set_cursor_pos_x(x0)
        if self.picture is not None:
            imgui.image(imgui.ImTextureRef(self.picture.glo), V2(w, h))
        else:
            imgui.dummy(V2(w, h))

        # Transport and marks.
        if imgui.button("Pause" if self.player.playing else "Play", V2(70, 0)):
            self.play()
        imgui.same_line()
        if imgui.button("|<"):
            self.seek(0.0)
        imgui.same_line()
        if imgui.arrow_button("##back", imgui.Dir.left):
            self.step(-1.0 / self.player.fps)
        if imgui.is_item_hovered():
            imgui.set_tooltip("Back a frame (Left; Shift+Left a second)")
        imgui.same_line()
        if imgui.arrow_button("##on", imgui.Dir.right):
            self.step(1.0 / self.player.fps)
        if imgui.is_item_hovered():
            imgui.set_tooltip("On a frame (Right; Shift+Right a second)")
        imgui.same_line()
        imgui.text(f"{stamp(self.t)} / {stamp(self.duration)}")
        imgui.same_line()
        if imgui.button("Set start (I)"):
            self.mark(0)
        if imgui.is_item_hovered():
            imgui.set_tooltip("The playhead becomes the start of the part it is in, or of a new part")
        imgui.same_line()
        if imgui.button("Set stop (O)"):
            self.mark(1)
        if imgui.is_item_hovered():
            imgui.set_tooltip("The playhead becomes the stop of the part it is in, or of a new part")
        imgui.same_line()
        if imgui.button("Add part (A)"):
            self.add()
        imgui.same_line()
        imgui.begin_disabled(self.sel is None or whole)
        if imgui.button("Delete part"):
            self.delete()
        imgui.end_disabled()
        imgui.same_line()
        imgui.begin_disabled(whole)
        if imgui.button("Whole video"):
            self.whole()
        imgui.end_disabled()

        self._strip()
        self._table()
        short = [i + 1 for i, (a, b) in enumerate(self.spans) if self.shortest and b - a < self.shortest]
        if short:
            imgui.text_colored(imgui.ImVec4(1, 0.8, 0.4, 1), f"Part{'s' if len(short) > 1 else ''} "
                               f"{', '.join(map(str, short))} {'are' if len(short) > 1 else 'is'} shorter than the "
                               f"shortest shard ({self.shortest:.2f} s) and won't be cut from.")
        imgui.text_disabled("Space play   I start   O stop   A add a part   Del delete it   Left/Right a frame   "
                            "Drag a part's ends on the strip; double-click the strip to add a part.")
        imgui.end()
        return bool(still_open)

    def _keys(self) -> None:
        from imgui_bundle import imgui

        K = imgui.Key
        shift = imgui.get_io().key_shift
        if imgui.is_key_pressed(K.space, False):
            self.play()
        if imgui.is_key_pressed(K.i, False):
            self.mark(0)
        if imgui.is_key_pressed(K.o, False):
            self.mark(1)
        if imgui.is_key_pressed(K.a, False):
            self.add()
        if imgui.is_key_pressed(K.delete, False) or imgui.is_key_pressed(K.backspace, False):
            self.delete()
        if imgui.is_key_pressed(K.left_arrow):
            self.step(-1.0 if shift else -1.0 / self.player.fps)
        if imgui.is_key_pressed(K.right_arrow):
            self.step(1.0 if shift else 1.0 / self.player.fps)
        if imgui.is_key_pressed(K.home, False):
            self.seek(0.0)
        if imgui.is_key_pressed(K.end, False):
            self.seek(self.duration)

    def _strip(self) -> None:
        from imgui_bundle import imgui

        V2 = imgui.ImVec2

        def col(r, g, b, a=1.0):
            return imgui.get_color_u32(imgui.ImVec4(r, g, b, a))

        draw = imgui.get_window_draw_list()
        o = imgui.get_cursor_screen_pos()
        width = max(50.0, imgui.get_content_region_avail().x)
        H = self.STRIP
        dur = self.duration

        def x(t):
            return o.x + t / dur * width

        draw.add_rect_filled(o, V2(o.x + width, o.y + H), col(0.1, 0.1, 0.12))
        st = self.strip
        if self.atlas is not None:
            cw = width / st.count
            for k in range(st.count):
                if k in self._uploaded:
                    draw.add_image(imgui.ImTextureRef(self.atlas.glo), V2(o.x + k * cw, o.y), V2(o.x + (k + 1) * cw, o.y + H),
                                   V2(k / st.count, 0), V2((k + 1) / st.count, 1))
        # What is not used is dimmed; the parts are outlined, the selected one brighter, with handles at the ends.
        edges = [0.0] + [v for a, b in self.spans for v in (a, b)] + [dur]
        for a, b in zip(edges[::2], edges[1::2]):
            if b > a:
                draw.add_rect_filled(V2(x(a), o.y), V2(x(b), o.y + H), col(0, 0, 0, 0.68))
        for i, (a, b) in enumerate(self.spans):
            sel = i == self.sel
            c = (1.0, 0.75, 0.25) if sel else (0.95, 0.55, 0.2)
            draw.add_rect(V2(x(a), o.y + 1), V2(x(b), o.y + H - 1), col(*c, 1.0 if sel else 0.8), 2.0, thickness=3.0 if sel else 2.0)
            for e in (a, b):
                draw.add_rect_filled(V2(x(e) - 3, o.y), V2(x(e) + 3, o.y + H), col(*c))
            draw.add_text(V2(x(a) + 6, o.y + 3), col(1, 1, 1, 0.95), str(i + 1))
        draw.add_line(V2(x(self.t), o.y - 4), V2(x(self.t), o.y + H + 4), col(1, 1, 1), 2.0)
        # The ruler.
        ry = o.y + H + 2
        step = next((s for s in (0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600) if width / (dur / s) >= 60), 1200)
        k = 0
        while k * step <= dur + 1e-9:
            tx = x(k * step)
            draw.add_line(V2(tx, ry), V2(tx, ry + 5), col(0.6, 0.6, 0.65))
            draw.add_text(V2(tx + 3, ry + 2), col(0.6, 0.6, 0.65), stamp(k * step)[:-3] if step >= 1 else stamp(k * step)[:-1])
            k += 1

        imgui.set_cursor_screen_pos(o)
        imgui.invisible_button("##parts", V2(width, H))
        mx = imgui.get_mouse_pos().x
        t = min(max(0.0, (mx - o.x) / width * dur), dur)
        near = None
        for i, (a, b) in enumerate(self.spans):
            for side, e in ((0, a), (1, b)):
                d = abs(mx - x(e))
                if d <= 7 and (near is None or d < near[0]):
                    near = (d, i, side)
        if imgui.is_item_hovered():
            if near or self._drag not in (None, "seek"):
                imgui.set_mouse_cursor(imgui.MouseCursor_.resize_ew)
            imgui.set_tooltip(stamp(t))
        if imgui.is_item_activated():
            if near:
                self._drag = (near[1], near[2])
                self.sel = near[1]
                self.player.pause()
            else:
                self._drag = "seek"
                i = _containing(self.spans, t)
                self.sel = i if i is not None else self.sel
        if imgui.is_item_active() and self._drag is not None:
            if self._drag == "seek":
                self.seek(t)
            else:
                i, side = self._drag
                drag_end(self.spans, i, side, t, dur)
                self.seek(self.spans[i][side])
                self.commit()
        elif not imgui.is_item_active():
            self._drag = None
        if imgui.is_item_hovered() and imgui.is_mouse_double_clicked(imgui.MouseButton_.left) and _containing(self.spans, t) is None:
            self.seek(t)
            self.add()
        imgui.set_cursor_screen_pos(V2(o.x, o.y + H + self.RULER + 6))

    def _table(self) -> None:
        from imgui_bundle import imgui

        V2 = imgui.ImVec2
        flags = imgui.TableFlags_.borders_inner_h | imgui.TableFlags_.row_bg | imgui.TableFlags_.scroll_y
        rows = min(len(self.spans), 6)
        height = imgui.get_frame_height_with_spacing() * (rows + 1) + 4
        if not imgui.begin_table("parts", 5, flags, V2(0, height)):
            return
        imgui.table_setup_column("part", imgui.TableColumnFlags_.width_fixed, 44)
        imgui.table_setup_column("start", imgui.TableColumnFlags_.width_fixed, 120)
        imgui.table_setup_column("stop", imgui.TableColumnFlags_.width_fixed, 120)
        imgui.table_setup_column("length", imgui.TableColumnFlags_.width_fixed, 80)
        imgui.table_setup_column("", imgui.TableColumnFlags_.width_stretch)
        imgui.table_setup_scroll_freeze(0, 1)
        imgui.table_headers_row()
        play = None
        for i, (a, b) in enumerate(self.spans):
            imgui.push_id(i)
            imgui.table_next_row()
            imgui.table_next_column()
            if imgui.selectable(str(i + 1), i == self.sel, imgui.SelectableFlags_.span_all_columns
                                | imgui.SelectableFlags_.allow_overlap)[0]:
                self.sel = i
                self.seek(a)
            for side in (0, 1):
                imgui.table_next_column()
                imgui.set_next_item_width(-1)
                ch, v = imgui.drag_float(f"##e{side}", float(self.spans[i][side]), 0.02, 0.0, self.duration, "%.2f s")
                if ch:
                    self.sel = i
                    drag_end(self.spans, i, side, v, self.duration)
                    self.seek(self.spans[i][side])
                    self.commit()
            imgui.table_next_column()
            imgui.text(f"{b - a:.2f} s")
            imgui.table_next_column()
            if imgui.small_button("play"):
                play = i
            imgui.pop_id()
        imgui.end_table()
        if play is not None:
            self.play(play)
