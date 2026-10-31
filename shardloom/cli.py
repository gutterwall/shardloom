"""shardloom: command line."""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

from . import __version__

ANALYSIS = "analysis"


def _bar(label: str):
    def show(done: int, total: int) -> None:
        width = 30
        filled = int(width * done / max(1, total))
        end = "\n" if done >= total else ""
        print(f"\r{label} [{'#' * filled}{'.' * (width - filled)}] {done}/{total}", end=end, file=sys.stderr, flush=True)
    return show


def _size(text: str) -> tuple[int, int]:
    try:
        w, h = (int(v) for v in text.lower().split("x"))
    except ValueError:
        raise argparse.ArgumentTypeError("size must look like 640x360")
    if w < 16 or h < 16 or w % 2 or h % 2:
        raise argparse.ArgumentTypeError("size must be even and at least 16x16")
    return w, h


class Session:
    """A project directory opened: project, pool frames, analysis and engine."""

    def __init__(self, directory: str | Path, project=None):
        from .analysis import Analysis
        from .sequencer import Engine, Project
        from .slicer import PoolFrames

        self.dir = Path(directory)
        if project is None and not (self.dir / "project.json").exists():
            raise SystemExit(f"{self.dir}: not a Shardloom project (no project.json); create one with 'shardloom new'")
        self.project = project or Project.load(self.dir)
        self.pool = PoolFrames(self.dir / self.project.pool)
        if not self.pool.shards:
            raise SystemExit(f"{self.dir}: the pool is empty; add shards with 'shardloom slice'")
        if not (self.dir / f"{ANALYSIS}.json").exists():
            raise SystemExit(f"{self.dir}: no music analysis; run 'shardloom analyse'")
        self.analysis = Analysis.load(self.dir / ANALYSIS)
        self.engine = Engine(self.project, self.pool.shards, self.analysis,
                             {s.id: self.pool.length(s.id) for s in self.pool.shards}, self.pool.fps)

    @property
    def music_path(self) -> Path:
        from .sequencer import project_path

        return project_path(self.dir, self.project.music)

    def save(self) -> None:
        from .library import save_project

        save_project(self.project, self.dir)

    def close(self) -> None:
        """Lets go of the pool's files (so they can be replaced, on Windows too)."""
        self.pool._frames.clear()


def cmd_new(args) -> None:
    from . import formats, library
    from .sequencer import starter_project

    d = Path(args.dir)
    if (d / "project.json").exists() and not args.force:
        raise SystemExit(f"{d}: a project is already there (use --force to replace it)")
    d.mkdir(parents=True, exist_ok=True)
    seed = args.seed if args.seed is not None else random.randint(1, 99999)
    proj = starter_project("", library.POOL, args.lanes, seed)
    proj.cut.update(count=args.count, min=args.min, max=args.max, seed=seed, detail=args.detail)
    if args.size:
        proj.output["format"] = "custom"
        proj.width, proj.height = args.size
    else:
        f = formats.BY_KEY[args.format]
        proj.output["format"] = f.key
        proj.width, proj.height = f.width, f.height
    proj.fps = args.fps
    copy = not args.link
    proj.music = library.import_file(d, args.music) if copy else str(Path(args.music).resolve())
    for v in args.video:
        library.add_source(proj, d, v, copy)
    pw, ph = formats.pool_size(proj.width, proj.height, args.detail)
    print(f"Cutting shards from {len(args.video)} file(s) at {pw}x{ph}...", file=sys.stderr)
    shards = library.build_pool(proj, d, pw, ph, progress=_bar("shards"))
    print(f"Analysing {Path(args.music).name}...", file=sys.stderr)
    a = library.analyse_music(proj, d)
    proj.save(d)
    print(f"{d}: {len(shards)} shards, {a.duration:.0f} s of music at {a.tempo:.1f} BPM ({len(a.beats)} beats).")
    print(f"Edit it:   shardloom edit {d}")
    print(f"Export it: shardloom render {d} -o video.mp4")


def cmd_build(args) -> None:
    """Rebuilds what is missing or out of date: the shards and the song analysis."""
    from . import formats, library
    from .sequencer import Project

    d = Path(args.dir)
    proj = Project.load(d)
    library.adopt_pool_sources(proj, d)
    pw, ph = formats.pool_size(proj.width, proj.height, int(proj.cut.get("detail", 540)))
    todo = library.needs_build(proj, d, pw, ph)
    if args.force:
        todo = {"pool": bool(proj.sources), "analysis": bool(proj.music)}
    if todo["analysis"]:
        print("Analysing the song...", file=sys.stderr)
        library.analyse_music(proj, d)
    if todo["pool"]:
        shards = library.build_pool(proj, d, pw, ph, progress=_bar("shards"))
        print(f"Cut {len(shards)} shards.")
    library.save_project(proj, d)
    if not any(todo.values()):
        print("Everything is up to date.")


def cmd_pack(args) -> None:
    from . import library
    from .sequencer import Project

    d = Path(args.dir)
    out = args.output or str(d.resolve()) + ".zip"
    library.pack(Project.load(d), d, out, shards=args.with_shards, progress=_bar("files"))
    print(f"Packed into {out}")


def cmd_unpack(args) -> None:
    from . import library

    d = library.unpack(args.zip, args.dir)
    print(f"Unpacked into {d}")
    cmd_build(argparse.Namespace(dir=str(d), force=False))


def cmd_slice(args) -> None:
    from .slicer import pool_format, slice_videos

    s = Session(args.dir)
    fmt = pool_format(s.dir / s.project.pool)
    shards = slice_videos(args.video, s.dir / s.project.pool, args.count, args.min, args.max, fmt["width"],
                          fmt["height"], fmt["fps"], args.seed, append=not args.replace, progress=_bar("shards"))
    print(f"The pool now has {len(shards)} shards.")


def cmd_analyse(args) -> None:
    from . import library
    from .sequencer import Project

    d = Path(args.dir)
    proj = Project.load(d)
    if args.music:
        proj.music = library.import_file(d, args.music)
    a = library.analyse_music(proj, d)
    proj.save(d)
    print(f"{a.duration:.0f} s at {a.tempo:.1f} BPM, {len(a.beats)} beats.")


def cmd_info(args) -> None:
    s = Session(args.dir)
    p, a = s.project, s.engine.analysis
    print(f"music     {p.music}")
    print(f"tempo     {a.tempo * p.tempo_scale:.1f} BPM" + (" (fixed grid)" if p.grid else " (tracked beats)")
          + (f", x{p.tempo_scale:g}" if p.tempo_scale != 1 else ""))
    print(f"length    {a.duration:.1f} s, {len(a.beats)} beats")
    print(f"pool      {len(s.pool.shards)} shards at {s.pool.width}x{s.pool.height}, {s.pool.fps:g} fps")
    print(f"lanes     " + ", ".join(f"{l.name} ({l.blend}, {l.choice})" for l in p.lanes))
    print(f"patterns  " + ", ".join(f"{pt.name} ({pt.length} steps)" for pt in p.patterns))
    if p.song:
        print("song      " + " ".join(f"{p.patterns[e['pattern']].name}x{e.get('repeats', 1)}" for e in p.song))
    if p.performance:
        print(f"recorded  {len(p.performance)} live moves")


def cmd_render(args) -> None:
    from . import formats
    from .export import project_exporter, run
    from .renderer import headless_context

    s = Session(args.dir)
    size = args.size or ((formats.BY_KEY[args.format].width, formats.BY_KEY[args.format].height) if args.format else None)
    out = args.output or str(s.dir / "renders" / f"{s.dir.resolve().name}.mp4")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    job = project_exporter(s, headless_context(), out, size=size, fps=args.fps, quality=args.quality,
                           start=args.start, end=args.end, titles=not args.no_titles)
    info = run(job, _bar("frames"))
    print(f"{out}: {info['seconds']:.1f} s, {info['frames']} frames, rendered at {info['fps']:.0f} fps.")


def cmd_edit(args) -> None:
    from .editor import run_editor

    run_editor(args.dir, layout=args.layout, screenshot=args.screenshot, screenshot_after=args.after)


def cmd_play(args) -> None:
    from .player import run

    run(Session(args.dir), fullscreen=args.fullscreen, start=args.start, screenshot=args.screenshot,
        screenshot_after=args.after, autoplay=args.autoplay)


def _hide_own_console() -> None:
    """On Windows, hides the console window when it is ours alone (started by a double-click)."""
    if sys.platform != "win32":
        return
    import ctypes

    kernel = ctypes.windll.kernel32
    if kernel.GetConsoleProcessList((ctypes.c_uint * 2)(), 2) == 1:
        ctypes.windll.user32.ShowWindow(kernel.GetConsoleWindow(), 0)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="shardloom", description="A step-sequenced video instrument.")
    ap.add_argument("--version", action="version", version=f"shardloom {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    def cut_options(p, count_default):
        p.add_argument("--count", type=int, default=count_default, help="how many shards to cut")
        p.add_argument("--min", type=float, default=0.25, help="shortest shard, seconds")
        p.add_argument("--max", type=float, default=1.5, help="longest shard, seconds")
        p.add_argument("--seed", type=int, default=None, help="same seed, same cuts")

    from .formats import BY_KEY, DETAIL

    p = sub.add_parser("edit", help="the editor (the same as running shardloom with nothing after it)")
    p.add_argument("dir", nargs="?", help="a project folder, or a packed project (.zip)")
    p.add_argument("--layout", choices=["Edit", "Perform"], default="Edit")
    p.add_argument("--screenshot", help=argparse.SUPPRESS)
    p.add_argument("--after", type=float, default=4.0, help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_edit)

    p = sub.add_parser("new", help="create a project: copy in a song, videos and photos, cut shards, analyse the song")
    p.add_argument("dir")
    p.add_argument("--music", required=True)
    p.add_argument("--video", required=True, nargs="+", help="videos and photos")
    cut_options(p, 48)
    p.add_argument("--lanes", type=int, default=3)
    p.add_argument("--format", choices=list(BY_KEY), default="youtube-1080", help="output size")
    p.add_argument("--size", type=_size, default=None, help="a custom output size instead, e.g. 1280x720")
    p.add_argument("--detail", type=int, choices=list(DETAIL), default=540, help="shard frame size (short side)")
    p.add_argument("--fps", type=float, default=30.0)
    p.add_argument("--link", action="store_true", help="use the files where they are instead of copying them in")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("build", help="rebuild the shards and the song analysis if they are missing or out of date")
    p.add_argument("dir")
    p.add_argument("--force", action="store_true", help="rebuild both anyway")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("pack", help="pack a project and its media into one .zip")
    p.add_argument("dir")
    p.add_argument("-o", "--output")
    p.add_argument("--with-shards", action="store_true", help="include the cut shards (bigger, opens faster)")
    p.set_defaults(func=cmd_pack)

    p = sub.add_parser("unpack", help="unpack a packed project and rebuild its shards")
    p.add_argument("zip")
    p.add_argument("dir", nargs="?")
    p.set_defaults(func=cmd_unpack)

    p = sub.add_parser("slice", help="cut more shards into a project's pool")
    p.add_argument("dir")
    p.add_argument("video", nargs="+")
    cut_options(p, 16)
    p.add_argument("--replace", action="store_true", help="replace the pool instead of adding to it")
    p.set_defaults(func=cmd_slice)

    p = sub.add_parser("analyse", aliases=["analyze"], help="analyse the project's song again, or a new one")
    p.add_argument("dir")
    p.add_argument("--music")
    p.set_defaults(func=cmd_analyse)

    p = sub.add_parser("info", help="describe a project")
    p.add_argument("dir")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("render", help="export the project to an MP4")
    p.add_argument("dir")
    p.add_argument("-o", "--output")
    p.add_argument("--start", type=float, default=None, help="song time to start from (default: the project's)")
    p.add_argument("--end", type=float, default=None)
    p.add_argument("--fps", type=float, default=None)
    p.add_argument("--format", choices=list(BY_KEY), default=None, help="output size (default: the project's)")
    p.add_argument("--size", type=_size, default=None)
    p.add_argument("--quality", choices=["draft", "good", "best"], default=None)
    p.add_argument("--no-titles", action="store_true")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("play", help="the live instrument")
    p.add_argument("dir")
    p.add_argument("--fullscreen", action="store_true")
    p.add_argument("--start", type=float, default=0.0)
    p.add_argument("--autoplay", action="store_true")
    p.add_argument("--screenshot", help=argparse.SUPPRESS)
    p.add_argument("--after", type=float, default=3.0, help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_play)

    if argv is None:
        argv = sys.argv[1:]
    if not argv or (len(argv) == 1 and Path(argv[0]).suffix.lower() in (".zip", ".json")
                    or len(argv) == 1 and Path(argv[0], "project.json").exists()):
        # No arguments (double-clicked), or a project dropped on the program: the editor.
        _hide_own_console()
        from .editor import run_editor

        target = argv[0] if argv else None
        if target and Path(target).name == "project.json":
            target = str(Path(target).parent)
        run_editor(target)
        return
    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
