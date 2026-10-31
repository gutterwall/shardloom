"""The start window: make a project from a song and videos, or open one. Shown when shardloom runs
without arguments (double-clicking it on Windows)."""

from __future__ import annotations

import threading
import traceback
from pathlib import Path

from imgui_bundle import hello_imgui, imgui, immapp
from imgui_bundle import portable_file_dialogs as pfd

MUSIC = ["Music", "*.mp3 *.wav *.flac *.ogg *.m4a *.aac *.opus", "All files", "*"]
VIDEO = ["Video", "*.mp4 *.mov *.mkv *.webm *.avi *.m4v *.mpg", "All files", "*"]


def V2(x, y):
    return imgui.ImVec2(float(x), float(y))


class Launcher:
    def __init__(self):
        self.music = ""
        self.videos: list[str] = []
        self.folder = str(Path.home() / "Shardloom" / "my-video")
        self.count, self.min_len, self.max_len, self.lanes = 48, 0.25, 1.5, 3
        self.dialog = None
        self.dialog_kind = ""
        self.worker: threading.Thread | None = None
        self.status = ""
        self.progress = 0.0
        self.error = ""
        self.result: str | None = None
        self.params: hello_imgui.RunnerParams | None = None

    # ---- dialogs (asynchronous: polled every frame) --------------------

    def ask(self, kind: str) -> None:
        if self.dialog is not None:
            return
        self.dialog_kind = kind
        if kind == "music":
            self.dialog = pfd.open_file("Choose the song", str(Path.home()), MUSIC)
        elif kind == "videos":
            self.dialog = pfd.open_file("Choose videos to cut up", str(Path.home()), VIDEO, pfd.opt.multiselect)
        elif kind == "folder":
            self.dialog = pfd.select_folder("Where to keep the project", str(Path(self.folder).parent))
        elif kind == "open":
            self.dialog = pfd.select_folder("Open a Shardloom project", str(Path.home()))

    def poll(self) -> None:
        if self.dialog is None or not self.dialog.ready():
            return
        result = self.dialog.result()
        self.dialog = None
        if not result:
            return
        if self.dialog_kind == "music":
            self.music = result[0]
            if self.folder.endswith("my-video"):
                self.folder = str(Path.home() / "Shardloom" / Path(self.music).stem)
        elif self.dialog_kind == "videos":
            self.videos += [v for v in result if v not in self.videos]
        elif self.dialog_kind == "folder":
            chosen = Path(result)
            # An empty folder is used as it is; otherwise the project gets its own folder inside it.
            empty = not any(chosen.iterdir())
            self.folder = str(chosen if empty else chosen / Path(self.folder).name)
        elif self.dialog_kind == "open":
            if Path(result, "project.json").exists():
                self.finish(result)
            else:
                self.error = f"{result} is not a Shardloom project (no project.json)."

    def finish(self, folder: str) -> None:
        self.result = folder
        if self.params:
            self.params.app_shall_exit = True

    # ---- making the project ------------------------------------------

    def create(self) -> None:
        self.error = ""
        folder = Path(self.folder)
        if (folder / "project.json").exists():
            self.error = f"{folder} already holds a project: open it, or choose another folder."
            return

        def work():
            try:
                from .analysis import analyse_file
                from .cli import ANALYSIS
                from .sequencer import starter_project
                from .slicer import slice_videos

                folder.mkdir(parents=True, exist_ok=True)
                self.status = "Cutting shards"

                def shard_progress(done, total):
                    self.progress = 0.85 * done / total

                slice_videos(self.videos, folder / "pool", self.count, self.min_len, self.max_len,
                             progress=shard_progress)
                self.status = "Listening to the song"
                a = analyse_file(self.music)
                a.save(folder / ANALYSIS)
                starter_project(str(Path(self.music).resolve()), "pool", self.lanes).save(folder)
                self.progress = 1.0
                self.finish(str(folder))
            except Exception as e:
                traceback.print_exc()
                self.error = f"Could not make the project: {e}"
                self.status = ""

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    # ---- window --------------------------------------------------------

    def gui(self) -> None:
        self.poll()
        vp = imgui.get_main_viewport()
        imgui.set_next_window_pos(vp.pos)
        imgui.set_next_window_size(vp.size)
        imgui.begin("##start", None, imgui.WindowFlags_.no_decoration | imgui.WindowFlags_.no_move
                    | imgui.WindowFlags_.no_saved_settings)
        busy = self.worker is not None and self.worker.is_alive()
        imgui.push_font(None, 26.0)
        imgui.text("Shardloom")
        imgui.pop_font()
        imgui.text_disabled("Cut your footage into shards and play them to a song.")
        imgui.dummy(V2(0, 8))
        imgui.begin_disabled(busy)

        imgui.separator_text("1. The song")
        if imgui.button("Choose song...", V2(150, 0)):
            self.ask("music")
        imgui.same_line()
        imgui.text(Path(self.music).name if self.music else "(none)")

        imgui.separator_text("2. Videos to cut up")
        if imgui.button("Add videos...", V2(150, 0)):
            self.ask("videos")
        if self.videos:
            imgui.same_line()
            if imgui.small_button("clear"):
                self.videos = []
        for v in self.videos[:8]:
            imgui.bullet_text(Path(v).name)
        if len(self.videos) > 8:
            imgui.bullet_text(f"and {len(self.videos) - 8} more")

        imgui.separator_text("3. Shards")
        imgui.set_next_item_width(200)
        _, self.count = imgui.slider_int("how many", self.count, 4, 256)
        imgui.set_next_item_width(200)
        _, self.min_len = imgui.slider_float("shortest (s)", self.min_len, 0.05, 4.0, "%.2f")
        imgui.set_next_item_width(200)
        _, self.max_len = imgui.slider_float("longest (s)", max(self.max_len, self.min_len), self.min_len, 8.0, "%.2f")
        imgui.set_next_item_width(200)
        _, self.lanes = imgui.slider_int("layers", self.lanes, 1, 4)

        imgui.separator_text("4. Project folder")
        imgui.set_next_item_width(420)
        _, self.folder = imgui.input_text("##folder", self.folder)
        imgui.same_line()
        if imgui.button("Browse..."):
            self.ask("folder")

        imgui.dummy(V2(0, 8))
        ready = bool(self.music and self.videos and self.folder)
        imgui.begin_disabled(not ready)
        if imgui.button("Make it", V2(150, 34)):
            self.create()
        imgui.end_disabled()
        imgui.same_line()
        if imgui.button("Open a project...", V2(150, 34)):
            self.ask("open")
        imgui.end_disabled()

        if busy:
            imgui.dummy(V2(0, 6))
            imgui.text(self.status + "...")
            imgui.progress_bar(self.progress, V2(420, 0))
        if self.error:
            imgui.text_colored(imgui.ImVec4(1.0, 0.45, 0.4, 1.0), self.error)
        imgui.end()


def run_launcher() -> str | None:
    """Shows the start window; returns the chosen or new project folder, or None if closed."""
    launcher = Launcher()
    params = hello_imgui.RunnerParams()
    params.app_window_params.window_title = "Shardloom"
    params.app_window_params.window_geometry.size = (640, 620)
    params.imgui_window_params.default_imgui_window_type = hello_imgui.DefaultImGuiWindowType.no_default_window
    params.ini_disable = True
    params.callbacks.show_gui = launcher.gui
    launcher.params = params
    immapp.run(params)
    return launcher.result
