"""The editor's tasks and project opening, without a window."""

import threading

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
