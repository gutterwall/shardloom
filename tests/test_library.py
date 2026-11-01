import json
import zipfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from shardloom import formats, library
from shardloom.analysis import analyse_samples
from shardloom.sequencer import Engine, Lane, Pattern, Project, Source, Step, Title, starter_project
from shardloom.slicer import PoolFrames

from conftest import click_track


@pytest.fixture(scope="module")
def photo(tmp_path_factory):
    d = tmp_path_factory.mktemp("photo")
    img = np.zeros((300, 400, 3), np.uint8)
    img[:, :200] = (220, 30, 30)
    img[:, 200:] = (30, 30, 220)
    Image.fromarray(img).save(d / "split.png")
    return str(d / "split.png")


def project_with(tmp_path, videos, photo, song):
    d = tmp_path / "proj"
    p = starter_project("", "pool", 2, 4)
    p.cut.update(count=6, min=0.3, max=0.6, seed=4)
    p.music = library.import_file(d, song)
    library.add_source(p, d, videos["ball"])
    library.add_source(p, d, photo)
    p.sources[1].motion = "zoom_in"
    p.sources[1].shards = 2
    return d, p


def test_import_copies_once_and_renames_clashes(tmp_path, videos, photo):
    d = tmp_path / "proj"
    a = library.import_file(d, videos["ball"])
    assert a == "media/ball.mp4" and (d / a).exists()
    assert library.import_file(d, videos["ball"]) == a           # the same file is not copied twice
    other = tmp_path / "x" / "ball.mp4"
    other.parent.mkdir()
    other.write_bytes(b"not the same")
    assert library.import_file(d, other) == "media/ball-2.mp4"
    assert library.import_file(d, d / a) == a                      # already inside the project


def test_build_pool_from_videos_and_photos(tmp_path, videos, photo, song):
    d, p = project_with(tmp_path, videos, photo, song)
    shards = library.build_pool(p, d, 64, 36)
    kinds = {s.source_id for s in shards}
    assert kinds == {"s1", "s2"}
    photo_shards = [s for s in shards if s.source_id == "s2"]
    assert len(photo_shards) == 2
    pool = PoolFrames(d / "pool")
    assert pool.width == 64 and pool.height == 36
    frames = np.load(d / "pool" / photo_shards[0].file)
    assert frames.shape[1:] == (36, 64, 3) and len(frames) > 1     # zooming: more than one frame
    # Red on the left, blue on the right survives the cover crop.
    assert frames[0, 18, 4, 0] > 150 and frames[0, 18, 60, 2] > 150
    assert not library.needs_build(p, d, 64, 36)["pool"]
    p.cut["seed"] = 5
    assert library.needs_build(p, d, 64, 36)["pool"]


def test_the_same_recipe_cuts_the_same_pool(tmp_path, videos, photo, song):
    d, p = project_with(tmp_path, videos, photo, song)
    first = [(s.source_id, s.start, s.duration, s.frames) for s in library.build_pool(p, d, 64, 36)]
    a = np.load(d / "pool" / "0000.npy")
    second = [(s.source_id, s.start, s.duration, s.frames) for s in library.build_pool(p, d, 64, 36)]
    assert first == second
    assert np.array_equal(a, np.load(d / "pool" / "0000.npy"))


def test_trim_and_weight(tmp_path, videos, photo, song):
    d, p = project_with(tmp_path, videos, photo, song)
    library.add_source(p, d, videos["still"])
    p.sources[0].start, p.sources[0].end = 1.0, 2.0
    p.sources[2].weight = 0.0
    p.cut["count"] = 8
    shards = library.build_pool(p, d, 64, 36)
    ball = [s for s in shards if s.source_id == "s1"]
    assert ball and all(1.0 <= s.start and s.start + s.duration <= 2.0 + 1e-6 for s in ball)
    assert not [s for s in shards if s.source_id == "s3"]


def test_lane_draws_only_from_its_sources():
    from shardloom.slicer import Shard

    shards = [Shard(i, "", "", 0, 1, 10, source_id="a" if i < 4 else "b") for i in range(8)]
    p = Project(seed=2)
    p.lanes = [Lane("L", choice="random", sources=["b"])]
    p.patterns = [Pattern("A", 16, [[Step(on=True) for _ in range(16)]])]
    e = Engine(p, shards, analyse_samples(click_track(120, 10.0)).with_grid(120.0))
    e.advance(8.0)
    assert {v.shard for v in e._voices[0]} <= {4, 5, 6, 7}


def test_save_as_and_pack_and_unpack_rebuild(tmp_path, videos, photo, song):
    d, p = project_with(tmp_path, videos, photo, song)
    p.titles = [Title("Hello", background="image", image=photo)]
    library.build_pool(p, d, 64, 36)
    library.analyse_music(p, d)
    library.save_project(p, d)
    saved = json.loads((d / "project.json").read_text())
    # Everything it uses is inside the folder, by relative path.
    assert saved["music"].startswith("media/") and saved["titles"][0]["image"].startswith("media/")
    assert all(s["path"].startswith("media/") for s in saved["sources"])

    copy = tmp_path / "copy"
    library.save_as(p, d, copy)
    assert (copy / "project.json").exists() and (copy / "pool" / "pool.json").exists()
    assert (copy / p.music).exists()
    with pytest.raises(FileExistsError):
        library.save_as(p, d, copy)

    z = tmp_path / "packed.zip"
    library.pack(p, d, z)
    names = zipfile.ZipFile(z).namelist()
    assert "project.json" in names and not any(n.startswith("pool/") for n in names)
    out = library.unpack(z)
    assert out == tmp_path / "packed"
    q = Project.load(out)
    todo = library.needs_build(q, out, 64, 36)
    assert todo == {"pool": True, "analysis": True}
    library.analyse_music(q, out)
    rebuilt = library.build_pool(q, out, 64, 36)
    original = json.loads((d / "pool" / "pool.json").read_text())["shards"]
    assert [(s.start, s.duration, s.frames) for s in rebuilt] == [(s["start"], s["duration"], s["frames"]) for s in original]
    assert np.array_equal(np.load(out / "pool" / "0001.npy"), np.load(d / "pool" / "0001.npy"))


def test_unpack_refuses_paths_outside(tmp_path):
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("project.json", "{}")
        f.writestr("../escape.txt", "x")
    with pytest.raises(ValueError):
        library.unpack(z)
    assert not (tmp_path / "escape.txt").exists()


def test_old_projects_adopt_their_pool_sources(tmp_path, videos):
    from shardloom.slicer import slice_videos

    d = tmp_path / "old"
    slice_videos([videos["ball"], videos["still"]], d / "pool", count=4, width=32, height=18, seed=1)
    p = Project()
    library.adopt_pool_sources(p, d)
    assert sorted(Path(s.path).name for s in p.sources) == ["ball.mp4", "still.mp4"]
    assert [s.id for s in p.sources] == ["s1", "s2"]


def test_project_file_round_trip_keeps_editor_fields(tmp_path):
    p = starter_project("media/song.wav", "pool")
    p.sources = [Source("media/a.mp4", id="s1", start=1.5, end=4.0), Source("media/p.jpg", "photo", "s2", motion="pan")]
    p.titles = [Title("Hi", "there", at="end", extra=True, colour="#112233")]
    p.output["format"] = "instagram-reel"
    p.lanes[1].sources = ["s2"]
    p.save(tmp_path)
    q = Project.load(tmp_path)
    assert q.sources == p.sources and q.titles == p.titles and q.lanes[1].sources == ["s2"]
    assert q.output["format"] == "instagram-reel" and q.cut == p.cut
    # Files from older versions load with the defaults.
    data = json.loads((tmp_path / "project.json").read_text())
    for k in ("sources", "titles", "cut", "output"):
        del data[k]
    data["unknown_future_field"] = 1
    (tmp_path / "project.json").write_text(json.dumps(data))
    old = Project.load(tmp_path)
    assert old.sources == [] and old.output["format"] == "youtube-1080"


def test_formats():
    assert formats.BY_KEY["youtube-1080"].width == 1920
    assert (formats.BY_KEY["instagram-reel"].width, formats.BY_KEY["instagram-reel"].height) == (1080, 1920)
    for f in formats.FORMATS:
        assert f.width % 2 == 0 and f.height % 2 == 0
    assert formats.pool_size(1920, 1080, 540) == (960, 540)
    assert formats.pool_size(1080, 1920, 540) == (540, 960)
    assert formats.pool_size(640, 360, 1080) == (640, 360)        # never above the output
    assert formats.preview_size(3840, 2160) == (960, 540)
    assert 7e6 < formats.bitrate(1920, 1080, 30, "good") < 9e6
