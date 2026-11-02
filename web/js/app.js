// Shardloom for phones and the web: the projects list and the editor.

import { html, render, useEffect, useRef, useState } from "../vendor/standalone.module.js";
import * as store from "./store.js";
import * as audio from "./audio.js";
import * as formats from "./formats.js";
import * as fxlib from "./fx.js";
import * as eventlib from "./events.js";
import * as pacelib from "./pace.js";
import * as poollib from "./pool.js";
import * as packlib from "./pack.js";
import * as exporter from "./exporter.js";
import { Analysis } from "./analysis.js";
import { Engine } from "./engine.js";
import { Program } from "./program.js";
import { Renderer } from "./renderer.js";
import { POSITIONS, FONTS } from "./titles.js";
import { Lane, Title, Modulation, Source, CHOICES, fromDict, normalise, starterProject, newSourceId, kindOf } from "./project.js";

export const VERSION = "0.6.1";

// ---- app state -------------------------------------------------------------------

const S = {
  view: "projects",
  projects: [],
  id: null, name: "", project: null,
  files: new Map(),               // path -> Blob
  info: {},                       // path -> {duration, width, height}
  thumbs: {},                     // path -> object URL
  song: null, analysis: null,
  pool: null, frames: null,
  engine: null, program: null, renderer: null, canvas: null, player: null,
  T: 0, tab: "media", selTitle: 0, pattern: 0,
  busy: null, toast: null, dialog: null, renders: [],
  needDraw: true, usage: null,
};
let update = () => {};
const say = (text, seconds = 4) => {
  S.toast = text;
  update();
  clearTimeout(say.timer);
  say.timer = setTimeout(() => { S.toast = null; update(); }, seconds * 1000);
};

const clock = s => {
  s = Math.max(0, s || 0);
  const m = Math.floor(s / 60);
  return `${m}:${(s - m * 60).toFixed(1).padStart(4, "0")}`;
};
const mb = n => (n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${Math.max(0.1, n / 1e6).toFixed(n < 1e7 ? 1 : 0)} MB`);
const uid = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`);
const safeName = name => name.replace(/[\\/:*?"<>|\u0000-\u001f]+/g, "_").trim() || "file";

// Asks for files; resolves with the chosen ones (never, if the picker is cancelled on some phones).
function pick(accept, multiple = false) {
  return new Promise(resolve => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = accept;
    input.multiple = multiple;
    input.style.display = "none";
    input.onchange = () => { resolve([...input.files]); input.remove(); };
    input.oncancel = () => { resolve([]); input.remove(); };
    document.body.appendChild(input);
    input.click();
  });
}

// Shares a file (on phones: to Photos, Files, apps) or downloads it.
async function saveFile(blob, name) {
  const file = new File([blob], name, { type: blob.type });
  if (navigator.canShare && navigator.canShare({ files: [file] })) {
    try {
      await navigator.share({ files: [file], title: name });
      return;
    } catch (e) {
      if (e.name === "AbortError") return;
    }
  }
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 60000);
}

// Runs work(task) as the one busy job, with progress shown; task.report(done, total), task.cancelled.
async function run(label, work) {
  if (S.busy) {
    say(`Wait for ${S.busy.label.toLowerCase()} to finish.`);
    return null;
  }
  const task = { label, done: 0, total: 0, cancelled: false,
    report(done, total) { task.done = done; task.total = total; update(); } };
  S.busy = task;
  update();
  try {
    return await work(task);
  } catch (e) {
    if (!task.cancelled) {
      console.error(e);
      say(`${label} failed: ${e.message || e}`, 10);
    }
    return null;
  } finally {
    S.busy = null;
    update();
  }
}

// ---- projects ----------------------------------------------------------------------

async function refreshProjects() {
  const rows = await store.all("projects");
  S.projects = rows.map(([, r]) => r).sort((a, b) => b.updated - a.updated);
  S.usage = await store.usage();
  update();
}

const SHAPES = [["youtube-1080", "Wide", 16, 9], ["instagram-reel", "Tall", 9, 16], ["instagram-square", "Square", 1, 1]];

async function createProject(name, format) {
  const seed = 1 + Math.floor(Math.random() * 99999);
  const p = starterProject("", "pool", 3, seed);
  p.cut.seed = seed;
  p.output.format = format;
  const f = formats.BY_KEY[format];
  p.width = f.width;
  p.height = f.height;
  p.fps = 30;
  const id = uid();
  await store.put("projects", id, { id, name, updated: Date.now(), project: p });
  store.persist();
  await openProject(id);
}

async function openProject(id) {
  closeProject();
  const rec = await store.get("projects", id);
  if (!rec) return say("That project is gone.");
  S.id = id;
  S.name = rec.name;
  S.project = fromDict(rec.project);
  if (!formats.BY_KEY[S.project.output.format]) S.project.output.format = "custom";
  S.T = 0;
  S.tab = "media";
  S.view = "edit";
  S.renders = (await store.all("renders")).filter(([, r]) => r.projectId === id).map(([k, r]) => ({ key: k, ...r }))
    .sort((a, b) => b.created - a.created);
  update();
  for (const s of S.project.sources) thumbnail(s);
  S.pool = await store.get("pools", id);
  if (S.pool) S.frames = await new poollib.PoolFrames(id, S.pool).load();
  if (S.frames) S.frames.onReady = () => { S.needDraw = true; };
  if (S.project.music) await loadSong();
  if (S.project.sources.length && !S.pool) await cut();
  session();
}

function closeProject() {
  if (S.project) saveNow();
  if (S.player) S.player.stop();
  if (S.frames) S.frames.close();
  if (S.renderer) S.renderer.dispose();
  for (const url of Object.values(S.thumbs)) URL.revokeObjectURL(url);
  Object.assign(S, { id: null, project: null, files: new Map(), info: {}, thumbs: {}, song: null, analysis: null,
    pool: null, frames: null, engine: null, program: null, renderer: null, canvas: null, player: null, renders: [] });
}

let saveTimer = null;
function saveNow() {
  clearTimeout(saveTimer);
  saveTimer = null;
  if (!S.id || !S.project) return Promise.resolve();
  return store.put("projects", S.id, { id: S.id, name: S.name, updated: Date.now(), project: S.project })
    .catch(e => say(`Could not save: ${e.message}`, 8));
}

// After any change to the project: save soon, replay the sequencer with it, redraw.
function changed(what = "") {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveNow, 700);
  const e = S.engine;
  if (e) {
    e.songMode = S.project.song.length > 0;
    if (what === "tempo") e.retime();
    else e.reset();
    if (what === "lanes") session();
  }
  rebuildProgram();
  S.needDraw = true;
  update();
}

function rebuildProgram() {
  if (!S.analysis) { S.program = null; return; }
  try {
    S.program = new Program(S.project, S.analysis.duration);
  } catch (e) {
    S.program = null;
  }
  if (S.program) S.T = Math.min(S.T, Math.max(0, S.program.duration - 0.001));
}

// ---- media ----------------------------------------------------------------------------

async function fileBlob(path) {
  let b = S.files.get(path);
  if (!b) {
    b = await store.get("files", `${S.id}/${path}`);
    if (!b) throw new Error(`${path} is missing from the project`);
    S.files.set(path, b);
  }
  return b;
}
const files = { blob: fileBlob };

// Copies a chosen file into the project (once) and returns its path, media/<name>.
async function importFile(file) {
  const name = safeName(file.name);
  const dot = name.lastIndexOf(".");
  const stem = dot > 0 ? name.slice(0, dot) : name, ext = dot > 0 ? name.slice(dot) : "";
  const existing = new Set((await store.keysWithPrefix("files", `${S.id}/media/`)).map(k => k.slice(S.id.length + 1)));
  let path = `media/${name}`, n = 2;
  while (existing.has(path)) {
    const old = await fileBlob(path);
    if (old.size === file.size) return path;
    path = `media/${stem}-${n++}${ext}`;
  }
  await store.put("files", `${S.id}/${path}`, file);
  S.files.set(path, file);
  return path;
}

async function chooseSong() {
  const [file] = await pick("audio/*,.mp3,.wav,.m4a,.aac,.flac,.ogg,.opus");
  if (!file) return;
  audio.context();
  await run("Adding the song", async () => {
    S.project.music = await importFile(file);
    changed();
  });
  await loadSong();
  session();
}

async function loadSong() {
  const p = S.project;
  await run("Listening to the song", async task => {
    const blob = await fileBlob(p.music);
    S.song = await audio.decode(blob);
    const key = `${p.music.split("/").pop()}:${blob.size}`;
    const stored = await store.get("analyses", S.id);
    if (stored && stored.key === key) {
      S.analysis = Analysis.from(stored);
    } else {
      const a = await audio.analyse(S.song, f => task.report(Math.round(f * 100), 100));
      a.key = key;
      await store.put("analyses", S.id, a.toData());
      S.analysis = a;
    }
  });
}

async function addMedia() {
  const chosen = await pick("video/*,image/*", true);
  if (!chosen.length) return;
  await run("Adding media", async task => {
    let n = 0;
    for (const file of chosen) {
      const kind = kindOf(file.name, file.type);
      task.report(n++, chosen.length);
      if (kind !== "video" && kind !== "photo") {
        say(`${file.name}: not a video or a photo`);
        continue;
      }
      try {
        await probe(file, kind);
      } catch (e) {
        say(`${file.name}: ${e.message}`, 8);
        continue;
      }
      const path = await importFile(file);
      const p = S.project;
      const src = Source({ path, kind, id: newSourceId(p) });
      p.sources.push(src);
      S.info[path] = S.info[file.name];
      thumbnail(src);
    }
    changed();
  });
}

async function probe(blob, kind) {
  if (kind === "photo") {
    const img = await createImageBitmap(blob);
    const info = { duration: 0, width: img.width, height: img.height };
    img.close();
    S.info[blob.name] = info;
    return info;
  }
  const g = await new poollib.VideoGrabber(blob).open();
  const info = { duration: g.duration, width: g.width, height: g.height };
  g.close();
  S.info[blob.name] = info;
  return info;
}

async function thumbnail(src) {
  if (S.thumbs[src.path]) return;
  try {
    const blob = await fileBlob(src.path);
    const c = document.createElement("canvas");
    c.width = 128;
    c.height = 80;
    const ctx = c.getContext("2d");
    if (src.kind === "photo") {
      const img = await createImageBitmap(blob, { imageOrientation: "from-image" });
      S.info[src.path] = S.info[src.path] || { duration: 0, width: img.width, height: img.height };
      const s = Math.max(128 / img.width, 80 / img.height);
      ctx.drawImage(img, (128 - img.width * s) / 2, (80 - img.height * s) / 2, img.width * s, img.height * s);
      img.close();
    } else {
      const g = await new poollib.VideoGrabber(blob).open();
      S.info[src.path] = { duration: g.duration, width: g.width, height: g.height };
      await g.seek(g.duration * 0.25);
      const s = Math.max(128 / g.width, 80 / g.height);
      ctx.drawImage(g.video, (128 - g.width * s) / 2, (80 - g.height * s) / 2, g.width * s, g.height * s);
      g.close();
    }
    const thumb = await new Promise(r => c.toBlob(r, "image/jpeg", 0.8));
    S.thumbs[src.path] = URL.createObjectURL(thumb);
    update();
  } catch { /* no picture: the list shows a blank */ }
}

function poolSize() {
  const p = S.project;
  return formats.poolSize(p.width, p.height, Number(p.cut.detail || 360));
}

function poolStale() {
  const p = S.project;
  if (!p.sources.length) return false;
  const [w, h] = poolSize();
  return !S.pool || S.pool.recipe !== poollib.recipe(p, w, h);
}

async function cut() {
  const p = S.project;
  if (!p.sources.some(s => s.enabled)) return say("Tick at least one video or photo to cut from.");
  if (S.player) pause();
  const [w, h] = poolSize();
  const made = await run("Cutting shards", task => poollib.buildPool(p, S.id, files, w, h,
    (d, t) => task.report(d, t), () => task.cancelled));
  if (!made) return;
  if (S.frames) S.frames.close();
  S.pool = made;
  S.frames = await new poollib.PoolFrames(S.id, made).load();
  S.frames.onReady = () => { S.needDraw = true; };
  changed();
  session();
  say(`${made.shards.length} shards cut.`);
}

// The engine and timeline, once the song and the shards are ready.
function session() {
  if (!S.analysis || !S.pool || !S.pool.shards.length) {
    S.engine = null;
    rebuildProgram();
    update();
    return;
  }
  S.engine = new Engine(S.project, S.pool.shards, S.analysis, S.pool.fps);
  if (!S.player || S.player.buffer !== S.song) S.player = new audio.Player(S.song);
  rebuildProgram();
  S.needDraw = true;
  update();
}

// ---- playing ----------------------------------------------------------------------------

function play() {
  if (!S.program || !S.player) return;
  if (S.T >= S.program.duration - 0.05) S.T = 0;
  S.player.start(S.program, S.T);
  update();
}

function pause() {
  if (!S.player) return;
  S.player.stop();
  S.T = Math.min(S.player.now(), S.program ? S.program.duration : 0);
  update();
}

function seek(T) {
  if (!S.program) return;
  S.T = Math.max(0, Math.min(T, S.program.duration - 0.001));
  if (S.player && S.player.playing) S.player.start(S.program, S.T);
  S.needDraw = true;
}

function previewKey() {
  const p = S.project;
  const longest = Math.min(screen.width * devicePixelRatio, window.innerWidth >= 900 ? 960 : 720);
  return formats.previewSize(p.width, p.height, Math.max(320, longest));
}

let lastUi = 0;
function loop(now) {
  requestAnimationFrame(loop);
  if (S.view !== "edit" || !S.engine || !S.program || !S.renderer || S.busy?.label === "Rendering") return;
  const playing = S.player && S.player.playing;
  if (playing) {
    S.T = S.player.now();
    if (S.T >= S.program.duration) {
      S.player.stop();
      S.T = S.program.duration - 0.001;
      update();
    }
    S.needDraw = true;
  }
  if (S.needDraw) {
    S.needDraw = false;
    try {
      const state = S.renderer.frame(S.engine, S.program, S.T);
      if (state) S.frames.prefetch(state.lanes.filter(l => l.voice).map(l => [l.voice.shard, l.frame, l.voice.reverse ? -1 : 1]));
    } catch (e) {
      console.error(e);
    }
    showTime();
  }
  // Panels that show what is playing now catch up a few times a second.
  if (playing && now - lastUi > 250 && (S.tab === "timing")) {
    lastUi = now;
    update();
  }
}
requestAnimationFrame(loop);

const timeEls = { label: null, slider: null, canvas: null };
function showTime() {
  if (!S.program) return;
  if (timeEls.label) {
    const t = S.program.songTime(S.T);
    timeEls.label.textContent = `${clock(S.T)} / ${clock(S.program.duration)}${t === null ? "  title card" : ""}`;
  }
  if (timeEls.canvas) drawTimeline(timeEls.canvas);
}

// The whole video: cards, the song section with its intensity, titles, the playhead.
function drawTimeline(c) {
  const prog = S.program, a = S.analysis;
  if (!prog || !a) return;
  const dpr = devicePixelRatio || 1;
  const w = c.clientWidth, h = c.clientHeight;
  if (c.width !== Math.round(w * dpr)) { c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); }
  const g = c.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  const x = T => (T / prog.duration) * w;
  const x0 = x(prog.pre), x1 = x(prog.pre + prog.songEnd - prog.songStart);
  g.fillStyle = "#1c2b40";
  g.fillRect(x0, 4, x1 - x0, h - 8);
  const curve = a.curves.intensity;
  if (curve) {
    g.fillStyle = "#4a7fc0";
    for (let px = Math.floor(x0); px < x1; px += 2) {
      const t = prog.songStart + ((px - x0) / Math.max(1, x1 - x0)) * (prog.songEnd - prog.songStart);
      const v = curve[Math.min(curve.length - 1, Math.max(0, Math.floor(t * a.fps)))];
      g.fillRect(px, h - 6 - v * (h - 14), 1.5, v * (h - 14));
    }
  }
  for (const pl of prog.placed) {
    g.fillStyle = pl.card ? "#a8652eaa" : "#7a55a5aa";
    g.fillRect(x(pl.t0), pl.card ? 4 : 4, x(pl.t1) - x(pl.t0), pl.card ? h - 8 : 14);
  }
  g.fillStyle = "#fff";
  g.fillRect(x(S.T) - 1, 0, 2, h);
}

// ---- rendering ----------------------------------------------------------------------------

async function renderVideo() {
  const p = S.project;
  if (!S.engine || !S.program) return say("The song and the shards need to be ready first.");
  pause();
  const [width, height] = [p.width, p.height];
  const mode = await exporter.exportMode(width, height, p.fps);
  if (!mode) return say("This browser can't make videos. Try Chrome or Safari, up to date.", 10);
  const titleImages = await loadTitleImages(p);
  const name = `${safeName(S.name)}-${p.output.format === "custom" ? `${width}x${height}` : p.output.format}`;
  const out = await run("Rendering", async task => {
    task.mode = mode;
    const t0 = performance.now();
    const result = await exporter.render({
      project: p, shards: S.pool.shards, analysis: S.analysis, poolFps: S.pool.fps, frames: S.frames,
      buffer: S.song, program: new Program(p, S.analysis.duration), width, height, titleImages,
      progress: (d, t) => task.report(d, t), cancelled: () => task.cancelled,
    });
    const rec = { projectId: S.id, name: `${name}.${result.ext}`, blob: result.blob, created: Date.now(),
      size: result.blob.size, seconds: (performance.now() - t0) / 1000 };
    const key = uid();
    await store.put("renders", key, rec);
    return { key, ...rec };
  });
  if (out) {
    S.renders.unshift(out);
    S.tab = "video";
    say(`Rendered ${out.name} (${mb(out.size)}). Tap Save to keep it.`, 8);
  }
  S.needDraw = true;
}

async function loadTitleImages(p) {
  const out = {};
  for (const t of p.titles) {
    if (t.background === "image" && t.image && !out[t.image]) {
      try { out[t.image] = await createImageBitmap(await fileBlob(t.image), { imageOrientation: "from-image" }); } catch { /* shown as colour */ }
    }
  }
  return out;
}

// ---- packing ----------------------------------------------------------------------------------

async function packProject(id) {
  await run("Packing", async task => {
    const rec = await store.get("projects", id);
    const keys = await store.keysWithPrefix("files", `${id}/media/`);
    const list = [];
    for (const k of keys) list.push([k.slice(id.length + 1), await store.get("files", k)]);
    task.report(1, 2);
    const zip = await packlib.pack(rec.project, list);
    S.dialog = { kind: "packed", blob: zip, name: `${safeName(rec.name)}.zip` };
  });
}

async function importPack() {
  const [file] = await pick(".zip,application/zip");
  if (!file) return;
  await run("Opening the packed project", async task => {
    const { project, files: media } = await packlib.unpack(file);
    const id = uid();
    let n = 0;
    for (const [path, blob] of media) {
      await store.put("files", `${id}/${path}`, blob);
      task.report(++n, media.size);
    }
    const name = file.name.replace(/\.zip$/i, "");
    await store.put("projects", id, { id, name, updated: Date.now(), project: fromDict(project) });
    S.importedId = id;
  });
  if (S.importedId) {
    const id = S.importedId;
    S.importedId = null;
    await openProject(id);
  }
}

// ---- small controls ---------------------------------------------------------------------------

const pct = v => `${Math.round(v * 100)}%`;
const fix = n => v => Number(v).toFixed(n);

function Slider({ label, value, min = 0, max = 1, step = 0.01, fmt = fix(2), on, title = "", disabled = false }) {
  return html`<div class="slider" title=${title}>
    <label>${label}</label>
    <input type="range" min=${min} max=${max} step=${step} value=${value} disabled=${disabled} aria-label=${label}
      onInput=${e => on(e.currentTarget.valueAsNumber)} />
    <output>${fmt(value)}</output>
  </div>`;
}

function Field({ label, children }) {
  return html`<div class="field"><label>${label}</label><div class="row tight">${children}</div></div>`;
}

function Select({ value, options, on, disabled = false }) {
  return html`<select value=${String(value)} disabled=${disabled} onChange=${e => on(e.currentTarget.value)}>
    ${options.map(([v, l]) => html`<option value=${String(v)}>${l}</option>`)}
  </select>`;
}

function Check({ label, value, on, disabled = false }) {
  return html`<label class="check"><input type="checkbox" checked=${!!value} disabled=${disabled}
    onChange=${e => on(e.currentTarget.checked)} /><span>${label}</span></label>`;
}

function FxSliders({ amounts, names, onChange }) {
  return Object.entries(names).map(([name, about]) => html`<${Slider} label=${name.replace("_", " ")} title=${about}
    value=${amounts[name] || 0} fmt=${v => (v > 0 ? v.toFixed(2) : "off")}
    on=${v => { if (v > 0) amounts[name] = Math.round(v * 1000) / 1000; else delete amounts[name]; onChange(); }} />`);
}

// ---- projects screen ------------------------------------------------------------------------------

function Projects() {
  const u = S.usage;
  return html`<div class="projects">
    <div class="brand"><img src="icons/icon.svg" alt="" /><div><h1>Shardloom</h1>
      <div class="faint">Music videos cut from shards of your clips, in time with the song. v${VERSION}</div></div></div>
    <div class="row">
      <button class="primary" onClick=${() => { S.dialog = { kind: "new", name: "", format: SHAPES[0][0] }; update(); }}>New project</button>
      <button onClick=${importPack}>Open a packed project…</button>
    </div>
    <h2>Projects</h2>
    ${!S.projects.length && html`<p class="muted">No projects yet. Start one, then add a song and some videos or photos.</p>`}
    ${S.projects.map(r => html`<div class="card project-item">
      <div class="grow" onClick=${() => openProject(r.id)} style="cursor:pointer">
        <div class="name">${r.name}</div>
        <div class="faint">${(formats.BY_KEY[r.project.output.format] || { label: "Custom size" }).label} ·
          ${new Date(r.updated).toLocaleDateString()}</div>
      </div>
      <button class="small" onClick=${() => openProject(r.id)}>Open</button>
      <button class="small" title="A zip of the project and its media, to keep or open on a computer"
        onClick=${() => packProject(r.id)}>Pack</button>
      <button class="small danger" onClick=${() => { S.dialog = { kind: "delete", id: r.id, name: r.name }; update(); }}>Delete</button>
    </div>`)}
    ${u && u.quota ? html`<p class="faint">Using ${mb(u.usage || 0)} of the ${mb(u.quota)} this browser allows.</p>` : null}
    <h2>About</h2>
    <p class="faint">Everything stays on this device: your media, shards and videos are kept in the browser,
      not uploaded. Add Shardloom to your home screen to use it like an app, offline too. A packed project opens in
      the desktop Shardloom, and a project packed there opens here.</p>
  </div>`;
}

// ---- editor -------------------------------------------------------------------------------------

function Stage() {
  const box = useRef(null);
  const line = useRef(null);
  const label = useRef(null);
  const p = S.project;
  const [pw, ph] = previewKey();
  const ready = S.engine && S.program;
  useEffect(() => {
    timeEls.label = label.current;
    timeEls.canvas = line.current;
    if (!ready || !box.current) return;
    const key = `${pw}x${ph}`;
    if (!S.renderer || S.renderer.key !== key || S.renderer.frames !== S.frames) {
      if (S.renderer) { S.renderer.dispose(); S.canvas?.remove(); }
      S.canvas = document.createElement("canvas");
      try {
        S.renderer = new Renderer(S.canvas, pw, ph, S.frames);
        S.renderer.key = key;
        loadTitleImages(p).then(imgs => { if (S.renderer) { S.renderer.titleImages = imgs; S.renderer.titles.clear(); S.needDraw = true; } });
      } catch (e) {
        say(e.message, 10);
        S.renderer = null;
        return;
      }
    }
    if (S.canvas.parentNode !== box.current) box.current.prepend(S.canvas);
    S.needDraw = true;
  });
  const steps = [];
  if (!p.music) steps.push("Choose a song (Media).");
  if (!p.sources.length) steps.push("Add videos or photos (Media).");
  if (p.sources.length && !S.pool) steps.push("Press Cut shards (Media).");
  if (p.music && S.analysis && !S.program) steps.push("The song section is empty: check From and To (Video).");
  const playing = S.player && S.player.playing;
  const scrub = e => {
    if (!S.program) return;
    const r = e.currentTarget.getBoundingClientRect();
    seek(((e.clientX - r.left) / r.width) * S.program.duration);
  };
  return html`<div class="stage">
    <div class="screen" ref=${box} style=${`--ar:${p.width / p.height}`}>
      ${!ready && html`<div class="hint">${(steps.length ? steps : ["Getting ready…"]).map(s => html`<div>${s}</div>`)}</div>`}
    </div>
    <div class="transport">
      <button class="primary icon" disabled=${!ready} aria-label=${playing ? "Pause" : "Play"}
        onClick=${() => (playing ? pause() : play())}>${playing ? "❚❚" : "▶"}</button>
      <button class="icon" disabled=${!ready} aria-label="Back to the start" onClick=${() => { seek(0); update(); }}>⏮</button>
      <span class="time grow" ref=${label}>${ready ? "" : "—"}</span>
      <span class="faint">${p.width}×${p.height}</span>
    </div>
    <canvas class="timeline" ref=${line} aria-label="Timeline: tap or drag to move"
      onPointerDown=${e => { e.currentTarget.setPointerCapture(e.pointerId); scrub(e); }}
      onPointerMove=${e => { if (e.buttons) scrub(e); }} />
  </div>`;
}

const TABS = [["media", "Media"], ["layers", "Layers"], ["effects", "Shard effects"], ["timing", "Shard timing"],
  ["titles", "Titles"], ["video", "Video"]];

function Editor() {
  const stale = poolStale();
  return html`<div class="editor">
    <div class="topbar">
      <button class="ghost icon" aria-label="Projects" onClick=${() => { closeProject(); S.view = "projects"; refreshProjects(); update(); }}>‹</button>
      <div class="title" onClick=${() => { S.dialog = { kind: "rename", name: S.name }; update(); }}>${S.name}</div>
      <button class="small" onClick=${renderVideo} disabled=${!S.engine || !!S.busy}>Render</button>
    </div>
    <div class="main">
      <${Stage} />
      <div class="side">
        <div class="tabs" role="tablist">${TABS.map(([k, l]) => html`<button role="tab" class=${S.tab === k ? "on" : ""}
          onClick=${() => { S.tab = k; update(); }}>${l}${k === "media" && (stale || !S.project.music) ? html`<span class="dot"></span>` : null}</button>`)}</div>
        <div class="panel" key=${S.tab}>${{ media: MediaPanel, layers: LayersPanel, effects: EffectsPanel, timing: TimingPanel,
          titles: TitlesPanel, video: VideoPanel }[S.tab]()}</div>
      </div>
    </div>
  </div>`;
}

// ---- Media --------------------------------------------------------------------------------------

function MediaPanel() {
  const p = S.project, a = S.analysis, cut = p.cut;
  const stale = poolStale();
  const [w, h] = poolSize();
  const cutting = S.busy && S.busy.label === "Cutting shards";
  return html`
    <h2>Song</h2>
    ${p.music ? html`<p>${p.music.split("/").pop()}</p>` : html`<p class="warn-text">Choose the song the video is cut to.</p>`}
    ${a && html`<p class="faint">${clock(a.duration)} · ${a.tempo.toFixed(1)} BPM · ${a.beats.length} beats</p>`}
    <button onClick=${chooseSong}>${p.music ? "Change song…" : "Choose song…"}</button>

    <h2>Videos and photos</h2>
    <button onClick=${addMedia}>Add videos and photos…</button>
    ${!p.sources.length && html`<p class="faint">Shards are cut at random from these.</p>`}
    ${p.sources.map((s, i) => html`<${SourceCard} s=${s} i=${i} key=${s.id} />`)}

    <h2>Cut</h2>
    <${Slider} label="video shards" value=${cut.count} min=${4} max=${400} step=${1} fmt=${v => v}
      on=${v => { cut.count = v; changed(); }} />
    <${Slider} label="shortest" value=${cut.min} min=${0.05} max=${4} step=${0.05} fmt=${v => `${v.toFixed(2)} s`}
      on=${v => { cut.min = v; cut.max = Math.max(cut.max, v); changed(); }} />
    <${Slider} label="longest" value=${cut.max} min=${cut.min} max=${8} step=${0.05} fmt=${v => `${v.toFixed(2)} s`}
      on=${v => { cut.max = v; changed(); }} />
    <${Field} label="seed">
      <input type="number" value=${cut.seed} style="width:110px" onChange=${e => { cut.seed = Math.trunc(e.currentTarget.valueAsNumber || 1); changed(); }} />
      <button class="small" title="The same seed always cuts the same shards"
        onClick=${() => { cut.seed = 1 + Math.floor(Math.random() * 99999); changed(); }}>new</button>
    </${Field}>
    <${Field} label="shard detail">
      <${Select} value=${cut.detail} options=${Object.entries(formats.DETAIL)} on=${v => { cut.detail = Number(v); changed(); }} />
    </${Field}>
    <p class="faint">Shards ${w}×${h}. Higher detail looks sharper in big videos but takes more room and time.</p>
    <div class="row">
      <button class=${stale ? "warn" : ""} disabled=${cutting || !p.sources.length} onClick=${cutShards}>Cut shards</button>
      ${cutting ? html`<span>cutting…</span>` : stale ? html`<span class="warn-text">${S.pool ? "out of date" : "not cut yet"}</span>`
        : S.pool ? html`<span class="faint">${S.pool.shards.length} shards, up to date</span>` : null}
    </div>`;
}
const cutShards = () => cut();

function SourceCard({ s, i }) {
  const p = S.project;
  const info = S.info[s.path];
  const move = d => {
    const j = i + d;
    if (j < 0 || j >= p.sources.length) return;
    [p.sources[i], p.sources[j]] = [p.sources[j], p.sources[i]];
    changed();
  };
  const remove = () => {
    const gone = p.sources.splice(i, 1)[0];
    for (const lane of p.lanes) {
      if (lane.sources) {
        lane.sources = lane.sources.filter(x => x !== gone.id);
        if (!lane.sources.length) lane.sources = null;
      }
    }
    changed();
  };
  return html`<details class="card">
    <summary>
      <div class="source grow">
        <input type="checkbox" checked=${s.enabled} title="Use this in the cut" onClick=${e => e.stopPropagation()}
          onChange=${e => { s.enabled = e.currentTarget.checked; changed(); }} />
        ${S.thumbs[s.path] ? html`<img class="thumb" src=${S.thumbs[s.path]} alt="" />` : html`<div class="thumb"></div>`}
        <div style="min-width:0"><div class="name">${s.path.split("/").pop()}</div>
          <div class="faint">${s.kind}${info ? ` · ${info.width}×${info.height}${s.kind === "video" ? ` · ${clock(info.duration)}` : ""}` : ""}</div></div>
        <div class="row tight">
          <button class="small ghost" aria-label="Move up" onClick=${e => { e.preventDefault(); move(-1); }}>↑</button>
          <button class="small ghost" aria-label="Move down" onClick=${e => { e.preventDefault(); move(1); }}>↓</button>
          <button class="small ghost danger" aria-label="Remove" onClick=${e => { e.preventDefault(); remove(); }}>✕</button>
        </div>
      </div>
    </summary>
    ${s.kind === "video" ? html`
      <${Slider} label="share of cuts" value=${s.weight} min=${0} max=${4} step=${0.1} fmt=${v => `×${v.toFixed(1)}`}
        on=${v => { s.weight = v; changed(); }} />
      <${Slider} label="from" value=${s.start} min=${0} max=${info ? info.duration : 60} step=${0.1} fmt=${v => `${v.toFixed(1)} s`}
        on=${v => { s.start = v; if (s.end !== null && s.end < v) s.end = v; changed(); }} />
      <${Slider} label="to" value=${s.end === null ? (info ? info.duration : 60) : s.end} min=${0} max=${info ? info.duration : 60}
        step=${0.1} fmt=${v => `${v.toFixed(1)} s`}
        on=${v => { s.end = info && v >= info.duration - 0.05 ? null : Math.max(v, s.start); changed(); }} />`
    : html`
      <${Field} label="motion"><${Select} value=${s.motion} on=${v => { s.motion = v; changed(); }}
        options=${[["still", "still"], ["zoom_in", "zoom in"], ["zoom_out", "zoom out"], ["pan", "pan"], ["random", "random"]]} /></${Field}>
      <${Slider} label="shards" value=${s.shards} min=${1} max=${8} step=${1} fmt=${v => v} on=${v => { s.shards = v; changed(); }} />`}
    <div class="row"><span class="faint">in layers</span>
      ${p.lanes.map(lane => html`<${Check} label=${lane.name} value=${lane.sources === null || lane.sources.includes(s.id)}
        on=${v => {
          const ids = p.sources.map(x => x.id);
          let cur = lane.sources === null ? ids : [...lane.sources];
          cur = cur.filter(x => x !== s.id).concat(v ? [s.id] : []);
          lane.sources = ids.every(x => cur.includes(x)) ? null : cur;
          changed();
        }} />`)}
    </div>
  </details>`;
}

// ---- Layers -------------------------------------------------------------------------------------

const CHOICE_LABELS = { random: "Random", bag: "Shuffle (each once)", no_repeat: "Random, no repeats", intensity: "Follow the music" };

function LayersPanel() {
  const p = S.project;
  const pi = Math.min(S.pattern, p.patterns.length - 1);
  const pat = p.patterns[pi];
  const timed = pacelib.settings(p).on;
  const targets = [...Object.keys(fxlib.ALL_FX).map(n => [`master.${n}`, `whole picture: ${n}`]),
    ...Object.keys(fxlib.FX).map(n => [`all.${n}`, `every layer: ${n}`]),
    ...p.lanes.flatMap((l, li) => Object.keys(fxlib.FX).map(n => [`lane${li}.${n}`, `${l.name}: ${n}`]))];
  const addLane = () => {
    p.lanes.push(Lane({ name: `Layer ${p.lanes.length + 1}`, blend: "screen", opacity: 0.8, choice: "intensity" }));
    normalise(p);
    changed("lanes");
  };
  const removeLane = li => {
    p.lanes.splice(li, 1);
    for (const pt of p.patterns) pt.steps.splice(li, 1);
    p.modulations = p.modulations.filter(m => m.target.split(".")[0] !== `lane${li}`).map(m => {
      const [scope, name] = m.target.split(".");
      const n = scope.startsWith("lane") ? Number(scope.slice(4)) : -1;
      return n > li ? { ...m, target: `lane${n - 1}.${name}` } : m;
    });
    changed("lanes");
  };
  return html`
    <h2>Layers</h2>
    <p class="faint">Each layer plays shards on top of the ones above it, mixed by its blend.</p>
    ${p.lanes.map((lane, li) => html`<details class="card" key=${li}>
      <summary><span class="grow">${lane.name}</span>${lane.mute ? html`<span class="faint">muted</span>` : null}</summary>
      <${Field} label="name"><input type="text" value=${lane.name} onChange=${e => { lane.name = e.currentTarget.value || `Layer ${li + 1}`; changed(); }} /></${Field}>
      <${Check} label="Muted" value=${lane.mute} on=${v => { lane.mute = v; changed(); }} />
      <${Field} label="blend"><${Select} value=${lane.blend} options=${fxlib.BLEND_MODES.map(b => [b, b])} on=${v => { lane.blend = v; changed(); }} /></${Field}>
      <${Slider} label="opacity" value=${lane.opacity} on=${v => { lane.opacity = v; changed(); }} />
      <${Field} label="picks shards"><${Select} value=${lane.choice} options=${CHOICES.map(c => [c, CHOICE_LABELS[c]])} on=${v => { lane.choice = v; changed(); }} /></${Field}>
      <h2>Effects on this layer</h2>
      <${FxSliders} amounts=${lane.fx} names=${fxlib.FX} onChange=${() => changed()} />
      ${p.lanes.length > 1 && html`<button class="small danger" onClick=${() => removeLane(li)}>Remove this layer</button>`}
    </details>`)}
    ${p.lanes.length < 4 && html`<button class="small" onClick=${addLane}>+ Layer</button>`}

    <h2>Steps</h2>
    ${timed ? html`<p class="warn-text">Shard timing is on, so these steps don't cut the shards (Shard timing tab).</p>` : null}
    <div class="row">
      ${p.patterns.map((pt, k) => html`<button class=${`small ${k === pi ? "primary" : ""}`} onClick=${() => {
        S.pattern = k;
        if (S.engine && !p.song.length) { S.engine.livePattern = k; }
        changed();
      }}>${pt.name}</button>`)}
      ${p.patterns.length < 8 && html`<button class="small" onClick=${() => {
        const copy = JSON.parse(JSON.stringify(pat));
        copy.name = "ABCDEFGH"[p.patterns.length];
        p.patterns.push(copy);
        S.pattern = p.patterns.length - 1;
        changed();
      }}>+ copy</button>`}
    </div>
    <div class="grid" style=${`grid-template-columns: 64px repeat(${pat.length}, minmax(0, 1fr)); margin-top: 8px`}>
      ${p.lanes.map((lane, li) => [html`<div class="lane-name">${lane.name}</div>`,
        pat.steps[li].map((st, si) => html`<button class=${`cell ${si % 4 === 0 ? "beat" : ""} ${st.on ? "on" : ""}`}
          aria-label=${`${lane.name} step ${si + 1}`} onClick=${() => { st.on = !st.on; changed(); }}></button>`)])}
    </div>
    <p class="faint">A lit step cuts to a new shard on that 16th of a bar.</p>
    <h2>Arrangement</h2>
    <p class="faint">${p.song.length ? "The patterns play in this order, round and round." : `Only pattern ${pat.name} plays. Add entries to play patterns in turn.`}</p>
    ${p.song.map((e, k) => html`<div class="row" key=${k}>
      <${Select} value=${e.pattern} options=${p.patterns.map((pt, j) => [j, `Pattern ${pt.name}`])} on=${v => { e.pattern = Number(v); changed(); }} />
      <span>×</span><input type="number" min="1" max="64" value=${e.repeats} style="width:70px"
        onChange=${ev => { e.repeats = Math.max(1, Math.trunc(ev.currentTarget.valueAsNumber || 1)); changed(); }} />
      <button class="small ghost danger" onClick=${() => { p.song.splice(k, 1); changed(); }}>✕</button>
    </div>`)}
    <button class="small" onClick=${() => { p.song.push({ pattern: pi, repeats: 4 }); changed(); }}>+ Entry</button>

    <h2>Whole picture</h2>
    <${FxSliders} amounts=${p.master_fx} names=${fxlib.ALL_FX} onChange=${() => changed()} />

    <h2>The music moves these</h2>
    ${p.modulations.map((m, k) => html`<div class="card" key=${k}>
      <div class="row"><${Select} value=${m.source} options=${fxlib.SOURCES.map(s => [s, s])} on=${v => { m.source = v; changed(); }} />
        <span>moves</span>
        <${Select} value=${m.target} options=${targets} on=${v => { m.target = v; changed(); }} />
        <button class="small ghost danger" onClick=${() => { p.modulations.splice(k, 1); changed(); }}>✕</button></div>
      <${Slider} label="amount" value=${m.amount} min=${-1} max=${1} on=${v => { m.amount = v; changed(); }} />
    </div>`)}
    <button class="small" onClick=${() => { p.modulations.push(Modulation()); changed(); }}>+ Link</button>

    <h2>Tempo and colour</h2>
    <${Field} label="tempo"><${Select} value=${p.tempo_scale} options=${[[0.5, "half time"], [1, "as tracked"], [2, "double time"]]}
      on=${v => { p.tempo_scale = Number(v); changed("tempo"); }} /></${Field}>
    <${Check} label="Fixed tempo instead of the tracked beats" value=${!!p.grid} on=${v => {
      p.grid = v ? { bpm: Math.round((S.analysis ? S.analysis.tempo : 120) * 10) / 10, offset: 0 } : null; changed("tempo"); }} />
    ${p.grid && html`<${Field} label="BPM"><input type="number" step="0.1" value=${p.grid.bpm} style="width:100px"
      onChange=${e => { p.grid.bpm = Math.max(20, e.currentTarget.valueAsNumber || 120); changed("tempo"); }} /></${Field}>
      <${Slider} label="first beat at" value=${p.grid.offset} min=${0} max=${4} step=${0.01} fmt=${v => `${v.toFixed(2)} s`}
        on=${v => { p.grid.offset = v; changed("tempo"); }} />`}
    <${Field} label="palette changes"><${Select} value=${p.palette_mode}
      options=${[["intensity", "when the music changes"], ["bars", "every few bars"], ["off", "never"]]}
      on=${v => { p.palette_mode = v; changed("tempo"); }} /></${Field}>
    ${p.palette_mode === "bars" && html`<${Slider} label="bars" value=${p.palette_bars} min=${1} max=${32} step=${1} fmt=${v => v}
      on=${v => { p.palette_bars = v; changed("tempo"); }} />`}`;
}

// ---- Shard effects ------------------------------------------------------------------------------------

function syncLabel(sync) {
  if (sync <= -0.95) return "between";
  if (sync >= 0.95) return "on pulse";
  if (Math.abs(sync) < 0.05) return "any";
  return `${sync < 0 ? "betw." : "on"} ${Math.round(Math.abs(sync) * 100)}%`;
}

function tryEffect(name) {
  if (!S.engine || !S.program) return say("The song and the shards need to be ready first.");
  let t = S.program.songTime(S.T);
  if (t === null) {
    t = S.program.songStart;
    seek(S.program.programTime(t));
  }
  if (!S.player.playing) play();
  S.engine.events.fire(name, t);
}

function EffectsPanel() {
  const p = S.project;
  const on = eventlib.NAMES.filter(n => eventlib.setting(p, n).rate > 0).length;
  const surprise = () => {
    const names = [...eventlib.NAMES].sort(() => Math.random() - 0.5).slice(0, 4);
    p.shard_fx = Object.fromEntries(names.map(n => [n, { rate: Math.round((0.15 + Math.random() * 0.35) * 100) / 100,
      sync: [-1, 0, 1][Math.floor(Math.random() * 3)], strength: Math.round((0.5 + Math.random() * 0.5) * 100) / 100 }]));
    changed();
  };
  return html`
    <p class="muted">Effects that strike now and then. How often, whether they land on the pulses (the beats and hits)
      or between them, and how hard they hit the picture.</p>
    <div class="row"><button class="small" onClick=${() => { p.shard_fx = {}; changed(); }}>All off</button>
      <button class="small" onClick=${surprise}>Surprise me</button><span class="faint">${on} on</span></div>
    ${eventlib.GROUPS.map(group => html`<h2>${group}</h2>
      ${eventlib.NAMES.filter(n => eventlib.EVENTS[n].group === group).map(name => {
        const ev = eventlib.EVENTS[name];
        const s = eventlib.setting(p, name);
        const set = (k, v) => {
          s[k] = Math.round(v * 1000) / 1000;
          p.shard_fx[name] = s;
          changed();
        };
        return html`<div class="fxrow" key=${name}>
          <div><div class=${s.rate > 0 ? "" : "muted"}>${ev.label}</div><div class="faint">${ev.about}</div></div>
          <button class="small" title="Set it off now in the preview (not saved)" onClick=${() => tryEffect(name)}>try</button>
          <${Slider} label="how often" value=${s.rate} fmt=${v => (v <= 0 ? "off" : pct(v))} on=${v => set("rate", v)} />
          <${Slider} label="pulse" value=${s.sync} min=${-1} max=${1} fmt=${syncLabel} on=${v => set("sync", v)}
            title="Left: strikes between the beats. Middle: anywhere. Right: on the beats and hits." />
          <${Slider} label="strength" value=${s.strength} on=${v => set("strength", v)} />
        </div>`;
      })}`)}
    <p class="faint">Strikes fall the same way every time you play or render.</p>`;
}

// ---- Shard timing ---------------------------------------------------------------------------------------

function TimingPanel() {
  const p = S.project;
  const pace = pacelib.settings(p);
  const set = () => { p.pace = pace; changed(); };
  let intensity = 0.5, g = null;
  if (S.engine && S.program) {
    const t = S.program.songTime(S.T);
    if (t !== null) {
      intensity = S.engine.analysis.at(t).intensity ?? 0.5;
      g = S.engine.stepAt(t);
    }
  }
  const now = pacelib.shares(pace, intensity);
  const off = !pace.on;
  const surprise = () => {
    pace.weights = pacelib.LENGTHS.map(() => (Math.random() < 0.67 ? 0 : Math.round((0.2 + Math.random() * 0.8) * 100) / 100));
    if (!pace.weights.some(x => x)) pace.weights[2 + Math.floor(Math.random() * 3)] = 1;
    pace.tilt = Math.round((Math.random() * 1.2 - 0.6) * 100) / 100;
    pace.repeats = [1, 2, 4, 4, 4, 8][Math.floor(Math.random() * 6)];
    pace.intensity = Math.round((0.2 + Math.random() * 0.7) * 100) / 100;
    set();
  };
  return html`
    <${Check} label="Hold shards for beats at a time" value=${pace.on} on=${v => { pace.on = v; set(); }} />
    <p class="faint">Instead of the sequencer's cuts, each layer holds one shard and plays it over and over, then moves on.</p>
    <h2>Speed of change</h2>
    <${Slider} label="speed" value=${pace.tilt} min=${-1} max=${1} disabled=${off}
      fmt=${v => (v < -0.05 ? "faster" : v > 0.05 ? "slower" : "as weighted")} on=${v => { pace.tilt = v; set(); }}
      title="Leans every length below towards shorter (left) or longer (right)." />
    <${Slider} label="plays of each shard" value=${pace.repeats} min=${1} max=${16} step=${1} fmt=${v => v} disabled=${off}
      on=${v => { pace.repeats = v; set(); }} />
    <${Slider} label="faster when intense" value=${pace.intensity} disabled=${off} on=${v => { pace.intensity = v; set(); }}
      title="When the music gets more intense, shorter lengths come up more often (and in calm parts, longer ones)." />
    <h2>Lengths</h2>
    <p class="faint">Weights, and the chance of each right now (music intensity ${intensity.toFixed(2)}).</p>
    ${pacelib.LABELS.map((label, i) => html`<div class="share" key=${i}>
      <span>${label}</span>
      <input type="range" min="0" max="1" step="0.01" value=${pace.weights[i]} disabled=${off} aria-label=${`${label} weight`}
        onInput=${e => { pace.weights[i] = Math.round(e.currentTarget.valueAsNumber * 1000) / 1000; set(); }} />
      <div><div class="bar" style=${`width:${Math.round(now[i] * 100)}%; opacity:${now[i] > 0 ? 1 : 0.2}`}></div></div>
      <span class="faint" style="text-align:right">${pace.weights[i] <= 0 ? "off" : pct(now[i])}</span>
    </div>`)}
    <div class="row" style="margin-top:10px">
      <button class="small" disabled=${off} onClick=${surprise}>Surprise me</button>
      <button class="small" disabled=${off} onClick=${() => { p.pace = { ...pacelib.defaultPace(), on: pace.on }; changed(); }}>Reset</button>
    </div>
    ${pace.on && g !== null && S.engine ? html`<h2>Now</h2>${p.lanes.map((lane, li) => {
      const ph = S.engine.phrase(li, g);
      if (!ph) return null;
      const [first, every, plays, shard, beats] = ph;
      const play = Math.floor((g - first) / every) + 1;
      const k = pacelib.LENGTHS.indexOf(beats);
      return html`<p>${lane.name}: shard ${shard}, ${k >= 0 ? pacelib.LABELS[k] : `${beats} beats`}, play ${Math.min(play, plays)} of ${plays}</p>`;
    })}` : null}`;
}

// ---- Titles -----------------------------------------------------------------------------------------------

function TitlesPanel() {
  const p = S.project;
  const add = t => { p.titles.push(t); S.selTitle = p.titles.length - 1; changed(); };
  const i = Math.min(S.selTitle, p.titles.length - 1);
  const t = p.titles[i];
  const ch = () => changed();
  const show = () => {
    if (!S.program) return;
    const pl = S.program.placed.find(x => x.index === i);
    if (pl) { seek(pl.t0 + Math.min(pl.t1 - pl.t0, Math.max(pl.title.fade, 0) + 0.3)); update(); }
  };
  const choosePicture = async () => {
    const [file] = await pick("image/*");
    if (!file) return;
    t.image = await importFile(file);
    t.background = "image";
    if (S.renderer) S.renderer.titleImages = await loadTitleImages(p);
    changed();
  };
  return html`
    <div class="row">
      <button class="small" onClick=${() => add(Title({ text: S.name, at: "start", duration: 4 }))}>+ Opening title</button>
      <button class="small" onClick=${() => add(Title({ text: "Thanks for watching", at: "end", duration: 4, background: "colour" }))}>+ Closing title</button>
    </div>
    ${!p.titles.length && html`<p class="faint">No titles yet.</p>`}
    ${p.titles.map((x, k) => html`<button class=${`small ${k === i ? "primary" : ""}`} style="margin:6px 6px 0 0"
      onClick=${() => { S.selTitle = k; update(); }}>${x.at === "start" ? "Opening" : "Closing"}: ${(x.text || "(no text)").split("\n")[0]}</button>`)}
    ${t && html`
      <h2>Title</h2>
      <textarea value=${t.text} aria-label="Title text" onInput=${e => { t.text = e.currentTarget.value; ch(); }}></textarea>
      <${Field} label="subtitle"><input type="text" value=${t.subtitle} onInput=${e => { t.subtitle = e.currentTarget.value; ch(); }} /></${Field}>
      <${Field} label="when"><${Select} value=${t.at} options=${[["start", "at the start"], ["end", "at the end"]]} on=${v => { t.at = v; ch(); }} /></${Field}>
      <${Slider} label="seconds" value=${t.duration} min=${0.5} max=${20} step=${0.1} fmt=${fix(1)} on=${v => { t.duration = v; ch(); }} />
      <${Check} label=${`On its own card, ${t.at === "start" ? "before" : "after"} the song`} value=${t.extra} on=${v => { t.extra = v; ch(); }} />
      <${Slider} label="fade" value=${t.fade} min=${0} max=${3} step=${0.1} fmt=${v => `${v.toFixed(1)} s`} on=${v => { t.fade = v; ch(); }} />
      <h2>Look</h2>
      <${Field} label="position"><${Select} value=${t.position} options=${POSITIONS.map((x, k) => [x, ["upper third", "middle", "lower third"][k]])} on=${v => { t.position = v; ch(); }} /></${Field}>
      <${Slider} label="text size" value=${t.size} min=${0.03} max=${0.3} on=${v => { t.size = v; ch(); }} />
      <${Field} label="text colour"><input type="color" value=${t.text_colour} onInput=${e => { t.text_colour = e.currentTarget.value; ch(); }} />
        <${Check} label="shadow" value=${t.shadow} on=${v => { t.shadow = v; ch(); }} /></${Field}>
      <${Field} label="font"><${Select} value=${FONTS.some(([f]) => f === t.font) ? t.font : ""} options=${FONTS} on=${v => { t.font = v; ch(); }} /></${Field}>
      <${Field} label="background"><${Select} value=${t.background} options=${[["none", "none (over the picture)"], ["colour", "colour"], ["image", "picture"]]}
        on=${v => { t.background = v; ch(); }} /></${Field}>
      ${(t.background === "colour" || (t.extra && t.background === "none")) && html`<${Field} label="background colour">
        <input type="color" value=${t.colour} onInput=${e => { t.colour = e.currentTarget.value; if (t.background === "none") t.background = "colour"; ch(); }} /></${Field}>`}
      ${t.background === "image" && html`<${Field} label="picture"><button class="small" onClick=${choosePicture}>Choose…</button>
        <span class="faint">${t.image ? t.image.split("/").pop() : "(none)"}</span></${Field}>`}
      <div class="row" style="margin-top:12px">
        <button class="small" onClick=${() => { if (i > 0) { [p.titles[i - 1], p.titles[i]] = [p.titles[i], p.titles[i - 1]]; S.selTitle = i - 1; ch(); } }}>Move up</button>
        <button class="small" onClick=${() => { if (i < p.titles.length - 1) { [p.titles[i + 1], p.titles[i]] = [p.titles[i], p.titles[i + 1]]; S.selTitle = i + 1; ch(); } }}>Move down</button>
        <button class="small" onClick=${show}>Show it</button>
        <button class="small danger" onClick=${() => { p.titles.splice(i, 1); S.selTitle = Math.max(0, i - 1); ch(); }}>Delete</button>
      </div>`}`;
}

// ---- Video -----------------------------------------------------------------------------------------------------

function VideoPanel() {
  const p = S.project, out = p.output;
  const dur = S.analysis ? S.analysis.duration : 0;
  const f = formats.BY_KEY[out.format];
  const prog = S.program;
  const playhead = () => (prog ? prog.songTime(S.T) : null);
  const mbits = formats.bitrate(p.width, p.height, p.fps, out.quality) / 1e6 + 0.19;
  const rendering = S.busy && S.busy.label === "Rendering";
  const aspectOff = S.pool && Math.abs(S.pool.width / S.pool.height - p.width / p.height) > 0.02;
  return html`
    <h2>Size</h2>
    <${Select} value=${out.format} options=${[...formats.FORMATS.map(x => [x.key, x.label]), ["custom", "Custom size"]]}
      on=${v => { out.format = v; const g = formats.BY_KEY[v]; if (g) { p.width = g.width; p.height = g.height; } changed(); }} />
    ${f ? (f.note && html`<p class="faint">${f.note}</p>`) : html`<div class="row" style="margin-top:8px">
      <input type="number" value=${p.width} style="width:100px" aria-label="width" onChange=${e => { p.width = formats.even(Math.min(Math.max(e.currentTarget.valueAsNumber || 16, 16), 4096)); changed(); }} />
      <span>×</span>
      <input type="number" value=${p.height} style="width:100px" aria-label="height" onChange=${e => { p.height = formats.even(Math.min(Math.max(e.currentTarget.valueAsNumber || 16, 16), 4096)); changed(); }} /></div>`}
    ${aspectOff && html`<p class="warn-text">The shards were cut for another shape, so they are cropped to fit. Cut shards again (Media) to frame them for this one.</p>`}
    <h2>Frames and quality</h2>
    <${Field} label="frames a second"><${Select} value=${p.fps} options=${formats.FRAME_RATES.map(r => [r, r])} on=${v => { p.fps = Number(v); changed(); }} /></${Field}>
    <${Field} label="quality"><${Select} value=${out.quality} options=${Object.entries(formats.QUALITY).map(([k, v]) => [k, v[0]])} on=${v => { out.quality = v; changed(); }} /></${Field}>
    <h2>Song section</h2>
    <${Slider} label="from" value=${Number(out.start || 0)} min=${0} max=${Math.max(dur, 1)} step=${0.1} fmt=${clock}
      on=${v => { out.start = Math.round(v * 10) / 10; changed(); }} />
    <${Slider} label="to" value=${out.end === null ? dur : out.end} min=${0} max=${Math.max(dur, 1)} step=${0.1} fmt=${clock}
      on=${v => { out.end = dur && v >= dur - 0.05 ? null : Math.round(v * 10) / 10; changed(); }} />
    <div class="row">
      <button class="small" onClick=${() => { const t = playhead(); if (t !== null) { out.start = Math.round(t * 100) / 100; changed(); } }}>From the playhead</button>
      <button class="small" onClick=${() => { const t = playhead(); if (t !== null) { out.end = Math.round(t * 100) / 100; changed(); } }}>To the playhead</button>
      <button class="small" onClick=${() => { out.start = 0; out.end = null; changed(); }}>Whole song</button>
    </div>
    <${Slider} label="fade in" value=${Number(out.fade_in || 0)} min=${0} max=${5} step=${0.1} fmt=${v => `${v.toFixed(1)} s`} on=${v => { out.fade_in = v; changed(); }} />
    <${Slider} label="fade out" value=${Number(out.fade_out || 0)} min=${0} max=${8} step=${0.1} fmt=${v => `${v.toFixed(1)} s`} on=${v => { out.fade_out = v; changed(); }} />
    ${prog && html`<p class="faint">The video is ${clock(prog.duration)} long, about ${Math.round(mbits * prog.duration / 8)} MB.</p>`}
    <h2>Render</h2>
    <button class="primary" disabled=${!S.engine || !!S.busy} onClick=${renderVideo}>Render video</button>
    ${rendering ? null : html`<p class="faint">Keep Shardloom open and the screen on while it renders.</p>`}
    ${S.renders.length ? html`<h2>Rendered</h2>${S.renders.map(r => html`<div class="card row" key=${r.key}>
      <div class="grow"><div>${r.name}</div><div class="faint">${mb(r.size)} · ${new Date(r.created).toLocaleString()}</div></div>
      <button class="small primary" onClick=${() => saveFile(r.blob, r.name)}>Save</button>
      <button class="small" onClick=${() => { S.dialog = { kind: "watch", url: URL.createObjectURL(r.blob) }; update(); }}>Watch</button>
      <button class="small danger" onClick=${async () => { await store.del("renders", r.key); S.renders = S.renders.filter(x => x !== r); update(); }}>✕</button>
    </div>`)}` : null}`;
}

// ---- overlays -----------------------------------------------------------------------------------------------

function Busy() {
  const b = S.busy;
  if (!b) return null;
  const frac = b.total ? b.done / b.total : 0;
  const realtime = b.mode === "realtime";
  return html`<div class="busy" role="status">
    <div class="row"><strong class="grow">${b.label}…</strong>
      ${b.total ? html`<span class="faint">${b.done} / ${b.total}</span>` : null}
      ${["Cutting shards", "Rendering"].includes(b.label) && html`<button class="small" onClick=${() => { b.cancelled = true; }}>Cancel</button>`}</div>
    ${realtime && html`<p class="faint">This browser records the video as it plays, so it takes as long as the video.</p>`}
    <div class="progress"><div style=${`width:${Math.round(frac * 100)}%`}></div></div>
  </div>`;
}

function Dialog() {
  const d = S.dialog;
  if (!d) return null;
  const close = () => { if (d.url) URL.revokeObjectURL(d.url); S.dialog = null; update(); };
  let body;
  if (d.kind === "new") {
    body = html`<h3>New project</h3>
      <input type="text" placeholder="Name" value=${d.name} style="width:100%" onInput=${e => { d.name = e.currentTarget.value; }} />
      <div class="shapes">${SHAPES.map(([key, label, w, h]) => html`<button class=${d.format === key ? "on" : ""}
        onClick=${() => { d.format = key; update(); }}><div class="box" style=${`width:${w > h ? 40 : 40 * w / h}px;height:${h > w ? 40 : 40 * h / w}px`}></div>${label}</button>`)}</div>
      <p class="faint">You can change the size later (Video).</p>
      <div class="row"><button class="primary" onClick=${() => { const name = d.name.trim() || "My video"; S.dialog = null; createProject(name, d.format); }}>Create</button>
        <button onClick=${close}>Cancel</button></div>`;
  } else if (d.kind === "delete") {
    body = html`<h3>Delete ${d.name}?</h3><p>Its media, shards and rendered videos on this device go too.</p>
      <div class="row"><button class="danger" onClick=${async () => { S.dialog = null; await store.deleteProject(d.id); refreshProjects(); }}>Delete</button>
        <button onClick=${close}>Keep it</button></div>`;
  } else if (d.kind === "rename") {
    body = html`<h3>Rename</h3><input type="text" value=${d.name} style="width:100%" onInput=${e => { d.name = e.currentTarget.value; }} />
      <div class="row" style="margin-top:12px"><button class="primary" onClick=${() => { S.name = d.name.trim() || S.name; S.dialog = null; changed(); }}>Rename</button>
        <button onClick=${close}>Cancel</button></div>
      <hr /><button onClick=${() => { S.dialog = null; packProject(S.id); }}>Pack this project…</button>
      <p class="faint">A zip of the project and its media, to keep or to open in the desktop Shardloom.</p>`;
  } else if (d.kind === "packed") {
    body = html`<h3>Packed</h3><p>${d.name} (${mb(d.blob.size)})</p>
      <div class="row"><button class="primary" onClick=${() => saveFile(d.blob, d.name)}>Save</button><button onClick=${close}>Done</button></div>`;
  } else if (d.kind === "watch") {
    body = html`<video src=${d.url} controls playsinline autoplay style="width:100%;max-height:70dvh;background:#000"></video>
      <div class="row" style="margin-top:10px"><button onClick=${close}>Close</button></div>`;
  }
  return html`<div class="dialog-back" onClick=${e => { if (e.target === e.currentTarget) close(); }}><div class="dialog">${body}</div></div>`;
}

function App() {
  const [, tick] = useState(0);
  update = () => tick(n => n + 1);
  return html`${S.view === "edit" && S.project ? html`<${Editor} />` : html`<${Projects} />`}
    <${Busy} />${S.toast && html`<div class="toast" role="alert">${S.toast}</div>`}<${Dialog} />`;
}

render(html`<${App} />`, document.getElementById("app"));
refreshProjects();
window.addEventListener("pagehide", () => { saveNow(); });
document.addEventListener("visibilitychange", () => { if (document.hidden) { saveNow(); if (S.player && S.player.playing) pause(); } });
window.addEventListener("resize", () => { S.needDraw = true; update(); });
if ("serviceWorker" in navigator && location.protocol !== "file:") {
  navigator.serviceWorker.register("sw.js").catch(() => { /* works without it, just not offline */ });
}
// For tests and the curious.
window.shardloom = { S, openProject, createProject, update: () => update(), changed };
