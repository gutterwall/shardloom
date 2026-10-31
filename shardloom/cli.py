"""shardloom: command line."""

from __future__ import annotations

import argparse
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

    def __init__(self, directory: str | Path):
        from .analysis import Analysis
        from .sequencer import Engine, Project
        from .slicer import PoolFrames

        self.dir = Path(directory)
        if not (self.dir / "project.json").exists():
            raise SystemExit(f"{self.dir}: not a Shardloom project (no project.json); create one with 'shardloom new'")
        self.project = Project.load(self.dir)
        self.pool = PoolFrames(self.dir / self.project.pool)
        if not self.pool.shards:
            raise SystemExit(f"{self.dir}: the pool is empty; add shards with 'shardloom slice'")
        if not (self.dir / f"{ANALYSIS}.json").exists():
            raise SystemExit(f"{self.dir}: no music analysis; run 'shardloom analyse'")
        self.analysis = Analysis.load(self.dir / ANALYSIS)
        self.engine = Engine(self.project, self.pool.shards, self.analysis,
                             {s.id: self.pool.length(s.id) for s in self.pool.shards}, self.pool.fps)

    def save(self) -> None:
        self.project.save(self.dir)


def cmd_new(args) -> None:
    from .analysis import analyse_file
    from .sequencer import starter_project
    from .slicer import slice_videos

    d = Path(args.dir)
    if (d / "project.json").exists() and not args.force:
        raise SystemExit(f"{d}: a project is already there (use --force to replace it)")
    d.mkdir(parents=True, exist_ok=True)
    w, h = args.size
    print(f"Cutting {args.count} shards from {len(args.video)} video(s)...", file=sys.stderr)
    shards = slice_videos(args.video, d / "pool", args.count, args.min, args.max, w, h, args.fps, args.seed,
                          progress=_bar("shards"))
    print(f"Analysing {args.music}...", file=sys.stderr)
    a = analyse_file(args.music)
    a.save(d / ANALYSIS)
    proj = starter_project(str(Path(args.music).resolve()), "pool", args.lanes, args.seed if args.seed is not None else 1)
    proj.width, proj.height, proj.fps = w, h, args.fps
    proj.save(d)
    print(f"{d}: {len(shards)} shards, {a.duration:.0f} s of music at {a.tempo:.1f} BPM ({len(a.beats)} beats).")
    print(f"Play it:   shardloom play {d}")
    print(f"Export it: shardloom render {d} -o video.mp4")


def cmd_slice(args) -> None:
    from .slicer import pool_format, slice_videos

    s = Session(args.dir)
    fmt = pool_format(s.dir / s.project.pool)
    shards = slice_videos(args.video, s.dir / s.project.pool, args.count, args.min, args.max, fmt["width"],
                          fmt["height"], fmt["fps"], args.seed, append=not args.replace, progress=_bar("shards"))
    print(f"The pool now has {len(shards)} shards.")


def cmd_analyse(args) -> None:
    from .analysis import analyse_file
    from .sequencer import Project

    d = Path(args.dir)
    proj = Project.load(d)
    if args.music:
        proj.music = str(Path(args.music).resolve())
    a = analyse_file(proj.music)
    a.save(d / ANALYSIS)
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
    from .export import export
    from .renderer import Renderer, headless_context

    s = Session(args.dir)
    w, h = args.size or (s.project.width, s.project.height)
    r = Renderer(headless_context(), w, h, s.pool)
    out = args.output or str(s.dir / "render.mp4")
    info = export(s.engine, r, out, args.start, args.end, args.fps, bitrate=args.bitrate * 1000, progress=_bar("frames"))
    print(f"{out}: {info['seconds']:.1f} s, {info['frames']} frames, rendered at {info['fps']:.0f} fps.")


def cmd_play(args) -> None:
    from .player import run

    run(Session(args.dir), fullscreen=args.fullscreen, start=args.start, screenshot=args.screenshot,
        screenshot_after=args.after, autoplay=args.autoplay)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="shardloom", description="A step-sequenced video instrument.")
    ap.add_argument("--version", action="version", version=f"shardloom {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    def cut_options(p, count_default):
        p.add_argument("--count", type=int, default=count_default, help="how many shards to cut")
        p.add_argument("--min", type=float, default=0.25, help="shortest shard, seconds")
        p.add_argument("--max", type=float, default=1.5, help="longest shard, seconds")
        p.add_argument("--seed", type=int, default=None, help="same seed, same cuts")

    p = sub.add_parser("new", help="create a project: cut shards from videos and analyse a song")
    p.add_argument("dir")
    p.add_argument("--music", required=True)
    p.add_argument("--video", required=True, nargs="+")
    cut_options(p, 48)
    p.add_argument("--lanes", type=int, default=3)
    p.add_argument("--size", type=_size, default=(640, 360), help="frame size, e.g. 640x360")
    p.add_argument("--fps", type=float, default=30.0)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_new)

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
    p.add_argument("--start", type=float, default=0.0)
    p.add_argument("--end", type=float, default=None)
    p.add_argument("--fps", type=float, default=None)
    p.add_argument("--size", type=_size, default=None)
    p.add_argument("--bitrate", type=int, default=8000, help="video kbit/s")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("play", help="the live instrument")
    p.add_argument("dir")
    p.add_argument("--fullscreen", action="store_true")
    p.add_argument("--start", type=float, default=0.0)
    p.add_argument("--autoplay", action="store_true")
    p.add_argument("--screenshot", help=argparse.SUPPRESS)
    p.add_argument("--after", type=float, default=3.0, help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_play)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
