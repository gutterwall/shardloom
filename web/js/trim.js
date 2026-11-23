// The trim editor (as shardloom/trim.py): one video, played and scrubbed, with the parts of it shards are cut from.
// Drag a part's ends on the strip, mark the playhead as a start or a stop, add parts and delete them. The parts are
// kept in the video's source (setSourceSpans) as they change, so Cut shards cuts from them.

import { html, useEffect, useRef, useState } from "../vendor/standalone.module.js";
import { VideoGrabber } from "./pool.js";
import { setSourceSpans, sourceSpans } from "./project.js";

export const MIN_PART = 0.1;    // the shortest part, in seconds
export const NEW_PART = 5;      // how long a new part is

// ---- editing the parts (as trim.py) ----------------------------------------------------------------------
//
// Parts are kept as a sorted list of [start, end] that never overlap: every edit clamps to the neighbours instead of
// merging, so the selected part stays the selected part.

const r3 = v => Math.round(v * 1000) / 1000;

function containing(spans, t) {
  const i = spans.findIndex(([a, b]) => a <= t && t <= b);
  return i < 0 ? null : i;
}

function gap(spans, t, duration) {
  let lo = 0, hi = duration;
  for (const [a, b] of spans) {
    if (b <= t) lo = Math.max(lo, b);
    else if (a > t) hi = Math.min(hi, a);
  }
  return [lo, hi];
}

function insert(spans, a, b) {
  spans.push([a, b]);
  spans.sort((x, y) => x[0] - y[0] || x[1] - y[1]);
  return spans.findIndex(s => s[0] === a && s[1] === b);
}

// A new part from t (up to length seconds, as far as the next part); the part already at t if there is one.
export function addPart(spans, t, duration, length = NEW_PART) {
  t = Math.min(Math.max(0, t), duration);
  const i = containing(spans, t);
  if (i !== null) return i;
  const [lo, hi] = gap(spans, t, duration);
  let a = t;
  const b = Math.min(t + length, hi);
  if (b - a < MIN_PART) a = Math.max(lo, b - length);
  if (b - a < MIN_PART) return null;
  return insert(spans, r3(a), r3(b));
}

// The playhead becomes a start: of the part it is in, or of the selected part when it is in the free time just before
// it; otherwise a new part starts there.
export function markIn(spans, sel, t, duration) {
  t = r3(Math.min(Math.max(0, t), duration));
  let i = containing(spans, t);
  if (i === null && sel !== null && sel < spans.length) {
    const lo = sel > 0 ? spans[sel - 1][1] : 0;
    if (lo <= t && t < spans[sel][0]) i = sel;
  }
  if (i === null) return addPart(spans, t, duration);
  spans[i][0] = Math.min(t, r3(spans[i][1] - MIN_PART));
  return i;
}

// The playhead becomes a stop: of the part it is in, or of the selected part when it is in the free time just after
// it; otherwise a new part ends there.
export function markOut(spans, sel, t, duration) {
  t = r3(Math.min(Math.max(0, t), duration));
  let i = containing(spans, t);
  if (i === null && sel !== null && sel < spans.length) {
    const hi = sel + 1 < spans.length ? spans[sel + 1][0] : duration;
    if (spans[sel][1] < t && t <= hi) i = sel;
  }
  if (i === null) {
    const [lo] = gap(spans, t, duration);
    const a = Math.max(lo, t - NEW_PART);
    if (t - a < MIN_PART) return null;
    return insert(spans, r3(a), t);
  }
  spans[i][1] = Math.max(t, r3(spans[i][0] + MIN_PART));
  return i;
}

// Moves the start (side 0) or the stop (side 1) of part i to t, between its neighbours.
export function dragEnd(spans, i, side, t, duration) {
  const [a, b] = spans[i];
  if (side === 0) {
    const lo = i > 0 ? spans[i - 1][1] : 0;
    spans[i][0] = r3(Math.min(Math.max(t, lo), b - MIN_PART));
  } else {
    const hi = i + 1 < spans.length ? spans[i + 1][0] : duration;
    spans[i][1] = r3(Math.max(Math.min(t, hi), a + MIN_PART));
  }
}

// ---- the window ----------------------------------------------------------------------------------------------

const stamp = s => {
  s = Math.max(0, s || 0);
  const m = Math.floor(s / 60);
  return `${m}:${(s - m * 60).toFixed(2).padStart(5, "0")}`;
};

const STRIP_FRAMES = 16;

// src: the video's source; blob: its file; shortest: the shortest shard; onChange() after each edit; onClose().
export function TrimEditor({ src, blob, shortest = 0, onChange, onClose }) {
  const [, tick] = useState(0);
  const redraw = () => tick(n => n + 1);
  const video = useRef(null), strip = useRef(null), film = useRef(null), root = useRef(null);
  const st = useRef(null);
  if (!st.current) st.current = { url: URL.createObjectURL(blob), duration: 0, spans: null, sel: 0, t: 0, drag: null, until: null };
  const s = st.current;

  useEffect(() => {
    root.current && root.current.focus();
    let raf = 0, alive = true;
    // While playing: follow the video, and stop at the end of the part being played.
    const follow = () => {
      const v = video.current;
      if (v && !v.paused) {
        s.t = v.currentTime;
        if (s.until !== null && s.t >= s.until) { v.pause(); s.until = null; }
        redraw();
      }
      if (alive) raf = requestAnimationFrame(follow);
    };
    raf = requestAnimationFrame(follow);
    return () => { alive = false; cancelAnimationFrame(raf); URL.revokeObjectURL(s.url); };
  }, []);

  const loaded = async () => {
    const v = video.current;
    s.duration = v.duration;
    s.spans = sourceSpans(src, s.duration).map(([a, b]) => [a, b]);
    if (!s.spans.length) s.spans = [[0, s.duration]];
    seek(s.spans[0][0]);
    // The strip of frames, drawn as they are found.
    const c = film.current;
    const g = await new VideoGrabber(blob).open().catch(() => null);
    if (!g || !c) return;
    const fh = 54, fw = Math.round(fh * g.width / Math.max(1, g.height));
    c.width = fw * STRIP_FRAMES;
    c.height = fh;
    const ctx = c.getContext("2d");
    try {
      for (let k = 0; k < STRIP_FRAMES && film.current; k++) {
        await g.seek((k + 0.5) / STRIP_FRAMES * g.duration);
        ctx.drawImage(g.video, k * fw, 0, fw, fh);
      }
    } finally {
      g.close();
    }
  };

  const commit = () => { setSourceSpans(src, s.spans, s.duration); onChange(); redraw(); };
  const seek = t => {
    s.t = Math.min(Math.max(0, t), s.duration);
    if (video.current) video.current.currentTime = s.t;
    redraw();
  };
  const playing = () => video.current && !video.current.paused;
  // The video moved by itself (media keys, the end): the playhead follows.
  const sync = () => {
    const v = video.current;
    if (v && !s.drag && Math.abs(v.currentTime - s.t) > 0.02) { s.t = v.currentTime; redraw(); }
  };
  const play = (part = null) => {
    const v = video.current;
    if (!v) return;
    if (part !== null) {
      s.sel = part;
      seek(s.spans[part][0]);
      s.until = s.spans[part][1];
      v.play().catch(() => {});   // interrupted (paused or closed): nothing to do
    } else if (!v.paused) {
      v.pause();
    } else {
      s.until = null;
      if (v.currentTime >= s.duration - 0.05) seek(0);
      v.play().catch(() => {});   // interrupted (paused or closed): nothing to do
    }
    redraw();
  };
  const step = d => { if (video.current) video.current.pause(); seek(s.t + d); };
  const pick = i => { if (i !== null) { s.sel = i; commit(); } };
  const mark = side => pick((side === 0 ? markIn : markOut)(s.spans, s.sel, s.t, s.duration));
  const add = () => pick(addPart(s.spans, s.t, s.duration));
  const remove = () => {
    if (s.sel === null || s.sel >= s.spans.length) return;
    s.spans.splice(s.sel, 1);
    if (!s.spans.length) s.spans = [[0, s.duration]];
    s.sel = Math.min(s.sel, s.spans.length - 1);
    commit();
  };
  const whole = () => { s.spans = [[0, s.duration]]; s.sel = 0; commit(); };

  const keys = e => {
    if (e.target.tagName === "INPUT") return;
    const frame = 1 / 30;
    const k = e.key.toLowerCase();
    if (k === " ") play();
    else if (k === "i") mark(0);
    else if (k === "o") mark(1);
    else if (k === "a") add();
    else if (k === "delete" || k === "backspace") remove();
    else if (k === "arrowleft") step(e.shiftKey ? -1 : -frame);
    else if (k === "arrowright") step(e.shiftKey ? 1 : frame);
    else if (k === "home") seek(0);
    else if (k === "end") seek(s.duration);
    else if (k === "escape") onClose();
    else return;
    e.preventDefault();
  };

  // The strip: touch or click to move the playhead; drag a part's ends.
  const timeAt = e => {
    const r = strip.current.getBoundingClientRect();
    return Math.min(Math.max(0, (e.clientX - r.left) / r.width * s.duration), s.duration);
  };
  const down = e => {
    if (!s.spans) return;
    const r = strip.current.getBoundingClientRect();
    const px = e.clientX - r.left;
    let near = null;
    s.spans.forEach(([a, b], i) => [a, b].forEach((x, side) => {
      const d = Math.abs(px - x / s.duration * r.width);
      if (d <= (e.pointerType === "touch" ? 18 : 8) && (!near || d < near[0])) near = [d, i, side];
    }));
    strip.current.setPointerCapture(e.pointerId);
    if (near) {
      s.drag = [near[1], near[2]];
      s.sel = near[1];
      if (video.current) video.current.pause();
    } else {
      s.drag = "seek";
      const i = containing(s.spans, timeAt(e));
      if (i !== null) s.sel = i;
    }
    move(e);
  };
  const move = e => {
    if (!s.drag) return;
    const t = timeAt(e);
    if (s.drag === "seek") { seek(t); return; }
    const [i, side] = s.drag;
    dragEnd(s.spans, i, side, t, s.duration);
    seek(s.spans[i][side]);
    commit();
  };
  const up = () => { s.drag = null; };
  const dbl = e => { if (s.spans && containing(s.spans, timeAt(e)) === null) { seek(timeAt(e)); add(); } };

  const spans = s.spans || [];
  const d = s.duration || 1;
  const pc = x => `${x / d * 100}%`;
  const isWhole = spans.length === 1 && spans[0][0] <= 0 && spans[0][1] >= d - 0.05;
  const used = spans.reduce((n, [a, b]) => n + b - a, 0);
  const short = spans.map(([a, b], i) => (shortest && b - a < shortest ? i + 1 : 0)).filter(Boolean);
  const gaps = [];
  let at = 0;
  for (const [a, b] of spans) { if (a > at) gaps.push([at, a]); at = b; }
  if (at < d) gaps.push([at, d]);

  return html`<div class="trim-back" onClick=${e => { if (e.target === e.currentTarget) onClose(); }}>
    <div class="trim" tabindex="0" ref=${root} onKeyDown=${keys} role="dialog" aria-label=${`Trim ${src.path.split("/").pop()}`}>
      <div class="row"><strong class="grow">Trim ${src.path.split("/").pop()}</strong>
        <button class="small" onClick=${onClose}>Done</button></div>
      <p class="faint">${s.duration ? `${stamp(s.duration)} long · ${isWhole ? "the whole video is used"
        : `${spans.length} part${spans.length === 1 ? "" : "s"}, ${stamp(used)} used`}` : "Loading…"}</p>
      <video ref=${video} src=${s.url} muted playsinline preload="auto" onLoadedMetadata=${loaded}
        onPause=${redraw} onPlay=${redraw} onClick=${() => play()} onSeeked=${sync} onTimeUpdate=${sync}></video>
      <div class="row tight">
        <button class="small" onClick=${() => play()}>${playing() ? "Pause" : "Play"}</button>
        <button class="small" aria-label="Back a frame" onClick=${() => step(-1 / 30)}>◀</button>
        <button class="small" aria-label="On a frame" onClick=${() => step(1 / 30)}>▶</button>
        <span class="time">${stamp(s.t)} / ${stamp(s.duration)}</span>
      </div>
      <div class="trim-strip" ref=${strip} onPointerDown=${down} onPointerMove=${move} onPointerUp=${up}
        onPointerCancel=${up} onDblClick=${dbl}>
        <canvas ref=${film}></canvas>
        ${gaps.map(([a, b]) => html`<div class="trim-gap" style=${`left:${pc(a)};width:${pc(b - a)}`}></div>`)}
        ${spans.map(([a, b], i) => html`<div class=${`trim-part${i === s.sel ? " sel" : ""}`}
          style=${`left:${pc(a)};width:${pc(b - a)}`}><span>${i + 1}</span></div>`)}
        <div class="trim-head" style=${`left:${pc(s.t)}`}></div>
      </div>
      <div class="row tight wrap">
        <button class="small" onClick=${() => mark(0)} title="I">Set start</button>
        <button class="small" onClick=${() => mark(1)} title="O">Set stop</button>
        <button class="small" onClick=${add} title="A">Add part</button>
        <button class="small" disabled=${isWhole} onClick=${remove}>Delete part</button>
        <button class="small" disabled=${isWhole} onClick=${whole}>Whole video</button>
      </div>
      <div class="trim-parts">
        ${spans.map(([a, b], i) => html`<div class=${`trim-row${i === s.sel ? " sel" : ""}`}>
          <button class="small ghost" onClick=${() => { s.sel = i; seek(a); }}>${i + 1}</button>
          ${[a, b].map((x, side) => html`<input type="number" step="0.01" min="0" max=${s.duration} value=${x.toFixed(2)}
            aria-label=${`part ${i + 1} ${side ? "stop" : "start"}`}
            onChange=${e => { s.sel = i; dragEnd(s.spans, i, side, e.currentTarget.valueAsNumber || 0, s.duration); seek(s.spans[i][side]); commit(); }} />`)}
          <span class="faint">${(b - a).toFixed(2)} s</span>
          <button class="small" onClick=${() => play(i)}>play</button>
        </div>`)}
      </div>
      ${short.length ? html`<p class="warn-text">Part${short.length > 1 ? "s" : ""} ${short.join(", ")} ${short.length > 1 ? "are" : "is"} shorter
        than the shortest shard (${shortest.toFixed(2)} s) and won't be cut from.</p>` : null}
      <p class="faint">Tap the strip to move the playhead and drag a part's ends. Keys: Space play, I start, O stop,
        A add, Delete, ←/→ a frame.</p>
    </div>
  </div>`;
}
