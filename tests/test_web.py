"""The phone app's JavaScript against the desktop's Python: the same random numbers, cuts, analysis, sequencer
and timeline, so a project plays the same on both. Runs in Chrome through Playwright; skipped without them.
"""

from __future__ import annotations

import base64
import functools
import http.server
import json
import random
import shutil
import threading
from pathlib import Path

import numpy as np
import pytest

playwright = pytest.importorskip("playwright.sync_api")

from shardloom import pace as pacelib  # noqa: E402
from shardloom.analysis import RATE, Analysis, analyse_samples  # noqa: E402
from shardloom.compose import Program  # noqa: E402
from shardloom.sequencer import Engine, Project, Title, rand, starter_project  # noqa: E402
from shardloom.slicer import Shard, plan_cuts  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def page():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
    handler.log_message = lambda *a: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with playwright.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel="chrome", args=["--enable-unsafe-swiftshader"])
        except Exception:
            if not shutil.which("chromium"):
                pytest.skip("no Chrome for the web tests")
            browser = pw.chromium.launch(executable_path=shutil.which("chromium"))
        pg = browser.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(f"http://127.0.0.1:{server.server_address[1]}/tests/web_harness.html")
        pg.wait_for_function("window.ready === true")
        yield pg
        browser.close()
    server.shutdown()
    assert not errors, errors


def js(page, body: str, arg=None):
    """Runs body (an async function body with `m` holding the modules and `arg`) in the page."""
    return page.evaluate(f"async (arg) => {{ const m = window.mods; {body} }}", arg)


def test_rand_matches(page):
    keys = [[1, 2, 3], [99, -4, 0, 7], [123456, 7919, 3, 88], [5], [2**40, 3, -1]]
    got = js(page, "return arg.map(k => m.rand.rand(...k));", keys)
    assert got == [rand(*k) for k in keys]


def test_python_random_matches(page):
    for seed in (1, 42, 99999, 2**40 + 5):
        r = random.Random(seed)
        want = [r.random(), r.uniform(2, 5), r.choices([0, 1, 2], weights=[3.0, 1.0, 0.5])[0],
                r.choice(["a", "b", "c", "d"]), r.choice([-1, 1]), r.random()]
        got = js(page, """const r = new m.rand.PyRandom(arg);
            return [r.random(), r.uniform(2, 5), r.choices([0, 1, 2], [3, 1, 0.5]), r.choice(["a", "b", "c", "d"]),
                    r.choice([-1, 1]), r.random()];""", seed)
        assert got == want


def test_cuts_match(page):
    want = plan_cuts([30.0, 12.5, 0.1, 60.0], 40, 0.25, 1.5, random.Random(7), weights=[1.0, 2.0, 1.0, 0.5])
    got = js(page, "return m.pool.planCuts([30, 12.5, 0.1, 60], 40, 0.25, 1.5, new m.rand.PyRandom(7), [1, 2, 1, 0.5]);")
    assert [tuple(c) for c in got] == want


def song(seconds=12.0, bpm=124.0):
    """Kicks on the beat, hats between, a pad that gets louder: something with beats and sections."""
    t = np.arange(int(seconds * RATE)) / RATE
    rng = np.random.default_rng(3)
    x = 0.05 * np.sin(2 * np.pi * 220 * t) * (0.3 + 0.7 * t / seconds)
    period = 60.0 / bpm
    for b in np.arange(0.4, seconds, period):
        i = int(b * RATE)
        n = min(len(t) - i, int(0.12 * RATE))
        x[i:i + n] += np.sin(2 * np.pi * 60 * t[:n]) * np.exp(-t[:n] * 30)
        j = int((b + period / 2) * RATE)
        n = min(len(t) - j, int(0.03 * RATE))
        if n > 0:
            x[j:j + n] += 0.3 * rng.standard_normal(n) * np.exp(-t[:n] * 120)
    return x.astype(np.float32)


@pytest.fixture(scope="module")
def analysed(page):
    samples = song()
    want = analyse_samples(samples)
    data = base64.b64encode(samples.tobytes()).decode()
    got = js(page, """const bytes = Uint8Array.from(atob(arg), c => c.charCodeAt(0));
        const a = m.analysis.analyseSamples(new Float32Array(bytes.buffer));
        return {tempo: a.tempo, beats: Array.from(a.beats), duration: a.duration,
                curves: Object.fromEntries(Object.entries(a.curves).map(([k, v]) => [k, Array.from(v)]))};""", data)
    return want, got


def test_analysis_matches(analysed):
    want, got = analysed
    assert got["tempo"] == pytest.approx(want.tempo, rel=1e-6)
    assert got["beats"] == pytest.approx(list(want.beats), abs=1e-6)
    for name, curve in want.curves.items():
        assert np.max(np.abs(np.array(got["curves"][name]) - curve)) < 1e-4, name


def to_js_analysis(a: Analysis) -> dict:
    return {"duration": a.duration, "fps": a.fps, "tempo": a.tempo, "beats": [float(b) for b in a.beats],
            "curves": {k: [float(x) for x in v] for k, v in a.curves.items()}}


def shards(n=9) -> list[Shard]:
    return [Shard(i, f"{i:04d}.npy", "src", 0.0, 1.0, 10 + 3 * i, 0.0, 0.0, i / (n - 1), f"s{1 + i % 2}")
            for i in range(n)]


def states(engine, times):
    out = []
    for t in times:
        st = engine.state(t)
        out.append({"step": st.step, "pattern": st.pattern,
                    "lanes": [[l.voice.shard if l.voice else None, l.frame, sorted(l.fx.items())] for l in st.lanes],
                    "master": sorted(st.master.items()), "palette": st.palette,
                    "events": sorted((k, [round(x, 9) for x in v]) for k, v in st.events.items())})
    return out


JS_STATES = """
const a = m.analysis.Analysis.from({...arg.analysis, beats: arg.analysis.beats,
    curves: Object.fromEntries(Object.entries(arg.analysis.curves).map(([k, v]) => [k, Float32Array.from(v)]))});
const e = new m.engine.Engine(m.project.fromDict(arg.project), arg.shards, a, 30);
const sorted = o => Object.entries(o).sort(([x], [y]) => (x < y ? -1 : 1));
return arg.times.map(t => {
  const st = e.state(t);
  return {step: st.step, pattern: st.pattern,
          lanes: st.lanes.map(l => [l.voice ? l.voice.shard : null, l.frame, sorted(l.fx)]),
          master: sorted(st.master), palette: st.palette,
          events: sorted(st.events).map(([k, v]) => [k, v.map(x => Math.round(x * 1e9) / 1e9)])};
});
"""


def compare_engine(page, analysed, project: Project, times):
    want_a, _ = analysed
    sh = shards()
    want = states(Engine(project, sh, want_a), times)
    arg = {"analysis": to_js_analysis(want_a), "project": project.to_dict(), "times": times,
           "shards": [{"id": s.id, "frames": s.frames, "energy": s.energy, "source_id": s.source_id} for s in sh]}
    got = js(page, JS_STATES, arg)
    for w, g, t in zip(want, got, times):
        g["lanes"] = [[a, b, [tuple(x) for x in c]] for a, b, c in g["lanes"]]
        g["master"] = [tuple(x) for x in g["master"]]
        g["events"] = [tuple(x) for x in g["events"]]
        w["lanes"] = [[a, b, [(k, pytest.approx(v)) for k, v in c]] for a, b, c in w["lanes"]]
        w["master"] = [(k, pytest.approx(v)) for k, v in w["master"]]
        w["events"] = [(k, pytest.approx(v, abs=1e-6)) for k, v in w["events"]]
        assert g == w, f"at {t}"


def test_sequencer_matches(page, analysed):
    p = starter_project("", "pool", 3, seed=11)
    p.lanes[1].sources = ["s2"]
    p.lanes[2].choice = "no_repeat"
    p.lanes[0].fx = {"zoom": 0.2}
    compare_engine(page, analysed, p, [i * 0.05 for i in range(220)])


def test_shard_effects_and_timing_match(page, analysed):
    p = starter_project("", "pool", 3, seed=5)
    p.shard_fx = {"fireworks": {"rate": 0.8, "sync": 1.0, "strength": 0.9}, "tear": {"rate": 0.6, "sync": -1.0},
                  "hue_shift": {"rate": 0.7}, "brightness": {"rate": 0.9, "strength": 0.5}}
    p.pace = {**pacelib.default_pace(), "on": True, "weights": [0, 1, 1, 1, 1, 1, 1], "tilt": 0.3, "repeats": 3}
    p.palette_mode = "bars"
    p.palette_bars = 1
    compare_engine(page, analysed, p, [i * 0.04 for i in range(280)])


def test_program_matches(page):
    p = Project()
    p.output.update({"start": 3.0, "end": 40.0, "fade_in": 1.0, "fade_out": 2.0})
    p.titles = [Title("Hello", at="start", extra=True, duration=2.0), Title("Over", at="start", duration=3.0),
                Title("Bye", at="end", duration=5.0), Title("Card", at="end", extra=True, duration=1.5)]
    prog = Program(p, 60.0)
    got = js(page, """const pr = new m.program.Program(m.project.fromDict(arg), 60);
        return {duration: pr.duration, pre: pr.pre, placed: pr.placed.map(x => [x.index, x.t0, x.t1, x.card]),
                fades: [0.5, 10, 41, 43.4].map(T => pr.fade(T)), song: [1, 2.5, 20].map(T => pr.songTime(T))};""",
             p.to_dict())
    assert got["duration"] == pytest.approx(prog.duration)
    assert got["pre"] == pytest.approx(prog.pre)
    assert [tuple(x) for x in got["placed"]] == [pytest.approx((x.index, x.t0, x.t1, x.card)) for x in prog.placed]
    assert got["fades"] == pytest.approx([prog.fade(T) for T in (0.5, 10, 41, 43.4)])
    assert got["song"] == [prog.song_time(T) for T in (1, 2.5, 20)]


def test_shaders_compile_and_draw(page):
    """Every shader compiles in WebGL2 and a frame with all the effects draws."""
    got = js(page, """
        const c = document.createElement("canvas");
        const frames = {get: () => { const k = document.createElement("canvas"); k.width = 64; k.height = 36;
            const g = k.getContext("2d"); g.fillStyle = "#c33"; g.fillRect(0, 0, 64, 36); g.fillStyle = "#3c3";
            g.fillRect(0, 0, 32, 18); return k; }};
        const r = new m.renderer.Renderer(c, 160, 90, frames);
        const all = Object.fromEntries(m.events.NAMES.map(n => [n, [0.8, 0.4, 0.3]]));
        const fx = Object.fromEntries(Object.keys(m.fx.FX).map(n => [n, 0.3]));
        const state = {t: 1, step: 4, pattern: 0, palette: m.fx.PALETTES[0], music: {beat_phase: 0.2},
            lanes: [{voice: {shard: 0, step: 4}, frame: 0, fx, opacity: 1, blend: "screen"}],
            master: {...fx, trails: 0.4, flash: 0.1}, events: all};
        r.render(state);
        const gl = r.gl, px = new Uint8Array(4);
        gl.readPixels(80, 45, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
        return {error: gl.getError(), px: Array.from(px)};""")
    assert got["error"] == 0
    assert got["px"][3] == 255


def test_packed_projects_move_both_ways(page, tmp_path):
    """A project packed on the desktop opens in the web app, and one packed there opens on the desktop."""
    from shardloom import library
    from shardloom.sequencer import Source

    folder = tmp_path / "desk"
    (folder / "media").mkdir(parents=True)
    (folder / "media" / "song.wav").write_bytes(b"RIFF fake song")
    (folder / "media" / "clip.mp4").write_bytes(bytes(range(256)) * 40)
    p = starter_project("media/song.wav", "pool", 3, seed=9)
    p.sources = [Source(path="media/clip.mp4", kind="video", id="s1")]
    p.shard_fx = {"burn": {"rate": 0.5, "sync": 1.0, "strength": 0.6}}
    library.pack(p, folder, tmp_path / "desk.zip")
    data = base64.b64encode((tmp_path / "desk.zip").read_bytes()).decode()
    got = js(page, """const bytes = Uint8Array.from(atob(arg), c => c.charCodeAt(0));
        const {project, files} = await m.pack.unpack(new Blob([bytes]));
        const sizes = {};
        for (const [k, v] of files) sizes[k] = v.size;
        const again = await m.pack.pack(m.project.fromDict(project), [...files]);
        const out = new Uint8Array(await again.arrayBuffer());
        let s = ""; for (let i = 0; i < out.length; i += 0x8000) s += String.fromCharCode(...out.subarray(i, i + 0x8000));
        return {project, sizes, zip: btoa(s)};""", data)
    assert got["sizes"] == {"media/song.wav": 14, "media/clip.mp4": 256 * 40}
    assert got["project"]["shard_fx"] == p.shard_fx
    (tmp_path / "web.zip").write_bytes(base64.b64decode(got["zip"]))
    back = library.unpack(tmp_path / "web.zip", tmp_path / "back")
    q = Project.load(back)
    assert q.to_dict() == p.to_dict()
    assert (back / "media" / "clip.mp4").read_bytes() == (folder / "media" / "clip.mp4").read_bytes()
