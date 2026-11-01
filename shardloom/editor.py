"""The editor: the main window. Two workspaces share one project:

  Edit     the media (song, videos, photos), the cut, the output format, titles and rendering,
           with a preview of the finished video and a timeline.
  Perform  the instrument: sequencer, pool, step editor, music links (player.py), docked.

Slow work (copying media in, cutting shards, analysing the song, packing) runs in the background.
"""

from __future__ import annotations

import copy
import gc
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import moderngl
import numpy as np
from imgui_bundle import hello_imgui, imgui, immapp
from imgui_bundle import portable_file_dialogs as pfd

from . import events, formats, library
from .compose import Composer, Program
from .media import AudioPlayer, MediaError, decode_audio
from .sequencer import Project, Title, project_path, starter_project
from .titles import BACKGROUNDS, POSITIONS, font_names, hex_rgb

MUSIC = ["Music", " ".join(f"*{e}" for e in sorted(library.MUSIC_EXT)), "All files", "*"]
MEDIA = ["Videos and photos", " ".join(f"*{e}" for e in sorted(library.VIDEO_EXT | library.PHOTO_EXT | library.MUSIC_EXT)),
         "Videos", " ".join(f"*{e}" for e in sorted(library.VIDEO_EXT)),
         "Photos", " ".join(f"*{e}" for e in sorted(library.PHOTO_EXT)), "All files", "*"]
PHOTOS = ["Photos", " ".join(f"*{e}" for e in sorted(library.PHOTO_EXT)), "All files", "*"]
ZIP = ["Packed Shardloom project", "*.zip"]
PLAYER_WINDOWS = ["Output", "Transport", "Sequencer", "Edit", "Pool", "Music"]
ACCENT = (0.25, 0.45, 0.8)


def V2(x, y):
    return imgui.ImVec2(float(x), float(y))


def V4(r, g, b, a=1.0):
    return imgui.ImVec4(float(r), float(g), float(b), float(a))


def col32(r, g, b, a=1.0) -> int:
    return imgui.get_color_u32(V4(r, g, b, a))


def rgb01(colour: str) -> list[float]:
    return [c / 255 for c in hex_rgb(colour)]


def to_hex(rgb) -> str:
    return "#" + "".join(f"{int(round(max(0.0, min(1.0, c)) * 255)):02x}" for c in rgb[:3])


def clock(seconds: float) -> str:
    seconds = max(0.0, seconds)
    return f"{int(seconds // 60)}:{seconds % 60:04.1f}"


def reveal(path: str | os.PathLike) -> None:
    """Shows a folder in the file manager."""
    try:
        if sys.platform == "win32":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError:
        pass


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-") or "video"


class Task:
    """Work in a background thread, with progress; done(result) then runs in the window's thread."""

    def __init__(self, label: str, work, done=None):
        self.label, self.work, self.done = label, work, done
        self.status = label
        self.progress = 0.0
        self.error: str | None = None
        self.result = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        def run():
            try:
                self.result = self.work(self)
            except Exception as e:
                traceback.print_exc()
                self.error = str(e) or type(e).__name__

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def report(self, done: int, total: int) -> None:
        self.progress = done / max(1, total)

    @property
    def finished(self) -> bool:
        return self.thread is not None and not self.thread.is_alive()


class Editor:
    def __init__(self, folder: str | None = None, layout: str = "Edit"):
        self.start_folder = folder
        self.start_layout = layout
        library.projects_folder()
        self.dir: Path | None = None
        self.project: Project | None = None
        self.session = None
        self.player = None
        self.ctx: moderngl.Context | None = None
        self.composer: Composer | None = None
        self.song: np.ndarray | None = None          # the song at AudioPlayer.RATE, stereo
        self.audio: AudioPlayer | None = None         # the finished video's soundtrack, for the preview
        self._audio_key = None
        self.T = 0.0
        self.mode = "Edit"
        self.tasks: list[Task] = []
        self.dialog = None
        self.dialog_kind = ""
        self.message = ""
        self.message_until = 0.0
        self.thumbs: dict[str, moderngl.Texture] = {}
        self._thumb_data: list[tuple[str, np.ndarray]] = []
        self.media_info: dict[str, dict] = {}
        self.sel_title = 0
        self.show_titles_tab = False
        self.font_filter = ""
        self.renders: list[tuple[str, str, tuple[int, int]]] = []   # (label, path, size) still to do
        self.render_job = None
        self.render_label = ""
        self.render_done: list[str] = []
        self.params: hello_imgui.RunnerParams | None = None
        self._saved = ""
        self._dirty_check = 0
        self.dirty = False
        self._curve_cache: tuple | None = None
        self.show_about = False

    # ---- messages and tasks --------------------------------------------

    def say(self, text: str, seconds: float = 4.0) -> None:
        self.message = text
        self.message_until = time.monotonic() + seconds

    def run(self, label: str, work, done=None) -> None:
        self.tasks.append(Task(label, work, done))
        if len(self.tasks) == 1:
            self.tasks[0].start()

    @property
    def busy(self) -> bool:
        return bool(self.tasks)

    def busy_with(self, label: str) -> bool:
        return any(t.label == label for t in self.tasks)

    def poll_tasks(self) -> None:
        while self.tasks and self.tasks[0].finished:
            task = self.tasks.pop(0)
            if task.error:
                self.say(f"{task.label} failed: {task.error}", 10.0)
            elif task.done:
                try:
                    task.done(task.result)
                except Exception as e:
                    traceback.print_exc()
                    self.say(f"{task.label} failed: {e}", 10.0)
            if self.tasks:
                self.tasks[0].start()

    # ---- projects --------------------------------------------------------

    def new_project(self, folder: str | os.PathLike) -> None:
        folder = Path(folder)
        if folder.suffix.lower() == ".json":
            folder = folder.parent
        if (folder / "project.json").exists():
            self.say(f"{folder} already holds a project; opening it.")
            self.open_project(folder)
            return
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.say(f"Can't make the project folder {folder}: {e}", 10.0)
            return
        self.close_project()
        seed = random.randint(1, 99999)
        p = starter_project("", library.POOL, 3, seed)
        p.cut["seed"] = seed
        f = formats.BY_KEY[p.output["format"]]
        p.width, p.height, p.fps = f.width, f.height, 30.0
        library.save_project(p, folder)
        self.open_project(folder)

    def open_project(self, folder: str | os.PathLike) -> None:
        folder = Path(folder)
        if not (folder / "project.json").exists():
            self.say(f"{folder} is not a Shardloom project (no project.json).", 8.0)
            return
        self.close_project()
        try:
            p = Project.load(folder)
        except (OSError, ValueError, TypeError, KeyError) as e:
            self.say(f"Could not open {folder}: {e}", 10.0)
            return
        library.adopt_pool_sources(p, folder)
        if p.output.get("format") not in formats.BY_KEY and p.output.get("format") != "custom":
            p.output["format"] = "custom"
        self.dir, self.project = folder, p
        self.T = 0.0
        self._saved = json.dumps(p.to_dict(), sort_keys=True)
        self.dirty = False
        self.remember(folder)
        for s in p.sources:
            self.queue_thumbnail(s)
        if p.music:
            self.load_song()
        if p.sources and not (folder / p.pool / "pool.json").exists():
            self.cut()

    def close_project(self) -> None:
        if self.project and self.dir and self.dirty:
            self.save()
        self.drop_session()
        for tex in self.thumbs.values():
            tex.release()
        self.thumbs.clear()
        self.media_info.clear()
        self.song = None
        self.project = None
        self.dir = None

    def save(self) -> None:
        if not self.project:
            return
        try:
            missing = library.save_project(self.project, self.dir)
        except OSError as e:
            self.say(f"Could not save: {e}", 10.0)
            return
        self._saved = json.dumps(self.project.to_dict(), sort_keys=True)
        self.dirty = False
        self.say("Saved" + (f" ({len(missing)} files are missing: {', '.join(missing[:3])})" if missing else ""))

    def check_dirty(self) -> None:
        self._dirty_check += 1
        if self.project and self._dirty_check % 30 == 0:
            self.dirty = json.dumps(self.project.to_dict(), sort_keys=True) != self._saved

    def remember(self, folder: Path) -> None:
        recent = [str(folder)] + [r for r in self.recent() if r != str(folder)]
        try:
            hello_imgui.save_user_pref("recent", json.dumps(recent[:10]))
        except Exception:
            pass

    def recent(self) -> list[str]:
        try:
            return [r for r in json.loads(hello_imgui.load_user_pref("recent") or "[]") if Path(r, "project.json").exists()]
        except Exception:
            return []

    # ---- the session (pool + analysis + engine) and the player ---------

    def pool_size(self) -> tuple[int, int]:
        p = self.project
        return formats.pool_size(p.width, p.height, int(p.cut.get("detail", 540)))

    def stale(self) -> dict:
        if not self.project:
            return {"pool": False, "analysis": False}
        return library.needs_build(self.project, self.dir, *self.pool_size())

    def try_session(self) -> None:
        p = self.project
        if (self.session is not None or p is None or self.song is None or self.busy_with("Cutting shards")
                or not (self.dir / p.pool / "pool.json").exists() or not library.analysis_current(p, self.dir)):
            return
        from .cli import Session
        from .player import Player

        try:
            self.session = Session(self.dir, p)
        except SystemExit as e:
            self.say(str(e), 8.0)
            return
        self.player = Player(self.session)
        self.player.post_init(self.ctx, self.song)
        self.player.on_export = lambda: self.start_renders(self.render_list(False))
        self.composer = None

    def drop_session(self) -> None:
        if self.audio:
            self.audio.close()
            self.audio = None
            self._audio_key = None
        if self.player:
            self.player.release()
            self.player = None
        if self.session:
            self.session.close()
            self.session = None
        self.composer = None
        gc.collect()

    def program(self) -> Program:
        dur = self.session.analysis.duration if self.session else 1.0
        try:
            return Program(self.project, dur)
        except ValueError:
            return Program(self.project, dur, 0.0, dur)

    # ---- media -----------------------------------------------------------

    def queue_thumbnail(self, src) -> None:
        directory = self.dir

        def work(task):
            try:
                img = library.thumbnail(directory, src)
            except Exception:
                return
            full = project_path(directory, src.path)
            if src.kind == "video":
                from .media import probe

                meta = probe(full)
                info = {"duration": meta["duration"], "width": meta["video"]["width"], "height": meta["video"]["height"]}
            else:
                from PIL import Image

                with Image.open(full) as im:
                    info = {"duration": 0.0, "width": im.size[0], "height": im.size[1]}
            self.media_info[src.path] = info
            self._thumb_data.append((src.path, img))

        # Thumbnails run in their own threads, beside the queued tasks.
        threading.Thread(target=work, args=(None,), daemon=True).start()

    def upload_thumbs(self) -> None:
        while self._thumb_data:
            path, img = self._thumb_data.pop(0)
            img = np.ascontiguousarray(img)
            tex = self.ctx.texture((img.shape[1], img.shape[0]), 3, img.tobytes())
            tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            old = self.thumbs.pop(path, None)
            if old:
                old.release()
            self.thumbs[path] = tex

    def add_media(self, paths: list[str]) -> None:
        music = [p for p in paths if library.kind_of(p) == "music"]
        paths = [p for p in paths if library.kind_of(p) != "music"]
        if music:
            # A song among the files: it becomes the song.
            self.set_song(music[0])
        if not paths:
            return
        directory = self.dir

        def work(task):
            out, errors = [], []
            for i, path in enumerate(paths):
                task.status = f"Copying in {Path(path).name}"
                try:
                    out.append(library.prepare_source(directory, path))
                except Exception as e:
                    errors.append(str(e))
                task.report(i + 1, len(paths))
            return out, errors

        def done(result):
            added, errors = result
            for src, info in added:
                if any(s.path == src.path for s in self.project.sources):
                    continue
                src.id = library.new_id(self.project)
                self.project.sources.append(src)
                self.media_info[src.path] = info
                self.queue_thumbnail(src)
            self.save()
            if errors:
                self.say("Not added: " + "; ".join(errors), 10.0)
            else:
                self.say(f"Added {len(added)} file(s). Press Cut shards to use them.")

        self.run("Adding media", work, done)

    def set_song(self, path: str) -> None:
        directory = self.dir

        def work(task):
            task.status = "Copying in the song"
            rel = library.import_file(directory, path)
            task.status = "Listening to the song"
            library.analyse_music(Project(music=rel), directory)
            task.progress = 0.7
            task.status = "Decoding the song"
            return rel, decode_audio(project_path(directory, rel), AudioPlayer.RATE, 2)

        def done(result):
            rel, samples = result
            self.drop_session()
            self.project.music = rel
            self.song = samples
            self.save()
            self.try_session()

        self.run("Adding the song", work, done)

    def load_song(self) -> None:
        """Decodes the project's song (and analyses it when that is missing or out of date)."""
        directory, p = self.dir, self.project
        if not project_path(directory, p.music).exists():
            self.say(f"The song is missing: {p.music}. Choose it again.", 10.0)
            return

        def work(task):
            if not library.analysis_current(p, directory):
                task.status = "Listening to the song"
                library.analyse_music(p, directory)
            task.progress = 0.6
            task.status = "Decoding the song"
            return decode_audio(project_path(directory, p.music), AudioPlayer.RATE, 2)

        def done(samples):
            self.drop_session()
            self.song = samples
            self.try_session()

        self.run("Loading the song", work, done)

    def cut(self) -> None:
        p = self.project
        if not p.sources or not any(s.enabled for s in p.sources):
            self.say("Add a video or photo first.")
            return
        self.save()
        snapshot = copy.deepcopy(p)
        directory = self.dir
        size = self.pool_size()

        def work(task):
            task.status = "Cutting shards"
            return library.build_pool(snapshot, directory, *size, progress=task.report, swap=False)

        def done(shards):
            self.drop_session()
            library.finish_pool(self.project, directory)
            self.save()
            self.try_session()
            self.say(f"Cut {len(shards)} shards.")

        self.run("Cutting shards", work, done)

    # ---- dialogs -----------------------------------------------------------

    def ask(self, kind: str) -> None:
        if self.dialog is not None:
            return
        self.dialog_kind = kind
        home = str(Path.home())
        if kind == "new":
            self.dialog = pfd.save_file("New project: name its folder", str(library.projects_folder() / "My video"),
                                        ["Shardloom project folder", "*"], pfd.opt.force_overwrite)
        elif kind == "open":
            self.dialog = pfd.select_folder("Open a Shardloom project", str(library.projects_folder()))
        elif kind == "open_zip":
            self.dialog = pfd.open_file("Open a packed Shardloom project", home, ZIP)
        elif kind == "save_as":
            self.dialog = pfd.save_file("Save the project as a new folder", str(self.dir) + " copy",
                                        ["Shardloom project folder", "*"], pfd.opt.force_overwrite)
        elif kind == "pack":
            self.dialog = pfd.save_file("Pack the project into a .zip", str(self.dir.with_suffix(".zip")), ZIP)
        elif kind == "song":
            self.dialog = pfd.open_file("Choose the song (WAV, MP3, ...)", home, MUSIC)
        elif kind == "media":
            self.dialog = pfd.open_file("Add videos and photos", home, MEDIA, pfd.opt.multiselect)
        elif kind.startswith("title_image:"):
            self.dialog = pfd.open_file("Background picture for the title", home, PHOTOS)
        elif kind == "render_as":
            f = self.format_name()
            self.dialog = pfd.save_file("Render the video", str(self.dir / library.RENDERS / f"{safe_name(self.dir.name)}-{f}.mp4"),
                                        ["MP4 video", "*.mp4"])
        else:
            self.dialog = None

    def poll_dialog(self) -> None:
        if self.dialog is None or not self.dialog.ready():
            return
        result = self.dialog.result()
        self.dialog = None
        kind = self.dialog_kind
        if not result:
            return
        if kind == "new":
            self.new_project(result)
        elif kind == "open":
            self.open_project(result)
        elif kind == "open_zip":
            self.run("Unpacking", lambda task: library.unpack(result[0]), lambda folder: self.open_project(folder))
        elif kind == "save_as":
            self.save_as(result)
        elif kind == "pack":
            out = result if result.lower().endswith(".zip") else result + ".zip"
            snapshot, directory = self.project, self.dir
            self.run("Packing", lambda task: library.pack(snapshot, directory, out, progress=task.report),
                     lambda _: self.say(f"Packed into {out}", 8.0))
        elif kind == "song":
            self.set_song(result[0])
        elif kind == "media":
            self.add_media(result)
        elif kind.startswith("title_image:"):
            i = int(kind.split(":")[1])
            if i < len(self.project.titles):
                directory = self.dir

                def done(rel, i=i):
                    if i < len(self.project.titles):
                        self.project.titles[i].image = rel
                        self.project.titles[i].background = "image"

                self.run("Adding the picture", lambda task: library.import_file(directory, result[0]), done)
        elif kind == "render_as":
            path = result if result.lower().endswith(".mp4") else result + ".mp4"
            self.start_renders([(self.format_label(), path, (self.project.width, self.project.height))])

    def save_as(self, target: str) -> None:
        new = Path(target)
        if new.exists() and any(new.iterdir()):
            if (new / "project.json").exists():
                self.say(f"{new} already holds a project.", 8.0)
                return
            new = new / self.dir.name
        old = self.dir
        self.save()
        snapshot = copy.deepcopy(self.project)

        def work(task):
            library.save_as(snapshot, old, new, progress=task.report)
            return new

        self.run("Saving a copy", work, lambda folder: (self.open_project(folder), self.say(f"Now working in {folder}")))

    # ---- rendering -------------------------------------------------------

    def format_name(self) -> str:
        return self.project.output.get("format", "custom") if self.project.output.get("format") in formats.BY_KEY else \
            f"{self.project.width}x{self.project.height}"

    def format_label(self) -> str:
        f = formats.BY_KEY.get(self.project.output.get("format", ""))
        return f.label if f else f"Custom ({self.project.width}x{self.project.height})"

    def render_list(self, batch: bool = True) -> list[tuple[str, str, tuple[int, int]]]:
        p = self.project
        out_dir = self.dir / library.RENDERS
        name = safe_name(self.dir.name)
        items = [(self.format_label(), str(out_dir / f"{name}-{self.format_name()}.mp4"), (p.width, p.height))]
        if batch:
            for key in p.output.get("queue", []):
                f = formats.BY_KEY.get(key)
                if f and key != p.output.get("format"):
                    items.append((f.label, str(out_dir / f"{name}-{key}.mp4"), (f.width, f.height)))
        return items

    def start_renders(self, items) -> None:
        if not self.session:
            self.say("Cut shards and choose a song first.")
            return
        if self.audio:
            self.audio.pause()
        if self.player:
            self.player.audio.pause()
        self.renders = list(items)
        self.render_done = []
        self.next_render()

    def next_render(self) -> None:
        from .export import project_exporter

        self.render_job = None
        while self.renders:
            label, path, size = self.renders.pop(0)
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            try:
                engine = None
                if self.player:
                    from .sequencer import Engine

                    s = self.session
                    engine = Engine(copy.deepcopy(self.project), s.pool.shards, s.analysis,
                                    {sh.id: s.pool.length(sh.id) for sh in s.pool.shards}, s.pool.fps)
                    engine.song_mode = self.player.engine.song_mode
                self.render_job = project_exporter(self.session, self.ctx, path, size=size, engine=engine)
                self.render_label = label
                return
            except (MediaError, ValueError, OSError) as e:
                self.say(f"Render failed: {e}", 10.0)
        if self.render_done:
            self.say(f"Rendered {len(self.render_done)} video(s) into {Path(self.render_done[0]).parent}", 10.0)

    def step_render(self) -> None:
        job = self.render_job
        budget = time.perf_counter() + 1 / 30
        try:
            done = False
            while not done and time.perf_counter() < budget:
                done = job.step(1)
        except (MediaError, OSError) as e:
            self.renders = []
            self.render_job = None
            self.say(f"Render failed: {e}", 10.0)
            return
        finally:
            self.ctx.screen.use()
        if done:
            self.render_done.append(job.path)
            self.next_render()

    def cancel_render(self) -> None:
        if self.render_job:
            self.render_job.cancel()
        self.render_job = None
        self.renders = []
        self.say("Render cancelled")

    # ---- every frame, before the windows --------------------------------

    def post_init(self) -> None:
        self.ctx = moderngl.create_context()
        if self.start_folder:
            folder = Path(self.start_folder)
            if folder.suffix.lower() == ".zip":
                self.run("Unpacking", lambda task: library.unpack(folder), lambda f: self.open_project(f))
            else:
                self.open_project(folder)
        if self.start_layout != "Edit":
            self.switch(self.start_layout)

    def switch(self, mode: str) -> None:
        if mode == self.mode:
            return
        prog = self.program() if self.project else None
        if mode == "Perform":
            if self.audio:
                self.audio.pause()
            if self.player and prog:
                t = prog.song_time(self.T)
                self.player.seek(t if t is not None else prog.song_start)
        elif self.player:
            self.player.audio.pause()
            if prog:
                self.T = max(0.0, min(prog.duration, prog.program_time(self.player.t)))
                if self.audio:
                    self.audio.seek(self.T)
        self.mode = mode
        hello_imgui.switch_layout(mode)

    def frame_start(self) -> None:
        self.poll_dialog()
        self.poll_tasks()
        self.upload_thumbs()
        self.check_dirty()
        if self.render_job:
            self.step_render()
            return
        if not self.project:
            return
        if self.mode == "Perform":
            if self.player:
                self.player.tick()
            return
        if not self.player:
            return
        self.edit_keys()
        prog = self.program()
        self.update_audio(prog)
        if self.audio and self.audio.playing:
            self.T = self.audio.position()
            if self.audio.finished():
                self.audio.pause()
        self.T = max(0.0, min(self.T, prog.duration))
        r = self.player.renderer
        want = formats.preview_size(self.project.width, self.project.height)
        if (r.width, r.height) != want:
            from .renderer import Renderer

            self.player.renderer = Renderer(self.ctx, *want, self.session.pool)
            self.composer = None
        if self.composer is None:
            self.composer = Composer(self.player.engine, self.player.renderer, prog, self.dir)
        self.composer.program = prog
        self.composer.render(self.T)
        self.ctx.screen.use()

    def update_audio(self, prog: Program) -> None:
        key = (prog.song_start, prog.song_end, prog.pre, prog.post, prog.fade_in, prog.fade_out)
        if key == self._audio_key or self.song is None:
            return
        # Rebuild the soundtrack when the edit settles, not on every step of a slider.
        if imgui.is_any_item_active() and self.audio is not None:
            return
        playing = self.audio.playing if self.audio else False
        if self.audio:
            self.audio.close()
        self.audio = AudioPlayer(samples=prog.audio(self.song, AudioPlayer.RATE))
        self._audio_key = key
        self.audio.seek(self.T)
        if playing:
            self.play()

    def play(self) -> None:
        if not self.audio:
            return
        try:
            self.audio.toggle()
        except MediaError as e:
            self.say(str(e), 6.0)

    def seek(self, T: float) -> None:
        self.T = max(0.0, T)
        if self.audio:
            self.audio.seek(self.T)

    def edit_keys(self) -> None:
        io = imgui.get_io()
        if io.key_ctrl and imgui.is_key_pressed(imgui.Key.s, False):
            self.save()
        if io.want_text_input:
            return
        if imgui.is_key_pressed(imgui.Key.space, False):
            self.play()
        if imgui.is_key_pressed(imgui.Key.home, False):
            self.seek(0.0)
        if imgui.is_key_pressed(imgui.Key.left_arrow):
            self.seek(self.T - 5.0)
        if imgui.is_key_pressed(imgui.Key.right_arrow):
            self.seek(self.T + 5.0)

    # ---- menus, status, welcome, modals ---------------------------------

    def menus(self) -> None:
        if imgui.begin_menu("File"):
            if imgui.menu_item_simple("New project..."):
                self.ask("new")
            if imgui.menu_item_simple("Open project..."):
                self.ask("open")
            if imgui.menu_item_simple("Open packed project (.zip)..."):
                self.ask("open_zip")
            recent = self.recent()
            if imgui.begin_menu("Open recent", bool(recent)):
                for r in recent:
                    if imgui.menu_item_simple(r):
                        self.open_project(r)
                imgui.end_menu()
            imgui.separator()
            has = self.project is not None
            if imgui.menu_item_simple("Save", "Ctrl+S", False, has):
                self.save()
            if imgui.menu_item_simple("Save as new project...", "", False, has):
                self.ask("save_as")
            if imgui.menu_item_simple("Pack into a .zip...", "", False, has):
                self.ask("pack")
            if imgui.menu_item_simple("Show project folder", "", False, has):
                reveal(self.dir)
            imgui.separator()
            if imgui.menu_item_simple("Quit"):
                self.params.app_shall_exit = True
            imgui.end_menu()
        if imgui.begin_menu("Help"):
            if imgui.menu_item_simple("Keys and tips"):
                self.show_about = True
            imgui.end_menu()
        imgui.dummy(V2(24, 0))
        for mode in ("Edit", "Perform"):
            active = self.mode == mode
            imgui.push_style_color(imgui.Col_.button, V4(*ACCENT) if active else V4(0.2, 0.2, 0.22))
            if imgui.small_button(f"  {mode}  "):
                self.switch(mode)
            imgui.pop_style_color()
        if self.project:
            imgui.dummy(V2(16, 0))
            imgui.text_disabled(self.dir.name + (" *" if self.dirty else ""))

    def status(self) -> None:
        if self.tasks:
            t = self.tasks[0]
            imgui.text(t.status + "...")
            imgui.same_line()
            imgui.progress_bar(t.progress, V2(160, 0), f"{t.progress:.0%}" if t.progress else "")
            if len(self.tasks) > 1:
                imgui.same_line()
                imgui.text_disabled(f"(+{len(self.tasks) - 1} more)")
            imgui.same_line()
        if self.message and time.monotonic() < self.message_until:
            imgui.text_colored(V4(1, 0.85, 0.4), self.message)
        elif self.dir:
            imgui.text_disabled(str(self.dir))

    def gui(self) -> None:
        if not self.project:
            self.welcome()
        if self.render_job:
            self.render_modal()
        if self.show_about:
            self.about()

    def welcome(self) -> None:
        if not imgui.is_popup_open("Shardloom"):
            imgui.open_popup("Shardloom")
        vp = imgui.get_main_viewport()
        imgui.set_next_window_pos(V2(vp.pos.x + vp.size.x / 2, vp.pos.y + vp.size.y / 2), 0, V2(0.5, 0.5))
        imgui.set_next_window_size(V2(560, 0))
        if imgui.begin_popup_modal("Shardloom", None, imgui.WindowFlags_.no_resize | imgui.WindowFlags_.no_move
                                   | imgui.WindowFlags_.always_auto_resize)[0]:
            imgui.text_wrapped("Cut your footage and photos into shards, play them to a song, and render it for "
                               "YouTube or Instagram.")
            imgui.dummy(V2(0, 6))
            if imgui.button("New project...", V2(170, 34)):
                self.ask("new")
            imgui.same_line()
            if imgui.button("Open project...", V2(170, 34)):
                self.ask("open")
            imgui.same_line()
            if imgui.button("Open .zip...", V2(170, 34)):
                self.ask("open_zip")
            recent = self.recent()
            if recent:
                imgui.separator_text("Recent")
                for r in recent[:8]:
                    if imgui.selectable(f"{Path(r).name}   ({r})", False)[0]:
                        self.open_project(r)
            if self.tasks:
                imgui.text(self.tasks[0].status + "...")
            if self.message and time.monotonic() < self.message_until:
                imgui.text_colored(V4(1, 0.5, 0.4), self.message)
            if self.project:
                imgui.close_current_popup()
            imgui.end_popup()

    def render_modal(self) -> None:
        job = self.render_job
        if not imgui.is_popup_open("Rendering"):
            imgui.open_popup("Rendering")
        vp = imgui.get_main_viewport()
        imgui.set_next_window_pos(V2(vp.pos.x + vp.size.x / 2, vp.pos.y + vp.size.y / 2), 0, V2(0.5, 0.5))
        if imgui.begin_popup_modal("Rendering", None, imgui.WindowFlags_.always_auto_resize)[0]:
            imgui.text(self.render_label)
            imgui.text_disabled(job.path)
            took = job.info()["took"]
            left = took / max(job.progress, 1e-6) - took if job.progress > 0.02 else 0
            imgui.progress_bar(job.progress, V2(460, 0), f"{job.done} / {job.frames} frames")
            imgui.text_disabled(f"{job.info()['fps']:.0f} frames a second" + (f", about {clock(left)} to go" if left else ""))
            if self.renders:
                imgui.text_disabled(f"Then {len(self.renders)} more: " + ", ".join(r[0] for r in self.renders))
            if imgui.button("Cancel", V2(100, 0)):
                self.cancel_render()
                imgui.close_current_popup()
            imgui.end_popup()

    def about(self) -> None:
        imgui.open_popup("Keys and tips")
        opened = imgui.begin_popup_modal("Keys and tips", True, imgui.WindowFlags_.always_auto_resize)
        if opened[0]:
            for line in [
                "Edit workspace",
                "   Space play / pause     Home to the start     Left / Right 5 seconds     Ctrl+S save",
                "   Click the timeline to move the playhead; drag the ends of the song strip to trim the song.",
                "Perform workspace (the instrument)",
                "   Space play     1-9 next pattern     F fill     R record live moves     Tab hide the panels",
                "   Hold Z X C V B N M for invert, rgb split, kaleido, strobe, trails, glitch, mirror",
                "",
                "A project folder keeps copies of everything it uses (media/), so it can be moved, copied with",
                "File > Save as new project, or packed into one .zip. Shards and the beat analysis are rebuilt",
                "from the media when they are missing.",
            ]:
                imgui.text(line)
            if imgui.button("Close", V2(100, 0)):
                self.show_about = False
                imgui.close_current_popup()
            imgui.end_popup()
        if not opened[1]:
            self.show_about = False

    # ---- Media ----------------------------------------------------------

    def need_project(self) -> bool:
        if self.project:
            return True
        imgui.text_disabled("Open or make a project.")
        return False

    def media_window(self) -> None:
        if not self.need_project():
            return
        p = self.project
        imgui.separator_text("Song")
        if p.music:
            imgui.text(Path(p.music).name)
            if self.session:
                a = self.session.analysis
                imgui.text_disabled(f"{clock(a.duration)}   {a.tempo:.1f} BPM   {len(a.beats)} beats")
        else:
            imgui.text_colored(V4(1, 0.8, 0.4), "Choose the song the video is cut to.")
        if imgui.button("Choose song (WAV, MP3)..." if not p.music else "Change song..."):
            self.ask("song")

        imgui.separator_text("Videos and photos")
        if imgui.button("Add videos and photos..."):
            self.ask("media")
        if not p.sources:
            imgui.text_disabled("Shards are cut at random from these.")
        remove, move = None, None
        lanes = p.lanes
        for i, s in enumerate(p.sources):
            imgui.push_id(f"src{s.id}")
            right = imgui.get_cursor_pos_x() + imgui.get_content_region_avail().x
            ch, on = imgui.checkbox("##on", s.enabled)
            if imgui.is_item_hovered():
                imgui.set_tooltip("Use this in the cut")
            if ch:
                s.enabled = on
            imgui.same_line()
            tex = self.thumbs.get(s.path)
            th = 36
            if tex:
                imgui.image(imgui.ImTextureRef(tex.glo), V2(th * tex.width / tex.height, th))
            else:
                imgui.dummy(V2(th * 16 / 9, th))
            imgui.same_line()
            imgui.begin_group()
            name = Path(s.path).name
            imgui.text(name if len(name) < 34 else name[:31] + "...")
            info = self.media_info.get(s.path)
            kind = "video" if s.kind == "video" else "photo"
            detail = kind
            if info:
                detail += f"  {info['width']}x{info['height']}" + (f"  {clock(info['duration'])}" if s.kind == "video" else "")
            imgui.text_disabled(detail)
            imgui.end_group()
            imgui.same_line(right - 64)
            if imgui.small_button("^") and i > 0:
                move = (i, i - 1)
            imgui.same_line()
            if imgui.small_button("v") and i < len(p.sources) - 1:
                move = (i, i + 1)
            imgui.same_line()
            if imgui.small_button("x"):
                remove = i
            if imgui.tree_node("settings"):
                if s.kind == "video":
                    imgui.set_next_item_width(140)
                    _, s.weight = imgui.slider_float("share of cuts", s.weight, 0.0, 4.0, "x%.1f")
                    dur = info["duration"] if info else 0.0
                    imgui.set_next_item_width(90)
                    ch, v = imgui.drag_float("from (s)", s.start, 0.1, 0.0, max(0.0, dur), "%.1f")
                    if ch:
                        s.start = max(0.0, v)
                    imgui.same_line()
                    imgui.set_next_item_width(90)
                    end = s.end if s.end is not None else dur
                    ch, v = imgui.drag_float("to (s)", end, 0.1, s.start, max(s.start, dur), "%.1f")
                    if ch:
                        s.end = None if dur and v >= dur - 0.05 else v
                else:
                    imgui.set_next_item_width(120)
                    ch, k = imgui.combo("motion", library.MOTIONS.index(s.motion) if s.motion in library.MOTIONS else 4,
                                        ["still", "zoom in", "zoom out", "pan", "random"])
                    if ch:
                        s.motion = library.MOTIONS[k]
                    imgui.set_next_item_width(120)
                    _, s.shards = imgui.slider_int("shards", s.shards, 1, 8)
                imgui.text("in layers")
                for li, lane in enumerate(lanes):
                    imgui.same_line()
                    on = lane.sources is None or s.id in lane.sources
                    ch, v = imgui.checkbox(f"{lane.name}##l{li}", on)
                    if ch:
                        ids = [x.id for x in p.sources]
                        current = list(lane.sources) if lane.sources is not None else ids
                        current = [x for x in current if x != s.id] + ([s.id] if v else [])
                        lane.sources = None if set(ids) <= set(current) else current
                imgui.tree_pop()
            imgui.pop_id()
        if remove is not None:
            gone = p.sources.pop(remove)
            for lane in lanes:
                if lane.sources is not None:
                    lane.sources = [x for x in lane.sources if x != gone.id] or None
        if move:
            a, b = move
            p.sources[a], p.sources[b] = p.sources[b], p.sources[a]

        imgui.separator_text("Cut")
        cut = p.cut
        imgui.set_next_item_width(160)
        _, cut["count"] = imgui.slider_int("video shards", int(cut.get("count", 48)), 4, 400)
        imgui.set_next_item_width(160)
        _, cut["min"] = imgui.slider_float("shortest (s)", float(cut.get("min", 0.25)), 0.05, 4.0, "%.2f")
        imgui.set_next_item_width(160)
        _, cut["max"] = imgui.slider_float("longest (s)", max(float(cut.get("max", 1.5)), cut["min"]), cut["min"], 8.0, "%.2f")
        imgui.set_next_item_width(100)
        _, cut["seed"] = imgui.input_int("seed", int(cut.get("seed", 1)))
        imgui.same_line()
        if imgui.small_button("new"):
            cut["seed"] = random.randint(1, 99999)
        if imgui.is_item_hovered():
            imgui.set_tooltip("The same seed always cuts the same shards")
        details = list(formats.DETAIL)
        imgui.set_next_item_width(160)
        cur = details.index(int(cut.get("detail", 540))) if int(cut.get("detail", 540)) in details else 1
        ch, k = imgui.combo("shard detail", cur, list(formats.DETAIL.values()))
        if ch:
            cut["detail"] = details[k]
        pw, ph = self.pool_size()
        gb = library.disk_estimate(p, pw, ph) / 1e9
        imgui.text_disabled(f"shards {pw}x{ph}, about {gb:.1f} GB on disk" if gb >= 0.1 else f"shards {pw}x{ph}")
        stale = self.stale()["pool"]
        cutting = self.busy_with("Cutting shards")
        imgui.begin_disabled(cutting or not p.sources)
        if stale:
            imgui.push_style_color(imgui.Col_.button, V4(0.8, 0.45, 0.15))
        if imgui.button("Cut shards", V2(140, 30)):
            self.cut()
        if stale:
            imgui.pop_style_color()
        imgui.end_disabled()
        imgui.same_line()
        if cutting:
            imgui.text("cutting...")
        elif stale:
            imgui.text_colored(V4(1, 0.7, 0.3), "out of date")
        elif self.session:
            imgui.text_disabled(f"{len(self.session.pool.shards)} shards, up to date")

    # ---- Preview ----------------------------------------------------------

    def preview_window(self) -> None:
        if not self.need_project():
            return
        p = self.project
        if not self.player or not self.composer:
            imgui.dummy(V2(0, 30))
            steps = []
            if not p.music:
                steps.append("Choose a song (Media, left).")
            if not p.sources:
                steps.append("Add videos or photos (Media, left).")
            if p.sources and not (self.dir / p.pool / "pool.json").exists():
                steps.append("Press Cut shards.")
            for line in steps or ["Getting ready..."]:
                imgui.text(line)
            return
        prog = self.composer.program
        avail = imgui.get_content_region_avail()
        bar = imgui.get_frame_height_with_spacing() * 2 + 8
        aspect = p.width / p.height
        h = max(40.0, avail.y - bar)
        w = min(avail.x, h * aspect)
        h = w / aspect
        imgui.set_cursor_pos_x(imgui.get_cursor_pos_x() + (avail.x - w) / 2)
        imgui.image(imgui.ImTextureRef(self.composer.compositor.output.glo), V2(w, h), V2(0, 1), V2(1, 0))
        playing = self.audio is not None and self.audio.playing
        if imgui.button("Pause" if playing else "Play", V2(70, 0)):
            self.play()
        imgui.same_line()
        if imgui.button("|<"):
            self.seek(0.0)
        imgui.same_line()
        imgui.text(f"{clock(self.T)} / {clock(prog.duration)}")
        imgui.same_line()
        t = prog.song_time(self.T)
        imgui.text_disabled(f"song {clock(t)}" if t is not None else "title card")
        imgui.same_line()
        imgui.text_disabled(f"   {self.format_label()}, {p.fps:g} fps")
        imgui.set_next_item_width(-1)
        ch, v = imgui.slider_float("##T", self.T, 0.0, float(prog.duration), "")
        if ch:
            self.seek(v)

    # ---- Timeline ---------------------------------------------------------

    def timeline_window(self) -> None:
        if not self.need_project() or not self.session:
            if self.project:
                imgui.text_disabled("The timeline shows once the song and shards are ready.")
            return
        p = self.project
        prog = self.composer.program if self.composer else self.program()
        a = self.session.analysis
        draw = imgui.get_window_draw_list()
        o = imgui.get_cursor_screen_pos()
        width = max(50.0, imgui.get_content_region_avail().x)
        dur = max(prog.duration, 0.1)

        def x(T):
            return o.x + T / dur * width

        ruler, track, strip = 18.0, 26.0, 46.0
        y0 = o.y
        # Ruler.
        draw.add_rect_filled(V2(o.x, y0), V2(o.x + width, y0 + ruler), col32(0.13, 0.13, 0.15))
        step = next(s for s in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600) if width / dur * s >= 60)
        T = 0.0
        while T <= dur:
            draw.add_line(V2(x(T), y0 + ruler - 6), V2(x(T), y0 + ruler), col32(0.6, 0.6, 0.6))
            draw.add_text(V2(x(T) + 2, y0 + 1), col32(0.6, 0.6, 0.6), clock(T)[:-2])
            T += step
        # Picture track: cards and the song section.
        y1 = y0 + ruler + 2
        sx0, sx1 = x(prog.pre), x(prog.pre + prog.song_end - prog.song_start)
        draw.add_rect_filled(V2(sx0, y1), V2(sx1, y1 + track), col32(0.18, 0.3, 0.45), 3.0)
        curve = a.curves.get("intensity")
        if curve is not None and len(curve) and sx1 - sx0 > 2:
            n = int(sx1 - sx0)
            ts = np.linspace(prog.song_start, prog.song_end, n)
            vals = curve[np.clip((ts * a.fps).astype(int), 0, len(curve) - 1)]
            for k in range(0, n, 2):
                v = float(vals[k])
                draw.add_line(V2(sx0 + k, y1 + track - 2), V2(sx0 + k, y1 + track - 2 - v * (track - 6)),
                              col32(0.35, 0.6, 0.9, 0.8))
        draw.add_text(V2(sx0 + 4, y1 + 4), col32(1, 1, 1, 0.9), Path(p.music).name)
        # Title track.
        y2 = y1 + track + 2
        for pl in prog.placed:
            c = (0.75, 0.45, 0.2) if pl.card else (0.55, 0.35, 0.7)
            sel = pl.index == self.sel_title
            draw.add_rect_filled(V2(x(pl.t0), y2 if not pl.card else y1), V2(x(pl.t1), y2 + track),
                                 col32(*c, 1.0 if sel else 0.75), 3.0)
            if sel:
                draw.add_rect(V2(x(pl.t0), y2 if not pl.card else y1), V2(x(pl.t1), y2 + track), col32(1, 0.85, 0.3), 3.0,
                              thickness=2.0)
            label = (pl.title.text or "title").split("\n")[0]
            draw.push_clip_rect(V2(x(pl.t0), y1), V2(x(pl.t1), y2 + track), True)
            draw.add_text(V2(x(pl.t0) + 4, y2 + 5), col32(1, 1, 1), label)
            draw.pop_clip_rect()
        imgui.set_cursor_screen_pos(V2(o.x, y0))
        imgui.invisible_button("##timeline", V2(width, ruler + 2 * track + 4))
        if imgui.is_item_active() or imgui.is_item_clicked():
            mx = imgui.get_mouse_pos().x
            T = (mx - o.x) / width * dur
            self.seek(T)
            if imgui.is_item_clicked():
                my = imgui.get_mouse_pos().y
                for pl in prog.placed:
                    if pl.t0 <= T < pl.t1 and (my >= y2 or pl.card):
                        self.sel_title = pl.index
                        self.show_titles_tab = True
        # Song strip: the whole song, the part used, draggable ends.
        y3 = y2 + track + 10
        sd = max(a.duration, 0.1)

        def sx(t):
            return o.x + t / sd * width

        draw.add_rect_filled(V2(o.x, y3), V2(o.x + width, y3 + strip), col32(0.12, 0.12, 0.14), 3.0)
        if curve is not None and len(curve):
            vals = curve[np.clip((np.linspace(0, sd, int(width) // 2) * a.fps).astype(int), 0, len(curve) - 1)]
            for k, v in enumerate(vals):
                xx = o.x + 2 * k
                draw.add_line(V2(xx, y3 + strip - 2), V2(xx, y3 + strip - 2 - float(v) * (strip - 6)),
                              col32(0.4, 0.4, 0.45))
        bars = a.beat_times()[::4]
        if len(bars) and width / len(bars) > 4:
            for bt in bars:
                draw.add_line(V2(sx(bt), y3), V2(sx(bt), y3 + 5), col32(0.5, 0.5, 0.55))
        draw.add_rect_filled(V2(sx(prog.song_start), y3), V2(sx(prog.song_end), y3 + strip), col32(0.3, 0.55, 0.9, 0.25))
        for t in (prog.song_start, prog.song_end):
            draw.add_rect_filled(V2(sx(t) - 3, y3), V2(sx(t) + 3, y3 + strip), col32(0.4, 0.7, 1.0))
        st = prog.song_time(self.T)
        if st is not None:
            draw.add_line(V2(sx(st), y3), V2(sx(st), y3 + strip), col32(1, 1, 1), 1.5)
        draw.add_text(V2(o.x + 4, y3 + 3), col32(0.8, 0.8, 0.85), "whole song: drag the blue ends to trim")
        imgui.set_cursor_screen_pos(V2(o.x, y3))
        imgui.invisible_button("##strip", V2(width, strip))
        if imgui.is_item_activated():
            mx = imgui.get_mouse_pos().x
            near = [(abs(mx - sx(prog.song_start)), "start"), (abs(mx - sx(prog.song_end)), "end")]
            d, which = min(near)
            self._dragging = which if d < 10 else "seek"
        if imgui.is_item_active():
            t = max(0.0, min(sd, (imgui.get_mouse_pos().x - o.x) / width * sd))
            which = getattr(self, "_dragging", "seek")
            out = p.output
            if which == "start":
                out["start"] = round(min(t, prog.song_end - 0.5), 2)
            elif which == "end":
                out["end"] = None if t >= sd - 0.05 else round(max(t, prog.song_start + 0.5), 2)
            elif prog.song_start <= t <= prog.song_end:
                self.seek(prog.program_time(t))
        if imgui.is_item_hovered():
            imgui.set_tooltip(f"{clock((imgui.get_mouse_pos().x - o.x) / width * sd)} in the song")
        # Playhead over the program tracks.
        draw.add_line(V2(x(self.T), y0), V2(x(self.T), y2 + track), col32(1, 1, 1), 2.0)

    # ---- Video settings ---------------------------------------------------

    def settings_window(self) -> None:
        if not self.need_project():
            return
        if imgui.begin_tab_bar("settings"):
            if imgui.begin_tab_item("Format")[0]:
                self.format_tab()
                imgui.end_tab_item()
            flags = imgui.TabItemFlags_.set_selected if self.show_titles_tab else 0
            self.show_titles_tab = False
            if imgui.begin_tab_item("Titles", None, flags)[0]:
                self.titles_tab()
                imgui.end_tab_item()
            if imgui.begin_tab_item("Render")[0]:
                self.render_tab()
                imgui.end_tab_item()
            imgui.end_tab_bar()

    def format_tab(self) -> None:
        p, out = self.project, self.project.output
        imgui.separator_text("Size")
        keys = [f.key for f in formats.FORMATS] + ["custom"]
        labels = [f.label for f in formats.FORMATS] + ["Custom size"]
        cur = keys.index(out.get("format")) if out.get("format") in keys else len(keys) - 1
        imgui.set_next_item_width(-1)
        ch, k = imgui.combo("##format", cur, labels)
        if ch:
            out["format"] = keys[k]
            f = formats.BY_KEY.get(keys[k])
            if f:
                p.width, p.height = f.width, f.height
        f = formats.BY_KEY.get(out.get("format"))
        if f is None:
            imgui.set_next_item_width(100)
            ch, w = imgui.input_int("width", p.width, 2, 16)
            if ch:
                p.width = formats.even(min(max(w, 16), 7680))
            imgui.same_line()
            imgui.set_next_item_width(100)
            ch, h = imgui.input_int("height", p.height, 2, 16)
            if ch:
                p.height = formats.even(min(max(h, 16), 7680))
        elif f.note:
            imgui.text_disabled(f.note)
        # The shape at a glance.
        draw = imgui.get_window_draw_list()
        pos = imgui.get_cursor_screen_pos()
        box = 60.0
        bw, bh = (box, box * p.height / p.width) if p.width >= p.height else (box * p.width / p.height, box)
        draw.add_rect(V2(pos.x, pos.y), V2(pos.x + bw, pos.y + bh), col32(0.6, 0.75, 1.0), 2.0, thickness=2.0)
        draw.add_text(V2(pos.x + bw + 10, pos.y + bh / 2 - 8), col32(0.8, 0.8, 0.85), f"{p.width} x {p.height}")
        imgui.dummy(V2(box, max(bh, 20) + 4))
        if self.session and (self.session.pool.width / self.session.pool.height > 0) and \
                abs(self.session.pool.width / self.session.pool.height - p.width / p.height) > 0.02:
            imgui.text_colored(V4(1, 0.7, 0.3), "The shards were cut for another shape: they are cropped to fit.")
            imgui.text_colored(V4(1, 0.7, 0.3), "Cut shards again to frame them for this one.")

        imgui.separator_text("Frames and quality")
        rates = formats.FRAME_RATES
        imgui.set_next_item_width(120)
        ch, k = imgui.combo("frames a second", rates.index(p.fps) if p.fps in rates else 2, [f"{r:g}" for r in rates])
        if ch:
            p.fps = rates[k]
        qkeys = list(formats.QUALITY)
        imgui.set_next_item_width(120)
        q = out.get("quality", "good")
        ch, k = imgui.combo("quality", qkeys.index(q) if q in qkeys else 1, [v[0] for v in formats.QUALITY.values()])
        if ch:
            out["quality"] = qkeys[k]
        mbps = formats.bitrate(p.width, p.height, p.fps, out.get("quality", "good")) / 1e6
        imgui.text_disabled(f"{mbps:.1f} Mbit/s video")

        imgui.separator_text("Song section")
        dur = self.session.analysis.duration if self.session else 0.0
        imgui.set_next_item_width(110)
        ch, v = imgui.drag_float("from", float(out.get("start") or 0.0), 0.1, 0.0, max(dur, 0.0), "%.1f s")
        if ch:
            out["start"] = max(0.0, v)
        imgui.same_line()
        if imgui.small_button("playhead##start") and self.composer:
            t = self.composer.program.song_time(self.T)
            if t is not None:
                out["start"] = round(t, 2)
        imgui.set_next_item_width(110)
        end = out.get("end") if out.get("end") is not None else dur
        ch, v = imgui.drag_float("to", float(end), 0.1, 0.0, max(dur, 0.0), "%.1f s")
        if ch:
            out["end"] = None if dur and v >= dur - 0.05 else v
        imgui.same_line()
        if imgui.small_button("playhead##end") and self.composer:
            t = self.composer.program.song_time(self.T)
            if t is not None:
                out["end"] = round(t, 2)
        imgui.same_line()
        if imgui.small_button("whole song"):
            out["start"], out["end"] = 0.0, None
        imgui.set_next_item_width(110)
        _, out["fade_in"] = imgui.slider_float("fade in", float(out.get("fade_in") or 0.0), 0.0, 5.0, "%.1f s")
        imgui.set_next_item_width(110)
        _, out["fade_out"] = imgui.slider_float("fade out", float(out.get("fade_out") or 0.0), 0.0, 8.0, "%.1f s")
        if self.composer:
            imgui.text_disabled(f"The video is {clock(self.composer.program.duration)} long.")

    def titles_tab(self) -> None:
        p = self.project
        if imgui.button("+ Opening title"):
            p.titles.append(Title(text=self.dir.name, at="start", extra=False, duration=4.0))
            self.sel_title = len(p.titles) - 1
        imgui.same_line()
        if imgui.button("+ Closing title"):
            p.titles.append(Title(text="Thanks for watching", at="end", duration=4.0, background="colour",
                                  colour="#000000", position="center"))
            self.sel_title = len(p.titles) - 1
        if not p.titles:
            imgui.text_disabled("No titles yet.")
            return
        for i, t in enumerate(p.titles):
            label = f"{'Opening' if t.at == 'start' else 'Closing'}: {(t.text or '(no text)').splitlines()[0]}"
            if imgui.selectable(f"{label}##t{i}", i == self.sel_title)[0]:
                self.sel_title = i
        self.sel_title = min(self.sel_title, len(p.titles) - 1)
        i = self.sel_title
        t = p.titles[i]
        imgui.separator_text("Title")
        imgui.push_id(f"title{i}")
        _, t.text = imgui.input_text_multiline("text", t.text, V2(-60, 48))
        _, t.subtitle = imgui.input_text("subtitle", t.subtitle)
        ch, k = imgui.combo("when", 0 if t.at == "start" else 1, ["at the start", "at the end"])
        if ch:
            t.at = "start" if k == 0 else "end"
        imgui.set_next_item_width(140)
        _, t.duration = imgui.slider_float("seconds", t.duration, 0.5, 20.0, "%.1f")
        _, t.extra = imgui.checkbox("on its own card, " + ("before" if t.at == "start" else "after") + " the song", t.extra)
        if imgui.is_item_hovered():
            imgui.set_tooltip("Off: the title is shown over the picture while the song plays.\n"
                              "On: the title gets its own time, in silence, before or after the song.")
        imgui.set_next_item_width(140)
        _, t.fade = imgui.slider_float("fade", t.fade, 0.0, 3.0, "%.1f s")
        imgui.separator_text("Look")
        ch, k = imgui.combo("position", POSITIONS.index(t.position) if t.position in POSITIONS else 1,
                            ["upper third", "middle", "lower third"])
        if ch:
            t.position = POSITIONS[k]
        imgui.set_next_item_width(140)
        _, t.size = imgui.slider_float("text size", t.size, 0.03, 0.3, "%.2f")
        ch, c = imgui.color_edit3("text colour", rgb01(t.text_colour), imgui.ColorEditFlags_.no_inputs)
        if ch:
            t.text_colour = to_hex(c)
        imgui.same_line()
        _, t.shadow = imgui.checkbox("shadow", t.shadow)
        fonts = font_names()
        if imgui.begin_combo("font", t.font or "Built-in"):
            imgui.set_next_item_width(-1)
            _, self.font_filter = imgui.input_text("##filter", self.font_filter)
            if imgui.selectable("Built-in", not t.font)[0]:
                t.font = ""
            needle = self.font_filter.lower()
            shown = 0
            for name in fonts:
                if needle and needle not in name.lower():
                    continue
                if imgui.selectable(name, name == t.font)[0]:
                    t.font = name
                shown += 1
                if shown > 300:
                    imgui.text_disabled("type to find more")
                    break
            imgui.end_combo()
        ch, k = imgui.combo("background", BACKGROUNDS.index(t.background) if t.background in BACKGROUNDS else 0,
                            ["none (over the picture)", "colour", "picture"])
        if ch:
            t.background = BACKGROUNDS[k]
        if t.background == "colour" or (t.extra and t.background == "none"):
            ch, c = imgui.color_edit3("background colour", rgb01(t.colour), imgui.ColorEditFlags_.no_inputs)
            if ch:
                t.colour = to_hex(c)
                if t.background == "none":
                    t.background = "colour"
        if t.background == "image":
            if imgui.button("Choose picture..."):
                self.ask(f"title_image:{i}")
            imgui.same_line()
            imgui.text_disabled(Path(t.image).name if t.image else "(none)")
        imgui.dummy(V2(0, 6))
        if imgui.small_button("move up") and i > 0:
            p.titles[i - 1], p.titles[i] = p.titles[i], p.titles[i - 1]
            self.sel_title = i - 1
        imgui.same_line()
        if imgui.small_button("move down") and i < len(p.titles) - 1:
            p.titles[i + 1], p.titles[i] = p.titles[i], p.titles[i + 1]
            self.sel_title = i + 1
        imgui.same_line()
        if imgui.small_button("show it") and self.composer:
            for pl in self.composer.program.placed:
                if pl.index == i:
                    self.seek(pl.t0 + min(pl.t1 - pl.t0, max(pl.title.fade, 0.0) + 0.3))
        imgui.same_line()
        if imgui.small_button("delete"):
            del p.titles[i]
            self.sel_title = max(0, i - 1)
        imgui.pop_id()

    def render_tab(self) -> None:
        p = self.project
        ready = self.session is not None
        prog = self.composer.program if self.composer else None
        imgui.text(self.format_label())
        if prog:
            mbits = formats.bitrate(p.width, p.height, p.fps, p.output.get("quality", "good")) / 1e6 + 0.19
            imgui.text_disabled(f"{clock(prog.duration)} long, about {mbits * prog.duration / 8:.0f} MB")
        imgui.begin_disabled(not ready)
        if imgui.button("Render video", V2(150, 32)):
            self.start_renders(self.render_list(False))
        imgui.same_line()
        if imgui.button("Render as...", V2(110, 32)):
            self.ask("render_as")
        imgui.end_disabled()
        imgui.separator_text("Also render these sizes")
        queue = p.output.setdefault("queue", [])
        group = ""
        for f in formats.FORMATS:
            if f.key == p.output.get("format"):
                continue
            if f.group != group:
                group = f.group
                imgui.text_disabled(group)
            ch, v = imgui.checkbox(f"{f.name} ({f.width}x{f.height})##q{f.key}", f.key in queue)
            if ch:
                if v:
                    queue.append(f.key)
                else:
                    queue.remove(f.key)
        items = self.render_list(True)
        imgui.begin_disabled(not ready or len(items) < 2)
        if imgui.button(f"Render all {len(items)} sizes", V2(180, 30)):
            self.start_renders(items)
        imgui.end_disabled()
        if imgui.is_item_hovered():
            imgui.set_tooltip("The current size and every one ticked, one after the other, into the renders folder.")
        imgui.separator_text("Rendered")
        out_dir = self.dir / library.RENDERS
        files = sorted(out_dir.glob("*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True) if out_dir.exists() else []
        for f in files[:12]:
            imgui.bullet_text(f"{f.name}  ({f.stat().st_size / 1e6:.0f} MB)")
        if imgui.button("Show the renders folder"):
            out_dir.mkdir(parents=True, exist_ok=True)
            reveal(out_dir)

    # ---- shard effects -------------------------------------------------------

    def shard_fx_window(self) -> None:
        if not self.need_project():
            return
        p = self.project
        imgui.text_wrapped("Effects that strike now and then. How often, whether they land on the pulses "
                           "(the beats and hits) or between them, and how hard they hit the picture.")
        if imgui.button("All off"):
            p.shard_fx = {}
        imgui.same_line()
        if imgui.button("Surprise me"):
            rng = random.Random()
            p.shard_fx = {name: {"rate": round(rng.uniform(0.15, 0.5), 2), "sync": rng.choice([-1.0, 0.0, 1.0]),
                                 "strength": round(rng.uniform(0.5, 1.0), 2)}
                          for name in rng.sample(events.NAMES, 4)}
        imgui.same_line()
        on = sum(1 for n in events.NAMES if events.setting(p, n)["rate"] > 0)
        imgui.text_disabled(f"{on} on")
        flags = imgui.TableFlags_.sizing_stretch_prop | imgui.TableFlags_.row_bg | imgui.TableFlags_.pad_outer_x
        for group in events.GROUPS:
            imgui.separator_text(group)
            if not imgui.begin_table(f"shardfx_{group}", 5, flags):
                continue
            imgui.table_setup_column("effect", imgui.TableColumnFlags_.width_fixed, 88)
            imgui.table_setup_column("how often", 0, 1.0)
            imgui.table_setup_column("pulse", 0, 1.25)
            imgui.table_setup_column("strength", 0, 1.0)
            imgui.table_setup_column("", imgui.TableColumnFlags_.width_fixed, 30)
            if group == events.GROUPS[0]:
                imgui.table_headers_row()
            for name in (n for n in events.NAMES if events.EVENTS[n].group == group):
                self.shard_fx_row(name)
            imgui.end_table()
        imgui.spacing()
        imgui.text_disabled("Strikes fall the same way every time you play or render.")

    def shard_fx_row(self, name: str) -> None:
        ev = events.EVENTS[name]
        p = self.project
        s = events.setting(p, name)
        imgui.push_id(name)
        imgui.table_next_row()
        imgui.table_next_column()
        imgui.align_text_to_frame_padding()
        if s["rate"] > 0:
            imgui.text(ev.label)
        else:
            imgui.text_disabled(ev.label)
        if imgui.is_item_hovered():
            imgui.set_tooltip(ev.about)
        imgui.table_next_column()
        imgui.set_next_item_width(-1)
        ch1, s["rate"] = imgui.slider_float("##rate", float(s["rate"]), 0.0, 1.0,
                                            "off" if s["rate"] <= 0 else f"{s['rate'] * 100:.0f}%%")
        imgui.table_next_column()
        imgui.set_next_item_width(-1)
        sync = float(s["sync"])
        label = ("between" if sync <= -0.95 else "on pulse" if sync >= 0.95 else "any" if abs(sync) < 0.05
                 else f"{'betw.' if sync < 0 else 'on'} {abs(sync) * 100:.0f}%%")
        ch2, s["sync"] = imgui.slider_float("##sync", sync, -1.0, 1.0, label)
        if imgui.is_item_hovered():
            imgui.set_tooltip("Left: strikes between the beats. Middle: anywhere. Right: on the beats and hits.")
        imgui.table_next_column()
        imgui.set_next_item_width(-1)
        ch3, s["strength"] = imgui.slider_float("##strength", float(s["strength"]), 0.0, 1.0, "%.2f")
        if ch1 or ch2 or ch3:
            p.shard_fx[name] = {k: round(float(v), 3) for k, v in s.items()}
        imgui.table_next_column()
        if imgui.small_button("try"):
            self.try_shard_fx(name)
        if imgui.is_item_hovered():
            imgui.set_tooltip("Set it off now in the preview (not saved)")
        imgui.pop_id()

    def try_shard_fx(self, name: str) -> None:
        if not self.player:
            self.say("Cut the shards first.")
            return
        if self.mode == "Perform":
            t = self.player.t
        else:
            prog = self.program()
            t = prog.song_time(self.T)
            if t is None:
                t = prog.song_start
                self.seek(prog.program_time(t))
            if self.audio and not self.audio.playing:
                self.play()
        self.player.engine.events.fire(name, t)

    # ---- the instrument's windows ------------------------------------------

    def player_window(self, name: str) -> None:
        if self.player and not self.render_job:
            self.player.window(name)
            return
        imgui.begin(name)
        if self.render_job:
            imgui.text_disabled("Rendering...")
        else:
            imgui.text_disabled("The instrument opens once the song and shards are ready (Edit workspace).")
        imgui.end()

    def before_exit(self) -> None:
        if self.render_job:
            self.render_job.cancel()
        if self.project and self.dir:
            self.dirty = json.dumps(self.project.to_dict(), sort_keys=True) != self._saved
            if self.dirty:
                self.save()
                print(f"Saved changes to {self.dir / 'project.json'}.")
        self.drop_session()


def _split(initial: str, new: str, direction, ratio: float) -> hello_imgui.DockingSplit:
    s = hello_imgui.DockingSplit()
    s.initial_dock, s.new_dock, s.direction, s.ratio = initial, new, direction, ratio
    return s


def _window(label: str, dock: str, gui, begin_end: bool = True) -> hello_imgui.DockableWindow:
    w = hello_imgui.DockableWindow()
    w.label, w.dock_space_name, w.gui_function = label, dock, gui
    w.call_begin_end = begin_end
    return w


def layouts(editor: Editor) -> tuple[hello_imgui.DockingParams, hello_imgui.DockingParams]:
    edit = hello_imgui.DockingParams()
    edit.layout_name = "Edit"
    edit.docking_splits = [
        _split("MainDockSpace", "Left", imgui.Dir.left, 0.25),
        _split("MainDockSpace", "Right", imgui.Dir.right, 0.33),
        _split("MainDockSpace", "Bottom", imgui.Dir.down, 0.27),
    ]
    edit.dockable_windows = [
        _window("Media", "Left", editor.media_window),
        _window("Preview", "MainDockSpace", editor.preview_window),
        _window("Timeline", "Bottom", editor.timeline_window),
        _window("Video settings", "Right", editor.settings_window),
        _window("Shard effects", "Right", editor.shard_fx_window),
    ]
    edit.layout_condition = hello_imgui.DockingLayoutCondition.application_start

    perform = hello_imgui.DockingParams()
    perform.layout_name = "Perform"
    perform.docking_splits = [
        _split("MainDockSpace", "PRight", imgui.Dir.right, 0.34),
        _split("MainDockSpace", "PBottom", imgui.Dir.down, 0.38),
        _split("PRight", "PRightBottom", imgui.Dir.down, 0.62),
        _split("MainDockSpace", "PLeft", imgui.Dir.left, 0.2),
    ]
    dock = {"Output": "MainDockSpace", "Transport": "PRight", "Edit": "PRightBottom", "Sequencer": "PBottom",
            "Music": "PBottom", "Pool": "PLeft"}
    perform.dockable_windows = [_window(n, dock[n], lambda n=n: editor.player_window(n), False) for n in PLAYER_WINDOWS] + [
        _window("Shard effects##perform", "PRightBottom", editor.shard_fx_window)]
    perform.layout_condition = hello_imgui.DockingLayoutCondition.application_start
    return edit, perform


def run_editor(folder: str | None = None, layout: str = "Edit", screenshot: str | None = None,
               screenshot_after: float = 4.0) -> None:
    editor = Editor(folder, layout)
    params = hello_imgui.RunnerParams()
    params.app_window_params.window_title = "Shardloom"
    params.app_window_params.window_geometry.size = (1440, 900)
    params.app_window_params.restore_previous_geometry = True
    params.imgui_window_params.default_imgui_window_type = hello_imgui.DefaultImGuiWindowType.provide_full_screen_dock_space
    params.imgui_window_params.show_menu_bar = True
    params.imgui_window_params.show_menu_app = False
    params.imgui_window_params.show_menu_view = True
    params.imgui_window_params.show_status_bar = True
    params.imgui_window_params.show_status_fps = True
    params.fps_idling.enable_idling = False
    params.ini_folder_type = hello_imgui.IniFolderType.app_user_config_folder
    params.ini_filename = "shardloom-editor.ini"
    params.docking_params, perform = layouts(editor)
    params.alternative_docking_layouts = [perform]
    params.remember_selected_alternative_layout = False
    params.callbacks.post_init = editor.post_init
    params.callbacks.post_new_frame = editor.frame_start
    params.callbacks.show_gui = editor.gui
    params.callbacks.show_menus = editor.menus
    params.callbacks.show_status = editor.status
    params.callbacks.before_exit = editor.before_exit
    editor.params = params
    if screenshot:
        params.app_window_params.restore_previous_geometry = False
        began = time.monotonic()

        def grab():
            # Wait for the background work to finish, then a little more.
            if editor.tasks or time.monotonic() - began < screenshot_after:
                return
            if editor.mode == "Edit" and editor.audio and not editor.audio.playing:
                editor.play()
            elif editor.mode == "Perform" and editor.player and not editor.player.audio.playing:
                editor.player.play()
            if time.monotonic() - began < screenshot_after + 1.5:
                return
            from PIL import Image

            io = imgui.get_io()
            w = int(io.display_size.x * io.display_framebuffer_scale.x)
            h = int(io.display_size.y * io.display_framebuffer_scale.y)
            data = editor.ctx.screen.read(viewport=(0, 0, w, h), components=3, alignment=1)
            Image.frombytes("RGB", (w, h), data).transpose(Image.Transpose.FLIP_TOP_BOTTOM).save(screenshot)
            params.app_shall_exit = True

        params.callbacks.before_swap = grab
    immapp.run(params)
