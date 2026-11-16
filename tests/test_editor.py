"""The editor's tasks and project opening, without a window."""

import threading

import numpy as np
import pytest

from shardloom import library
from shardloom.editor import Editor, Task
from shardloom.sequencer import starter_project


@pytest.fixture
def editor(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    # hello_imgui's saved preferences need a running window.
    monkeypatch.setattr(Editor, "remember", lambda self, folder: None)
    monkeypatch.setattr(Editor, "recent", lambda self: [])
    return Editor()


def wait(editor):
    for task in list(editor.tasks):
        while task.thread is None or task.thread.is_alive():
            if task.thread is not None:
                task.thread.join(5)
            editor.poll_tasks()
    editor.poll_tasks()


def test_opening_a_project_without_shards_does_not_cut_them(editor, tmp_path, videos):
    d = tmp_path / "proj"
    p = starter_project("", library.POOL, 2, 1)
    library.add_source(p, d, videos["ball"])
    library.save_project(p, d)
    editor.open_project(d)
    assert not editor.busy_with("Cutting shards")
    assert not (d / "pool").exists() and not (d / "pool.new").exists()


def test_a_running_task_is_cancelled(editor):
    started = threading.Event()

    def work(task):
        started.set()
        while not task.cancelled.wait(0.01):
            pass
        raise library.Cancelled()

    done = []
    editor.run("Cutting shards", work, done.append)
    assert started.wait(5)
    editor.cancel_task("Cutting shards")
    wait(editor)
    assert not editor.tasks and not done                    # done() does not run for a cancelled task
    assert "cancelled" in editor.message


def test_a_queued_task_is_dropped(editor):
    gate = threading.Event()
    editor.run("Loading the song", lambda task: gate.wait(5))
    editor.run("Cutting shards", lambda task: pytest.fail("a cancelled cut ran"))
    editor.cancel_task("Cutting shards")
    assert not editor.busy_with("Cutting shards")
    gate.set()
    wait(editor)
    assert not editor.tasks


def test_a_task_error_is_still_reported(editor):
    def work(task):
        raise ValueError("bad media")

    editor.run("Cutting shards", work)
    wait(editor)
    assert "failed: bad media" in editor.message


def test_task_cancel_before_start_is_seen():
    t = Task("x", lambda task: task.cancelled.is_set())
    t.cancel()
    t.start()
    t.thread.join(5)
    assert t.result is True


def photos(tmp_path, n):
    from PIL import Image

    from shardloom.sequencer import Source

    folder = tmp_path / "photos"
    folder.mkdir()
    out = []
    for i in range(n):
        path = folder / f"p{i}.jpg"
        Image.new("RGB", (64, 48), (i * 10, 80, 160)).save(path)
        out.append(Source(str(path), "photo", f"s{i}"))
    return out


def test_thumbnails_are_made_a_few_at_a_time(editor, tmp_path, monkeypatch):
    import time

    lock = threading.Lock()
    running = [0, 0]                                         # now, most at once

    def thumbnail(directory, src, width=160):
        with lock:
            running[0] += 1
            running[1] = max(running[1], running[0])
        time.sleep(0.02)
        with lock:
            running[0] -= 1
        return np.zeros((12, 16, 3), np.uint8)

    monkeypatch.setattr(library, "thumbnail", thumbnail)
    editor.dir = tmp_path
    sources = photos(tmp_path, 30)
    for s in sources:
        editor.queue_thumbnail(s)
    deadline = time.monotonic() + 10
    while len(editor._thumb_data) < 30 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(editor._thumb_data) == 30
    assert {s.path for s in sources} == set(editor.media_info)
    assert running[1] <= Editor.THUMB_THREADS and len(editor._thumb_threads) <= Editor.THUMB_THREADS


def test_thumbnails_of_a_closed_project_are_skipped(editor, tmp_path, monkeypatch):
    import time

    gate = threading.Event()
    made = []

    def thumbnail(directory, src, width=160):
        gate.wait(5)
        made.append(src.path)
        return np.zeros((12, 16, 3), np.uint8)

    monkeypatch.setattr(library, "thumbnail", thumbnail)
    editor.dir = tmp_path
    for s in photos(tmp_path, 20):
        editor.queue_thumbnail(s)
    editor.close_project()                                   # while the first few are still being made
    gate.set()
    time.sleep(0.3)
    assert len(made) <= Editor.THUMB_THREADS                 # the rest are never made
    assert not editor._thumb_data and not editor.media_info
