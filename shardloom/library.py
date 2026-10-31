"""A project's media: importing videos, photos and the song into its folder, cutting the pool from them,
and saving, copying and packing the project so it can be rebuilt anywhere.

A project folder holds everything needed to make the video again:

    project.json   the edit: sources, cut settings, sequencer, titles, output
    media/         copies of the song, videos, photos and title images
    pool/          the cut shards (made from media/, can be rebuilt)
    analysis.*     the song's beats and loudness (can be rebuilt)
    renders/       exported videos
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import zipfile
from pathlib import Path

import numpy as np

from .media import VideoDecoder, probe
from .sequencer import PROJECT_FILE, Project, Source, project_path
from .slicer import POOL_FILE, Shard, plan_cuts, rank_energy, save_pool, scores

MEDIA = "media"
POOL = "pool"
ANALYSIS = "analysis"
RENDERS = "renders"

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpg", ".mpeg", ".wmv", ".mts", ".m2ts"}
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
MUSIC_EXT = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".aif", ".aiff"}
MOTIONS = ["still", "zoom_in", "zoom_out", "pan", "random"]


def kind_of(path: str | os.PathLike) -> str | None:
    ext = Path(path).suffix.lower()
    return "video" if ext in VIDEO_EXT else "photo" if ext in PHOTO_EXT else "music" if ext in MUSIC_EXT else None


# ---- copying media in ------------------------------------------------------

def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.stat().st_size == b.stat().st_size and _digest(a) == _digest(b)
    except OSError:
        return False


def _digest(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        # The start, the end and the size are enough to tell media files apart.
        h.update(f.read(1 << 20))
        size = path.stat().st_size
        if size > 2 << 20:
            f.seek(size - (1 << 20))
            h.update(f.read())
    return h.hexdigest()


def import_file(directory: str | os.PathLike, path: str | os.PathLike) -> str:
    """Copies path into the project's media folder (once) and returns its path relative to the project."""
    directory = Path(directory)
    src = Path(path).resolve()
    media = directory / MEDIA
    try:
        return src.relative_to(directory.resolve()).as_posix()
    except ValueError:
        pass
    media.mkdir(parents=True, exist_ok=True)
    dest = media / src.name
    n = 2
    while dest.exists() and not _same_file(src, dest):
        dest = media / f"{src.stem}-{n}{src.suffix}"
        n += 1
    if not dest.exists():
        tmp = dest.with_name(dest.name + ".part")
        shutil.copyfile(src, tmp)
        tmp.replace(dest)
    return dest.relative_to(directory).as_posix()


def collect_media(project: Project, directory: str | os.PathLike) -> list[str]:
    """Copies every file the project uses from outside its folder into media/; returns what was missing."""
    missing = []

    def take(path: str) -> str:
        if not path:
            return path
        full = project_path(directory, path)
        if not full.exists():
            missing.append(path)
            return path
        return import_file(directory, full)

    project.music = take(project.music)
    for s in project.sources:
        s.path = take(s.path)
    for t in project.titles:
        if t.image:
            t.image = take(t.image)
    return missing


def adopt_pool_sources(project: Project, directory: str | os.PathLike) -> None:
    """For a project made before the editor: list the videos its pool was cut from as its sources."""
    if project.sources or not (Path(directory) / project.pool / POOL_FILE).exists():
        return
    data = json.loads((Path(directory) / project.pool / POOL_FILE).read_text())
    seen = []
    for s in data["shards"]:
        if s["source"] not in seen:
            seen.append(s["source"])
    project.sources = [Source(path=p, kind="video", id=f"s{i + 1}") for i, p in enumerate(q for q in seen if Path(q).exists())]


def new_id(project: Project) -> str:
    used = {s.id for s in project.sources}
    n = len(project.sources) + 1
    while f"s{n}" in used:
        n += 1
    return f"s{n}"


def prepare_source(directory: str | os.PathLike, path: str | os.PathLike, copy: bool = True) -> tuple[Source, dict]:
    """Copies a video or photo into the project and checks it opens; returns the source (not yet added) and
    what is known about it ({"duration", "width", "height"})."""
    kind = kind_of(path)
    if kind not in ("video", "photo"):
        raise ValueError(f"{Path(path).name}: not a video or a photo")
    rel = import_file(directory, path) if copy else str(Path(path).resolve())
    full = project_path(directory, rel)
    if kind == "video":
        meta = probe(full)
        if not meta["video"]:
            raise ValueError(f"{Path(path).name}: no video in it")
        info = {"duration": meta["duration"], "width": meta["video"]["width"], "height": meta["video"]["height"]}
    else:
        img = load_photo(full)   # fails early on a broken file
        info = {"duration": 0.0, "width": img.size[0], "height": img.size[1]}
    return Source(path=rel, kind=kind), info


def add_source(project: Project, directory: str | os.PathLike, path: str | os.PathLike, copy: bool = True) -> Source:
    src, _ = prepare_source(directory, path, copy)
    src.id = new_id(project)
    project.sources.append(src)
    return src


def thumbnail(directory: str | os.PathLike, src: Source, width: int = 160) -> np.ndarray:
    """A small picture of a source: (h, w, 3) uint8 at the source's own shape."""
    full = project_path(directory, src.path)
    if src.kind == "photo":
        img = load_photo(full)
        img.thumbnail((width, width))
        return np.asarray(img)
    meta = probe(full)
    v = meta["video"]
    height = max(2, int(round(width * v["height"] / max(1, v["width"]) / 2)) * 2)
    with VideoDecoder(full, width, height, 10.0) as d:
        return d.segment(min(d.duration * 0.25, max(0.0, d.duration - 0.2)), 0.1)[0]


# ---- photos -------------------------------------------------------------

def load_photo(path: str | os.PathLike):
    from PIL import Image, ImageOps

    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            flat = Image.new("RGB", img.size, (0, 0, 0))
            flat.paste(img, mask=img.split()[-1])
            return flat
        return img.convert("RGB")


def cover_box(sw: int, sh: int, width: int, height: int, zoom: float = 1.0, cx: float = 0.5, cy: float = 0.5):
    """The part of a sw x sh picture that covers width x height, zoomed in by zoom, centred at (cx, cy)."""
    aspect = width / height
    bw, bh = (sw, sw / aspect) if sw / sh < aspect else (sh * aspect, sh)
    bw, bh = bw / zoom, bh / zoom
    bw, bh = min(bw, sw), min(bh, sh)
    x0 = max(0.0, min(cx * sw - bw / 2, sw - bw))
    y0 = max(0.0, min(cy * sh - bh / 2, sh - bh))
    return (x0, y0, min(sw, x0 + bw), min(sh, y0 + bh))


def photo_frames(img, width: int, height: int, frames: int, motion: str, rng: random.Random) -> np.ndarray:
    """A shard from a photo: one frame when still, otherwise a slow zoom or pan over frames."""
    from PIL import Image

    if motion == "random":
        motion = rng.choice(["zoom_in", "zoom_out", "pan", "still"])
    sw, sh = img.size
    if motion == "still" or frames <= 1:
        box = cover_box(sw, sh, width, height)
        return np.asarray(img.resize((width, height), Image.Resampling.LANCZOS, box=box))[None]
    cx, cy = rng.uniform(0.4, 0.6), rng.uniform(0.4, 0.6)
    direction = rng.choice([-1, 1])
    out = np.empty((frames, height, width, 3), np.uint8)
    for k in range(frames):
        f = k / max(1, frames - 1)
        f = f * f * (3 - 2 * f)
        if motion == "zoom_in":
            box = cover_box(sw, sh, width, height, 1.0 + 0.15 * f, cx, cy)
        elif motion == "zoom_out":
            box = cover_box(sw, sh, width, height, 1.15 - 0.15 * f, cx, cy)
        else:
            box = cover_box(sw, sh, width, height, 1.12, 0.5 + direction * (f - 0.5) * 0.5, cy)
        out[k] = np.asarray(img.resize((width, height), Image.Resampling.BILINEAR, box=box))
    return out


# ---- the pool -------------------------------------------------------------

def recipe(project: Project, width: int, height: int) -> str:
    """A fingerprint of everything the pool is cut from: when it changes, the shards are out of date."""
    data = {
        "size": [width, height], "fps": project.fps,
        "cut": {k: project.cut.get(k) for k in ("count", "min", "max", "seed")},
        "sources": [[s.path, s.kind, s.id, s.weight, s.start, s.end, s.motion, s.shards]
                    for s in project.sources if s.enabled],
    }
    return hashlib.sha1(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]


def pool_recipe(directory: str | os.PathLike, pool: str = POOL) -> str | None:
    path = Path(directory) / pool / POOL_FILE
    if not path.exists():
        return None
    return json.loads(path.read_text()).get("recipe")


def build_pool(project: Project, directory: str | os.PathLike, width: int, height: int, progress=None,
               swap: bool = True) -> list[Shard]:
    """Cuts the pool from the project's enabled sources: random video cuts plus shards made from the photos.

    The same project and settings always cut the same pool, so a project can be rebuilt from its media.
    The new pool is cut beside the old one; with swap=False it is left there for finish_pool(), so a
    program still showing the old pool can let go of it first.
    """
    directory = Path(directory)
    pool_dir = directory / project.pool
    cut = project.cut
    fps = project.fps
    seed = int(cut.get("seed", 1))
    rng = random.Random(seed)
    min_len, max_len = float(cut.get("min", 0.25)), float(cut.get("max", 1.5))
    videos = [s for s in project.sources if s.enabled and s.kind == "video"]
    photos = [s for s in project.sources if s.enabled and s.kind == "photo"]
    if not videos and not photos:
        raise ValueError("add a video or a photo first")
    tmp_dir = pool_dir.with_name(pool_dir.name + ".new")
    shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True)
    shards: list[Shard] = []
    decoders = []
    try:
        for s in videos:
            decoders.append(VideoDecoder(project_path(directory, s.path), width, height, fps))
        spans = []
        for s, d in zip(videos, decoders):
            start = min(max(0.0, s.start), d.duration)
            end = d.duration if s.end is None else min(max(s.end, start), d.duration)
            spans.append((start, end - start))
        cuts = plan_cuts([length for _, length in spans], int(cut.get("count", 48)) if videos else 0,
                         min_len, max_len, rng, weights=[s.weight for s in videos])
        total = len(cuts) + sum(max(1, p.shards) for p in photos)
        done = 0

        def add(frames: np.ndarray, src: Source, start: float, length: float) -> None:
            nonlocal done
            sid = len(shards)
            name = f"{sid:04d}.npy"
            np.save(tmp_dir / name, frames)
            motion, brightness = scores(frames)
            shards.append(Shard(sid, name, str(project_path(directory, src.path)), start, length, len(frames),
                                motion, brightness, source_id=src.id))
            done += 1
            if progress:
                progress(done, total)

        for vi, start, length in cuts:
            add(decoders[vi].segment(spans[vi][0] + start, length), videos[vi], spans[vi][0] + start, length)
        for p in photos:
            img = load_photo(project_path(directory, p.path))
            for _ in range(max(1, p.shards)):
                length = rng.uniform(min_len, max_len)
                frames = photo_frames(img, width, height, max(1, int(round(length * fps))), p.motion, rng)
                add(frames, p, 0.0, length)
    except BaseException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    finally:
        for d in decoders:
            d.close()
    rank_energy(shards)
    save_pool(tmp_dir, shards, width, height, fps)
    meta = json.loads((tmp_dir / POOL_FILE).read_text())
    meta["recipe"] = recipe(project, width, height)
    (tmp_dir / POOL_FILE).write_text(json.dumps(meta, indent=1))
    if swap:
        finish_pool(project, directory)
    return shards


def finish_pool(project: Project, directory: str | os.PathLike) -> None:
    """Puts a pool cut by build_pool(swap=False) in place of the old one."""
    pool_dir = Path(directory) / project.pool
    tmp_dir = pool_dir.with_name(pool_dir.name + ".new")
    shards = json.loads((tmp_dir / POOL_FILE).read_text())["shards"]
    old = pool_dir.with_name(pool_dir.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if pool_dir.exists():
        pool_dir.rename(old)
    tmp_dir.rename(pool_dir)
    shutil.rmtree(old, ignore_errors=True)
    # Picks of shards that no longer exist fall back to the lane's choice.
    ids = {s["id"] for s in shards}
    for lane in project.lanes:
        if lane.shards is not None:
            lane.shards = [i for i in lane.shards if i in ids] or None
    for pat in project.patterns:
        for row in pat.steps:
            for step in row:
                if step.shard is not None and step.shard not in ids:
                    step.shard = None


def disk_estimate(project: Project, width: int, height: int) -> int:
    """Roughly how many bytes the pool will take."""
    frame = width * height * 3
    avg = (float(project.cut.get("min", 0.25)) + float(project.cut.get("max", 1.5))) / 2
    count = int(project.cut.get("count", 48)) if any(s.enabled and s.kind == "video" for s in project.sources) else 0
    frames = count * avg * project.fps
    for s in project.sources:
        if s.enabled and s.kind == "photo":
            frames += max(1, s.shards) * (1 if s.motion == "still" else avg * project.fps)
    return int(frames * frame)


# ---- music ---------------------------------------------------------------

def music_key(path: str | os.PathLike) -> str:
    p = Path(path)
    try:
        return f"{p.name}:{p.stat().st_size}"
    except OSError:
        return p.name


def analysis_current(project: Project, directory: str | os.PathLike) -> bool:
    meta = Path(directory) / f"{ANALYSIS}.json"
    if not project.music or not meta.exists() or not (Path(directory) / f"{ANALYSIS}.npz").exists():
        return False
    data = json.loads(meta.read_text())
    return data.get("key") == music_key(project_path(directory, project.music))


def analyse_music(project: Project, directory: str | os.PathLike):
    from .analysis import analyse_file

    path = project_path(directory, project.music)
    a = analyse_file(path)
    a.key = music_key(path)
    a.save(Path(directory) / ANALYSIS)
    return a


# ---- saving, copying and packing ------------------------------------------

def save_project(project: Project, directory: str | os.PathLike) -> list[str]:
    """Saves the project with every file it uses copied into its folder; returns any files that were missing."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    missing = collect_media(project, directory)
    project.save(directory)
    return missing


def save_as(project: Project, old: str | os.PathLike, new: str | os.PathLike, progress=None) -> None:
    """Copies the project to a new folder with its media, shards and analysis."""
    old, new = Path(old), Path(new)
    if (new / PROJECT_FILE).exists():
        raise FileExistsError(f"{new} already holds a project")
    new.mkdir(parents=True, exist_ok=True)
    # Make sure everything is inside the old folder first, so relative paths carry over.
    collect_media(project, old)
    names = [MEDIA, project.pool, f"{ANALYSIS}.json", f"{ANALYSIS}.npz"]
    files = [f for n in names for f in ([old / n] if (old / n).is_file() else sorted((old / n).rglob("*")))
             if f.is_file()]
    for i, f in enumerate(files):
        dest = new / f.relative_to(old)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(f, dest)
        if progress:
            progress(i + 1, len(files))
    project.save(new)


def pack(project: Project, directory: str | os.PathLike, out: str | os.PathLike, shards: bool = False,
         progress=None) -> None:
    """A zip of the project and its media (and the shards, if asked), to move or share. Opening it rebuilds the rest."""
    directory = Path(directory)
    save_project(project, directory)
    files = [directory / PROJECT_FILE] + sorted(f for f in (directory / MEDIA).rglob("*") if f.is_file())
    if shards:
        files += [directory / f"{ANALYSIS}.json", directory / f"{ANALYSIS}.npz"]
        files += sorted(f for f in (directory / project.pool).rglob("*") if f.is_file())
    files = [f for f in files if f.exists()]
    tmp = Path(str(out) + ".part")
    with zipfile.ZipFile(tmp, "w") as z:
        for i, f in enumerate(files):
            # Media is compressed already; only the project file is worth squeezing.
            method = zipfile.ZIP_DEFLATED if f.suffix in (".json",) else zipfile.ZIP_STORED
            z.write(f, f.relative_to(directory).as_posix(), compress_type=method)
            if progress:
                progress(i + 1, len(files))
    tmp.replace(out)


def unpack(archive: str | os.PathLike, directory: str | os.PathLike | None = None) -> Path:
    """Unzips a packed project into directory (by default a new folder beside the zip); returns the folder."""
    archive = Path(archive)
    if directory is None:
        directory = archive.with_suffix("")
        n = 2
        while directory.exists():
            directory = archive.with_name(f"{archive.stem}-{n}")
            n += 1
    directory = Path(directory)
    with zipfile.ZipFile(archive) as z:
        if PROJECT_FILE not in z.namelist():
            raise ValueError(f"{archive.name} is not a packed Shardloom project")
        root = directory.resolve()
        for name in z.namelist():
            # Nothing may land outside the project folder.
            if not (root / name).resolve().is_relative_to(root):
                raise ValueError(f"{archive.name}: unsafe path {name}")
        z.extractall(directory)
    return directory


def needs_build(project: Project, directory: str | os.PathLike, width: int, height: int) -> dict:
    """What is out of date: {"pool": bool, "analysis": bool}."""
    return {"pool": pool_recipe(directory, project.pool) != recipe(project, width, height)
            and bool(project.sources),
            "analysis": bool(project.music) and not analysis_current(project, directory)}
