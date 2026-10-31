"""The live instrument: output, step grid, step and lane editor, pool, music reactivity."""

from __future__ import annotations

import copy
import time

import numpy as np
import moderngl
from imgui_bundle import hello_imgui, imgui, immapp
from imgui_bundle import portable_file_dialogs as pfd

from . import fx as fxlib
from .export import Exporter
from .media import AudioPlayer, MediaError
from .renderer import Renderer
from .sequencer import CHOICES, Modulation, Pattern, Step

PUNCH_KEYS = [("z", "invert"), ("x", "rgb_split"), ("c", "kaleido"), ("v", "strobe"), ("b", "trails"),
              ("n", "glitch"), ("m", "mirror")]
METERS = ["bass", "mid", "high", "level", "onset", "intensity", "beat"]
TARGETS = [f"master.{n}" for n in fxlib.ALL_FX] + [f"all.{n}" for n in fxlib.FX]
CELL = 26


def V2(x, y):
    return imgui.ImVec2(float(x), float(y))


def V4(r, g, b, a=1.0):
    return imgui.ImVec4(float(r), float(g), float(b), float(a))


def _key(name: str):
    return getattr(imgui.Key, name if not name.isdigit() else f"_{name}")


class Player:
    def __init__(self, session, start: float = 0.0, autoplay: bool = False):
        self.s = session
        self.engine = session.engine
        self.project = session.project
        self.t = start
        self.autoplay = autoplay
        self.sel_lane = 0
        self.sel_step = 0
        self.edit_pattern = 0
        self.follow = True
        self.show_ui = True
        self.message = ""
        self.message_until = 0.0
        self.ctx: moderngl.Context | None = None
        self.renderer: Renderer | None = None
        self.audio: AudioPlayer | None = None
        self.thumbs: dict[int, moderngl.Texture] = {}
        self.dirty = False
        self.state = None
        self.frame_times: list[float] = []
        self.export_job: Exporter | None = None
        self.export_dialog = None

    # ---- setup -------------------------------------------------------

    def post_init(self) -> None:
        self.ctx = moderngl.create_context()
        p = self.project
        self.renderer = Renderer(self.ctx, p.width, p.height, self.s.pool)
        for shard in self.s.pool.shards:
            frame = self.s.pool.frame(shard.id, self.s.pool.length(shard.id) // 2)
            small = np.ascontiguousarray(frame[::4, ::4])
            tex = self.ctx.texture((small.shape[1], small.shape[0]), 3, small.tobytes())
            tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            self.thumbs[shard.id] = tex
        self.audio = AudioPlayer(p.music)
        if self.t:
            self.audio.seek(self.t)
        if self.autoplay:
            self.play()

    def play(self) -> None:
        try:
            self.audio.toggle()
        except MediaError as e:
            self.say(str(e), 6.0)

    def before_exit(self) -> None:
        if self.audio:
            self.audio.close()

    def say(self, text: str, seconds: float = 2.5) -> None:
        self.message = text
        self.message_until = time.monotonic() + seconds

    # ---- time and keys -----------------------------------------------

    def now(self) -> float:
        if self.audio and self.audio.playing:
            self.t = self.audio.position()
            if self.audio.finished():
                self.audio.pause()
        return self.t

    def seek(self, t: float) -> None:
        self.t = max(0.0, min(t, self.engine.analysis.duration))
        self.audio.seek(self.t)

    def keys(self) -> None:
        io = imgui.get_io()
        if io.want_text_input:
            return
        e = self.engine
        if imgui.is_key_pressed(imgui.Key.space, False):
            self.play()
        if imgui.is_key_pressed(imgui.Key.tab, False):
            self.show_ui = not self.show_ui
        if imgui.is_key_pressed(imgui.Key.home, False):
            self.seek(0.0)
        bar = 4 * 60.0 / max(1.0, e.analysis.tempo * self.project.tempo_scale)
        if imgui.is_key_pressed(imgui.Key.left_arrow):
            self.seek(self.t - bar)
        if imgui.is_key_pressed(imgui.Key.right_arrow):
            self.seek(self.t + bar)
        if io.key_ctrl and imgui.is_key_pressed(imgui.Key.s, False):
            self.save()
        elif imgui.is_key_pressed(imgui.Key.r, False):
            self.toggle_record()
        for i in range(min(9, len(self.project.patterns))):
            if imgui.is_key_pressed(_key(str(i + 1)), False):
                e.queue_pattern(i)
                self.edit_pattern = i
                self.follow = False
                self.say(f"Pattern {self.project.patterns[i].name} next")
        e.fill = imgui.is_key_down(imgui.Key.f)
        for key, name in PUNCH_KEYS:
            k = _key(key)
            if imgui.is_key_pressed(k, False):
                e.record_punch(self.t, name, True)
            elif imgui.is_key_released(k):
                e.record_punch(self.t, name, False)

    def toggle_record(self) -> None:
        e = self.engine
        e.recording = not e.recording
        if e.recording:
            # Moves from here on replace what was recorded after this point.
            g = e.step_at(self.t)
            self.project.performance = [ev for ev in self.project.performance
                                        if ev.get("step", -1) < g and ev.get("t", -1.0) < self.t]
            self.say("Recording live moves")
        else:
            self.say(f"Recorded: {len(self.project.performance)} moves (saved with Ctrl+S)")
        self.dirty = True

    def save(self) -> None:
        self.s.save()
        self.dirty = False
        self.say("Saved")

    # ---- frame -------------------------------------------------------

    def gui(self) -> None:
        if self.export_job or self.export_dialog:
            self.export_window()
            return
        started = time.perf_counter()
        self.keys()
        t = self.now()
        self.state = self.engine.state(t)
        out = self.renderer.render(self.state)
        self.ctx.screen.use()
        self.frame_times = (self.frame_times + [time.perf_counter() - started])[-60:]
        if self.show_ui:
            self.output_window(out)
            self.transport_window()
            self.grid_window()
            self.edit_window()
            self.pool_window()
            self.music_window()
        else:
            self.fullscreen_output(out)

    def _image(self, tex: moderngl.Texture, size) -> None:
        imgui.image(imgui.ImTextureRef(tex.glo), size, V2(0, 1), V2(1, 0))

    def fullscreen_output(self, tex) -> None:
        vp = imgui.get_main_viewport()
        imgui.set_next_window_pos(vp.pos)
        imgui.set_next_window_size(vp.size)
        flags = (imgui.WindowFlags_.no_decoration | imgui.WindowFlags_.no_move | imgui.WindowFlags_.no_background
                 | imgui.WindowFlags_.no_saved_settings)
        imgui.push_style_var(imgui.StyleVar_.window_padding, V2(0, 0))
        imgui.begin("##full", None, flags)
        w, h = vp.size.x, vp.size.y
        aspect = self.renderer.width / self.renderer.height
        fw, fh = (w, w / aspect) if w / h < aspect else (h * aspect, h)
        imgui.set_cursor_pos(V2((w - fw) / 2, (h - fh) / 2))
        self._image(tex, V2(fw, fh))
        imgui.end()
        imgui.pop_style_var()

    def output_window(self, tex) -> None:
        imgui.set_next_window_pos(V2(8, 8), imgui.Cond_.first_use_ever)
        imgui.set_next_window_size(V2(660, 410), imgui.Cond_.first_use_ever)
        imgui.begin("Output")
        avail = imgui.get_content_region_avail()
        aspect = self.renderer.width / self.renderer.height
        w = min(avail.x, avail.y * aspect)
        self._image(tex, V2(w, w / aspect))
        imgui.end()

    def transport_window(self) -> None:
        e, p, a = self.engine, self.project, self.engine.analysis
        imgui.set_next_window_pos(V2(676, 8), imgui.Cond_.first_use_ever)
        imgui.set_next_window_size(V2(500, 250), imgui.Cond_.first_use_ever)
        imgui.begin("Transport")
        if imgui.button("Pause" if self.audio.playing else "Play", V2(70, 0)):
            self.play()
        imgui.same_line()
        if imgui.button("|<"):
            self.seek(0.0)
        imgui.same_line()
        rec = e.recording
        if rec:
            imgui.push_style_color(imgui.Col_.button, V4(0.75, 0.1, 0.1))
        if imgui.button("Rec" if not rec else "Recording"):
            self.toggle_record()
        if rec:
            imgui.pop_style_color()
        imgui.same_line()
        if imgui.button("Save" + ("*" if self.dirty else "")):
            self.save()
        imgui.same_line()
        if imgui.button("Export MP4"):
            self.audio.pause()
            self.export_dialog = pfd.save_file("Export video", str(self.s.dir / "render.mp4"),
                                               ["MP4 video", "*.mp4"])
        imgui.same_line()
        imgui.text(f"{self.t:6.1f}s  step {self.state.step}  {p.patterns[self.state.pattern].name}")
        imgui.set_next_item_width(-1)
        changed, t = imgui.slider_float("##time", self.t, 0.0, float(a.duration), "%.1f s")
        if changed:
            self.seek(t)
        song_changed, song = imgui.checkbox("Song", e.song_mode)
        if song_changed and p.song:
            e.song_mode = song
            e.reset()
        imgui.same_line()
        imgui.text(f"{a.tempo * p.tempo_scale:.1f} BPM" + (" grid" if p.grid else ""))
        for label, scale in (("x0.5", 0.5), ("x2", 2.0)):
            imgui.same_line()
            if imgui.small_button(label):
                p.tempo_scale = max(0.25, min(4.0, p.tempo_scale * scale))
                e.retime()
                self.dirty = True
        imgui.same_line()
        imgui.set_next_item_width(80)
        ch, spb = imgui.slider_int("steps/beat", p.steps_per_beat, 1, 8)
        if ch:
            p.steps_per_beat = spb
            e.retime()
            self.dirty = True
        if p.performance:
            if imgui.small_button("Clear recording"):
                p.performance = []
                e.reset()
                self.dirty = True
            imgui.same_line()
            imgui.text_disabled(f"{len(p.performance)} recorded moves play back")
        ms = 1000 * sum(self.frame_times) / max(1, len(self.frame_times))
        imgui.text_disabled(f"{imgui.get_io().framerate:.0f} fps, {ms:.1f} ms a frame")
        if e.fill:
            imgui.same_line()
            imgui.text_colored(V4(1, 0.8, 0.2), "FILL")
        held = [n for n in e.punch_at(self.t)]
        if held:
            imgui.same_line()
            imgui.text_colored(V4(0.4, 0.9, 1), " ".join(held))
        if self.message and time.monotonic() < self.message_until:
            imgui.text_colored(V4(1, 0.85, 0.4), self.message)
        imgui.separator()
        imgui.text_disabled("Space play  1-9 pattern  F fill  R record  Tab hide  Ctrl+S save  <- -> bar")
        imgui.text_disabled("Hold: Z invert  X rgb  C kaleido  V strobe  B trails  N glitch  M mirror")
        imgui.end()

    # ---- export --------------------------------------------------------

    def export_window(self) -> None:
        if self.export_dialog is not None:
            if not self.export_dialog.ready():
                return
            path = self.export_dialog.result()
            self.export_dialog = None
            if not path:
                return
            if not path.lower().endswith(".mp4"):
                path += ".mp4"
            from .sequencer import Engine

            s = self.s
            engine = Engine(copy.deepcopy(self.project), s.pool.shards, s.analysis,
                            {sh.id: s.pool.length(sh.id) for sh in s.pool.shards}, s.pool.fps)
            engine.song_mode = self.engine.song_mode
            renderer = Renderer(self.ctx, self.project.width, self.project.height, s.pool)
            try:
                self.export_job = Exporter(engine, renderer, path)
            except (MediaError, ValueError, OSError) as e:
                self.say(f"Export failed: {e}", 8.0)
            return
        job = self.export_job
        budget = time.perf_counter() + 1 / 30
        try:
            done = False
            while not done and time.perf_counter() < budget:
                done = job.step(1)
        except (MediaError, OSError) as e:
            self.export_job = None
            self.say(f"Export failed: {e}", 8.0)
            return
        self.ctx.screen.use()
        vp = imgui.get_main_viewport()
        imgui.set_next_window_pos(V2(vp.pos.x + vp.size.x / 2, vp.pos.y + vp.size.y / 2), 0, V2(0.5, 0.5))
        imgui.begin("Exporting", None, imgui.WindowFlags_.always_auto_resize | imgui.WindowFlags_.no_collapse)
        imgui.text(job.path)
        imgui.progress_bar(job.progress, V2(420, 0), f"{job.done} / {job.frames} frames")
        cancel = imgui.button("Cancel")
        imgui.end()
        if cancel:
            job.cancel()
            self.export_job = None
            self.say("Export cancelled")
        elif done:
            self.export_job = None
            self.say(f"Exported {job.path} ({job.info()['took']:.0f} s)", 8.0)

    # ---- the grid ----------------------------------------------------

    def grid_window(self) -> None:
        p = self.project
        imgui.set_next_window_pos(V2(8, 426), imgui.Cond_.first_use_ever)
        imgui.set_next_window_size(V2(660, 230), imgui.Cond_.first_use_ever)
        imgui.begin("Sequencer")
        if self.follow:
            self.edit_pattern = self.state.pattern
        for i, pat in enumerate(p.patterns):
            if i:
                imgui.same_line()
            active = i == self.edit_pattern
            playing = i == self.state.pattern
            col = V4(0.25, 0.45, 0.8) if active else V4(0.2, 0.2, 0.22)
            imgui.push_style_color(imgui.Col_.button, col)
            label = pat.name + (" >" if playing else "")
            if imgui.button(f"{label}##pat{i}", V2(48, 0)):
                self.edit_pattern = i
                self.follow = False
                if not self.engine.song_mode:
                    self.engine.queue_pattern(i)
            imgui.pop_style_color()
        imgui.same_line()
        if imgui.button("+ copy"):
            src = p.patterns[self.edit_pattern]
            new = copy.deepcopy(src)
            new.name = chr(ord("A") + len(p.patterns)) if len(p.patterns) < 26 else f"P{len(p.patterns) + 1}"
            p.patterns.append(new)
            self.edit_pattern = len(p.patterns) - 1
            self.follow = False
            self.dirty = True
        pat = p.patterns[self.edit_pattern]
        imgui.same_line()
        _, self.follow = imgui.checkbox("follow", self.follow)
        if imgui.is_item_hovered():
            imgui.set_tooltip("Show the pattern that is playing")
        imgui.same_line()
        imgui.set_next_item_width(90)
        ch, length = imgui.slider_int("length", pat.length, 1, 64)
        if ch:
            pat.length = length
            p.normalise()
            self.sel_step = min(self.sel_step, length - 1)
            self.dirty = True
        playing_here = self.state.pattern == self.edit_pattern
        cur = self.engine.pattern_for_step(self.state.step)[1] if playing_here else -1
        draw = imgui.get_window_draw_list()
        for li, lane in enumerate(p.lanes):
            imgui.push_id(f"lane{li}")
            ch, mute = imgui.checkbox("##mute", not lane.mute)
            if imgui.is_item_hovered():
                imgui.set_tooltip("Lane on / muted")
            if ch:
                lane.mute = not mute
                self.dirty = True
            imgui.same_line()
            name_col = V4(1, 1, 1) if li == self.sel_lane else V4(0.6, 0.6, 0.6)
            imgui.text_colored(name_col, f"{lane.name[:8]:<8}")
            for si in range(pat.length):
                imgui.same_line(0, 2 if si % 4 else 7)
                step = pat.steps[li][si]
                pos = imgui.get_cursor_screen_pos()
                clicked = imgui.invisible_button(f"s{si}", V2(CELL, CELL))
                right = imgui.is_item_clicked(imgui.MouseButton_.right)
                if clicked:
                    step.on = not step.on
                    self.dirty = True
                if clicked or right:
                    self.sel_lane, self.sel_step = li, si
                if step.on:
                    v = 0.35 + 0.65 * step.prob
                    fill = (0.95 * v, 0.45 * v, 0.2 * v) if step.fx else (0.3 * v, 0.6 * v, 0.95 * v)
                else:
                    fill = (0.16, 0.16, 0.18) if si % 4 else (0.22, 0.22, 0.25)
                col = imgui.get_color_u32(V4(*fill))
                draw.add_rect_filled(pos, V2(pos.x + CELL, pos.y + CELL), col, 3.0)
                if step.on and step.shard is not None:
                    draw.add_circle_filled(V2(pos.x + CELL - 6, pos.y + 6), 3, imgui.get_color_u32(V4(1, 1, 1)))
                if si == cur:
                    draw.add_rect(pos, V2(pos.x + CELL, pos.y + CELL), imgui.get_color_u32(V4(1, 1, 1, 0.9)), 3.0, thickness=2.0)
                if li == self.sel_lane and si == self.sel_step:
                    draw.add_rect(V2(pos.x - 2, pos.y - 2), V2(pos.x + CELL + 2, pos.y + CELL + 2),
                                  imgui.get_color_u32(V4(1, 0.8, 0.2)), 4.0, thickness=2.0)
                if imgui.is_item_hovered() and step.on:
                    imgui.set_tooltip(f"chance {step.prob:.0%}, gate {step.gate:g}, speed {step.speed:g}"
                                      + (", reverse" if step.reverse else "")
                                      + (f", shard {step.shard}" if step.shard is not None else "")
                                      + ("".join(f"\n{k} {v:.2f}" for k, v in step.fx.items())))
            imgui.pop_id()
        imgui.text_disabled("Click: on/off and select.  Right-click: select.  Orange: has effects.  Dot: fixed shard.")
        imgui.end()

    # ---- editor ------------------------------------------------------

    def edit_window(self) -> None:
        p = self.project
        imgui.set_next_window_pos(V2(676, 266), imgui.Cond_.first_use_ever)
        imgui.set_next_window_size(V2(500, 520), imgui.Cond_.first_use_ever)
        imgui.begin("Edit")
        lane = p.lanes[self.sel_lane]
        imgui.separator_text(f"Lane: {lane.name}")
        ch, name = imgui.input_text("name", lane.name)
        if ch:
            lane.name = name
            self.dirty = True
        ch, i = imgui.combo("blend", fxlib.BLEND_MODES.index(lane.blend), fxlib.BLEND_MODES)
        if ch:
            lane.blend = fxlib.BLEND_MODES[i]
            self.dirty = True
        ch, v = imgui.slider_float("opacity", lane.opacity, 0.0, 1.0)
        if ch:
            lane.opacity = v
            self.dirty = True
        ch, i = imgui.combo("chooses", CHOICES.index(lane.choice), CHOICES)
        if imgui.is_item_hovered():
            imgui.set_tooltip("random: any shard\nbag: every shard once before repeats\nno_repeat: never the same twice\n"
                              "intensity: calm shards in quiet parts, busy ones when the music is intense")
        if ch:
            lane.choice = CHOICES[i]
            self.dirty = True
        if lane.shards is not None:
            imgui.text(f"draws from {len(lane.shards)} shards (pick them in Pool)")
            imgui.same_line()
            if imgui.small_button("use whole pool"):
                lane.shards = None
                self.dirty = True
        self.fx_sliders(lane.fx, "lanefx", fxlib.FX, "Lane effects (always on)")

        pat = p.patterns[self.edit_pattern]
        step = pat.steps[self.sel_lane][self.sel_step]
        imgui.separator_text(f"Step {self.sel_step + 1} of pattern {pat.name}")
        changes = [
            imgui.checkbox("on", step.on),
            imgui.slider_float("chance", step.prob, 0.0, 1.0, "%.2f"),
            imgui.slider_float("gate (steps)", step.gate, 0.25, 16.0, "%.2f"),
            imgui.slider_float("speed", step.speed, 0.1, 4.0, "%.2f"),
            imgui.checkbox("reverse", step.reverse),
            imgui.slider_float("random start", step.jitter, 0.0, 1.0, "%.2f"),
        ]
        fields = ["on", "prob", "gate", "speed", "reverse", "jitter"]
        for f, (ch, v) in zip(fields, changes):
            if ch:
                setattr(step, f, v)
                self.dirty = True
        ids = [s.id for s in self.s.pool.shards]
        labels = ["lane chooses"] + [f"shard {i}" for i in ids]
        cur = 0 if step.shard is None or step.shard not in ids else ids.index(step.shard) + 1
        ch, i = imgui.combo("shard", cur, labels)
        if ch:
            step.shard = None if i == 0 else ids[i - 1]
            self.dirty = True
        self.fx_sliders(step.fx, "stepfx", fxlib.FX, "Step effects (parameter locks)")
        if imgui.button("Copy step to the whole lane"):
            for si in range(pat.length):
                if si != self.sel_step:
                    pat.steps[self.sel_lane][si] = copy.deepcopy(step)
            self.dirty = True
        imgui.end()

    def fx_sliders(self, amounts: dict, id_: str, names: dict, title: str) -> None:
        if imgui.tree_node(f"{title}##{id_}"):
            for name, desc in names.items():
                ch, v = imgui.slider_float(f"{name}##{id_}", float(amounts.get(name, 0.0)), 0.0, 1.0, "%.2f")
                if imgui.is_item_hovered():
                    imgui.set_tooltip(desc)
                if ch:
                    if v <= 0.001:
                        amounts.pop(name, None)
                    else:
                        amounts[name] = round(v, 3)
                    self.dirty = True
            imgui.tree_pop()

    # ---- pool --------------------------------------------------------

    def pool_window(self) -> None:
        p = self.project
        imgui.set_next_window_pos(V2(1184, 8), imgui.Cond_.first_use_ever)
        imgui.set_next_window_size(V2(330, 780), imgui.Cond_.first_use_ever)
        imgui.begin("Pool")
        lane = p.lanes[self.sel_lane]
        imgui.text_wrapped(f"Click: use for the selected step.  Ctrl+click: add to / remove from lane {lane.name}'s pool.")
        step = p.patterns[self.edit_pattern].steps[self.sel_lane][self.sel_step]
        width = imgui.get_content_region_avail().x
        tw = 96
        per_row = max(1, int(width // (tw + 8)))
        playing = {l.voice.shard for l in self.state.lanes if l.voice}
        draw = imgui.get_window_draw_list()
        for n, shard in enumerate(sorted(self.s.pool.shards, key=lambda s: s.energy)):
            if n % per_row:
                imgui.same_line()
            pos = imgui.get_cursor_screen_pos()
            size = V2(tw, tw * 9 / 16)
            if imgui.image_button(f"sh{shard.id}", imgui.ImTextureRef(self.thumbs[shard.id].glo), size):
                if imgui.get_io().key_ctrl:
                    ids = list(lane.shards) if lane.shards is not None else []
                    lane.shards = [i for i in ids if i != shard.id] if shard.id in ids else ids + [shard.id]
                    if not lane.shards:
                        lane.shards = None
                else:
                    step.shard = None if step.shard == shard.id else shard.id
                self.dirty = True
            in_lane = lane.shards is not None and shard.id in lane.shards
            if shard.id in playing or step.shard == shard.id or in_lane:
                col = V4(1, 0.8, 0.2) if step.shard == shard.id else V4(0.3, 0.9, 0.5) if in_lane else V4(1, 1, 1, 0.7)
                draw.add_rect(pos, V2(pos.x + tw + 8, pos.y + size.y + 8), imgui.get_color_u32(col), 3.0, thickness=2.0)
            if imgui.is_item_hovered():
                imgui.set_tooltip(f"shard {shard.id}: {shard.duration:.2f} s from {shard.source.split('/')[-1]} "
                                  f"at {shard.start:.1f} s\nenergy {shard.energy:.2f}, brightness {shard.brightness:.2f}")
        imgui.end()

    # ---- music -------------------------------------------------------

    def music_window(self) -> None:
        p = self.project
        imgui.set_next_window_pos(V2(8, 664), imgui.Cond_.first_use_ever)
        imgui.set_next_window_size(V2(660, 300), imgui.Cond_.first_use_ever)
        imgui.begin("Music")
        m = self.state.music
        for name in METERS:
            imgui.progress_bar(float(m.get(name, 0.0)), V2(84, 16), name)
            imgui.same_line()
        imgui.new_line()
        imgui.text("palette")
        for i, pal in enumerate(p.palettes):
            imgui.same_line()
            current = pal == self.state.palette
            for c in pal:
                imgui.same_line(0, 0)
                imgui.color_button(f"##pal{i}{c}", V4(*fxlib.hex_rgb(c)), 0, V2(12, 16 if current else 10))
        imgui.same_line()
        modes = ["intensity", "bars", "off"]
        imgui.set_next_item_width(100)
        ch, i = imgui.combo("##palmode", modes.index(p.palette_mode) if p.palette_mode in modes else 0, modes)
        if imgui.is_item_hovered():
            imgui.set_tooltip("intensity: next palette when the music gets louder or quieter\nbars: every N bars")
        if ch:
            p.palette_mode = modes[i]
            self.engine.retime()
            self.dirty = True
        imgui.separator_text("The music moves these")
        remove = None
        for k, mod in enumerate(p.modulations):
            imgui.push_id(f"mod{k}")
            imgui.set_next_item_width(90)
            ch, i = imgui.combo("##src", fxlib.SOURCES.index(mod.source) if mod.source in fxlib.SOURCES else 0, fxlib.SOURCES)
            if ch:
                mod.source = fxlib.SOURCES[i]
                self.dirty = True
            imgui.same_line()
            imgui.text("->")
            imgui.same_line()
            targets = TARGETS + [f"lane{li}.{n}" for li in range(len(p.lanes)) for n in fxlib.FX]
            imgui.set_next_item_width(170)
            ch, i = imgui.combo("##dst", targets.index(mod.target) if mod.target in targets else 0, targets)
            if ch:
                mod.target = targets[i]
                self.dirty = True
            imgui.same_line()
            imgui.set_next_item_width(160)
            ch, v = imgui.slider_float("##amt", mod.amount, -1.0, 1.0, "%.2f")
            if ch:
                mod.amount = v
                self.dirty = True
            imgui.same_line()
            if imgui.small_button("x"):
                remove = k
            imgui.pop_id()
        if remove is not None:
            del p.modulations[remove]
            self.dirty = True
        if imgui.button("+ link"):
            p.modulations.append(Modulation("bass", "master.zoom", 0.2))
            self.dirty = True
        if imgui.tree_node("Master effects"):
            self.fx_sliders(p.master_fx, "masterfx", fxlib.ALL_FX, "always on")
            imgui.tree_pop()
        imgui.end()


def run(session, fullscreen: bool = False, start: float = 0.0, screenshot: str | None = None,
        screenshot_after: float = 3.0, autoplay: bool = False) -> None:
    player = Player(session, start, autoplay or bool(screenshot))
    params = hello_imgui.RunnerParams()
    params.app_window_params.window_title = f"Shardloom - {session.dir.name}"
    params.app_window_params.window_geometry.size = (1520, 980)
    if fullscreen:
        params.app_window_params.window_geometry.full_screen_mode = hello_imgui.FullScreenMode.full_monitor_work_area
        player.show_ui = False
    params.fps_idling.enable_idling = False
    params.imgui_window_params.default_imgui_window_type = hello_imgui.DefaultImGuiWindowType.no_default_window
    params.ini_folder_type = hello_imgui.IniFolderType.app_user_config_folder
    params.ini_filename = "shardloom.ini"
    params.callbacks.post_init = player.post_init
    params.callbacks.show_gui = player.gui
    params.callbacks.before_exit = player.before_exit
    if screenshot:
        began = time.monotonic()

        def grab():
            if time.monotonic() - began < screenshot_after:
                return
            from PIL import Image

            io = imgui.get_io()
            w = int(io.display_size.x * io.display_framebuffer_scale.x)
            h = int(io.display_size.y * io.display_framebuffer_scale.y)
            data = player.ctx.screen.read(viewport=(0, 0, w, h), components=3, alignment=1)
            Image.frombytes("RGB", (w, h), data).transpose(Image.Transpose.FLIP_TOP_BOTTOM).save(screenshot)
            params.app_shall_exit = True

        params.callbacks.before_swap = grab
    immapp.run(params)
    if player.dirty and not screenshot:
        session.save()
        print(f"Saved changes to {session.dir / 'project.json'}.")
