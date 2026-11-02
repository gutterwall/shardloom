// The pool: shards cut at random from the project's videos and photos (as library.build_pool), kept as JPEG
// frames on the phone and decoded on the way to the screen.

import * as store from "./store.js";
import { PyRandom, pyround } from "./rand.js";
import { coverBox } from "./titles.js";

// ---- cutting -------------------------------------------------------------------

// count random [source index, start, length] cuts, spread over the sources by duration (times weight).
export function planCuts(durations, count, minLen, maxLen, rng, weights = null) {
  if (minLen <= 0 || maxLen < minLen) throw new Error("need 0 < shortest <= longest");
  weights = weights || durations.map(() => 1);
  const eligible = durations.map((d, i) => i).filter(i => durations[i] >= minLen && weights[i] > 0);
  if (!eligible.length) return [];
  const taken = new Map(eligible.map(i => [i, []]));
  const cuts = [];
  let attempts = 0;
  while (cuts.length < count && attempts < count * 50) {
    attempts++;
    // A source is picked in proportion to its length.
    const src = rng.choices(eligible, eligible.map(i => durations[i] * weights[i]));
    const dur = durations[src];
    const length = rng.uniform(minLen, Math.min(maxLen, dur));
    const start = rng.uniform(0, Math.max(0, dur - length));
    if (taken.get(src).some(([s, e]) => start < e && start + length > s)) continue;
    taken.get(src).push([start, start + length]);
    cuts.push([src, Math.round(start * 1000) / 1000, Math.round(length * 1000) / 1000]);
  }
  return cuts;
}

// What the pool is cut from: when it changes, the shards are out of date.
export function recipe(project, width, height) {
  return JSON.stringify({
    size: [width, height], fps: project.fps,
    cut: ["count", "min", "max", "seed"].map(k => project.cut[k]),
    sources: project.sources.filter(s => s.enabled)
      .map(s => [s.path, s.kind, s.id, s.weight, s.start, s.end, s.motion, s.shards]),
  });
}

const once = (el, ev) => new Promise((resolve, reject) => {
  const ok = () => { el.removeEventListener("error", bad); resolve(); };
  const bad = () => { el.removeEventListener(ev, ok); reject(new Error("the video can't be read on this phone")); };
  el.addEventListener(ev, ok, { once: true });
  el.addEventListener("error", bad, { once: true });
});

// One video, opened for grabbing frames at given times.
export class VideoGrabber {
  constructor(blob) {
    this.url = URL.createObjectURL(blob);
    const v = document.createElement("video");
    v.muted = true;
    v.playsInline = true;
    v.preload = "auto";
    v.setAttribute("playsinline", "");
    v.style.cssText = "position:fixed;left:-10px;top:-10px;width:2px;height:2px;opacity:0;pointer-events:none";
    this.video = v;
  }

  async open() {
    const v = this.video;
    document.body.appendChild(v);
    const ready = once(v, "loadeddata");
    v.src = this.url;
    await ready;
    this.duration = v.duration;
    this.width = v.videoWidth;
    this.height = v.videoHeight;
    return this;
  }

  async seek(t) {
    const v = this.video;
    t = Math.max(0, Math.min(t, this.duration - 0.001));
    if (Math.abs(v.currentTime - t) < 1e-4 && v.readyState >= 2) return;
    const seeked = once(v, "seeked");
    v.currentTime = t;
    await seeked;
  }

  close() {
    this.video.removeAttribute("src");
    this.video.load();
    this.video.remove();
    URL.revokeObjectURL(this.url);
  }
}

// Draws frames at width x height (cover, centre crop) and turns them into JPEGs and motion scores.
class Framer {
  constructor(width, height) {
    this.canvas = document.createElement("canvas");
    this.canvas.width = width;
    this.canvas.height = height;
    this.ctx = this.canvas.getContext("2d", { willReadFrequently: false });
    this.small = document.createElement("canvas");
    this.small.width = Math.max(1, Math.ceil(width / 8));
    this.small.height = Math.max(1, Math.ceil(height / 8));
    this.sctx = this.small.getContext("2d", { willReadFrequently: true });
    this.width = width;
    this.height = height;
  }

  draw(img, sw, sh, box = null) {
    const [x, y, w, h] = box || coverBox(sw, sh, this.width, this.height);
    this.ctx.drawImage(img, x, y, w, h, 0, 0, this.width, this.height);
  }

  async take() {
    this.sctx.drawImage(this.canvas, 0, 0, this.small.width, this.small.height);
    const px = this.sctx.getImageData(0, 0, this.small.width, this.small.height).data;
    const blob = await new Promise(r => this.canvas.toBlob(r, "image/jpeg", 0.86));
    return [blob, px];
  }
}

// [motion, brightness] of a shard from its small frames, both 0..1.
function scores(smalls) {
  let bright = 0, motion = 0, n = 0;
  smalls.forEach((px, k) => {
    for (let i = 0; i < px.length; i += 4) {
      bright += px[i] + px[i + 1] + px[i + 2];
      if (k) {
        const q = smalls[k - 1];
        motion += Math.abs(px[i] - q[i]) + Math.abs(px[i + 1] - q[i + 1]) + Math.abs(px[i + 2] - q[i + 2]);
      }
    }
    n += px.length / 4 * 3;
  });
  const per = n / smalls.length;
  return [smalls.length > 1 ? motion / (per * (smalls.length - 1)) / 255 : 0, bright / n / 255];
}

const PHOTO_MOTIONS = ["zoom_in", "zoom_out", "pan", "still"];

// Cuts the pool from the project's enabled sources into the store; returns the pool's description.
// files.blob(path) gives a media file; progress(done, total); cancelled() says when to stop.
export async function buildPool(project, projectId, files, width, height, progress = () => {}, cancelled = () => false) {
  const cut = project.cut;
  const fps = project.fps;
  const rng = new PyRandom(Math.trunc(cut.seed ?? 1));
  const minLen = Number(cut.min ?? 0.25), maxLen = Number(cut.max ?? 1.5);
  const videos = project.sources.filter(s => s.enabled && s.kind === "video");
  const photos = project.sources.filter(s => s.enabled && s.kind === "photo");
  if (!videos.length && !photos.length) throw new Error("add a video or a photo first");
  const framer = new Framer(width, height);
  const grabbers = [];
  const shards = [];
  const prefix = `${projectId}/new-`;
  await store.delPrefix("frames", prefix);
  try {
    for (const s of videos) grabbers.push(await new VideoGrabber(await files.blob(s.path)).open());
    const spans = videos.map((s, i) => {
      const d = grabbers[i].duration;
      const start = Math.min(Math.max(0, s.start), d);
      const end = s.end === null || s.end === undefined ? d : Math.min(Math.max(s.end, start), d);
      return [start, end - start];
    });
    const cuts = planCuts(spans.map(([, l]) => l), videos.length ? Math.trunc(cut.count ?? 48) : 0, minLen, maxLen,
      rng, videos.map(s => s.weight));
    const total = cuts.length + photos.reduce((a, p) => a + Math.max(1, p.shards), 0);

    const add = async (frames, smalls, src, start, length) => {
      const id = shards.length;
      await store.put("frames", `${prefix}${id}`, frames);
      const [motion, brightness] = scores(smalls);
      shards.push({ id, file: `${String(id).padStart(4, "0")}.npy`, source: src.path, start, duration: length,
        frames: frames.length, motion, brightness, energy: 0, source_id: src.id });
      progress(shards.length, total);
    };

    for (const [vi, start, length] of cuts) {
      if (cancelled()) throw new Error("cancelled");
      const g = grabbers[vi];
      const count = Math.max(1, pyround(length * fps));
      const frames = [], smalls = [];
      let last = null;
      for (let k = 0; k < count; k++) {
        await g.seek(spans[vi][0] + start + k / fps);
        // A frame the same as the one before (a slower source) is stored once more by reference.
        framer.draw(g.video, g.width, g.height);
        const [blob, px] = await framer.take();
        frames.push(blob);
        smalls.push(px);
        last = blob;
      }
      if (!last) throw new Error("no frames");
      await add(frames, smalls, videos[vi], spans[vi][0] + start, length);
    }
    for (const p of photos) {
      const img = await createImageBitmap(await files.blob(p.path), { imageOrientation: "from-image" });
      for (let n = 0; n < Math.max(1, p.shards); n++) {
        if (cancelled()) throw new Error("cancelled");
        const length = rng.uniform(minLen, maxLen);
        const count = Math.max(1, pyround(length * fps));
        let motion = p.motion;
        if (motion === "random") motion = rng.choice(PHOTO_MOTIONS);
        const frames = [], smalls = [];
        if (motion === "still" || count <= 1) {
          framer.draw(img, img.width, img.height);
          const [blob, px] = await framer.take();
          frames.push(blob);
          smalls.push(px);
        } else {
          const cx = rng.uniform(0.4, 0.6), cy = rng.uniform(0.4, 0.6);
          const dir = rng.choice([-1, 1]);
          for (let k = 0; k < count; k++) {
            let f = k / Math.max(1, count - 1);
            f = f * f * (3 - 2 * f);
            const box = motion === "zoom_in" ? coverBox(img.width, img.height, width, height, 1 + 0.15 * f, cx, cy)
              : motion === "zoom_out" ? coverBox(img.width, img.height, width, height, 1.15 - 0.15 * f, cx, cy)
                : coverBox(img.width, img.height, width, height, 1.12, 0.5 + dir * (f - 0.5) * 0.5, cy);
            framer.draw(img, img.width, img.height, box);
            const [blob, px] = await framer.take();
            frames.push(blob);
            smalls.push(px);
          }
        }
        await add(frames, smalls, p, 0, length);
      }
      img.close();
    }
  } catch (e) {
    await store.delPrefix("frames", prefix);
    throw e;
  } finally {
    for (const g of grabbers) g.close();
  }
  // Rank of motion within the pool, 0 (calmest) .. 1 (busiest).
  [...shards].sort((a, b) => a.motion - b.motion).forEach((s, rank) => { s.energy = rank / Math.max(1, shards.length - 1); });
  // Put the new pool in place of the old one.
  for (const k of await store.keysWithPrefix("frames", `${projectId}/`)) {
    if (!k.startsWith(prefix)) await store.del("frames", k);
  }
  for (const s of shards) {
    const frames = await store.get("frames", `${prefix}${s.id}`);
    await store.put("frames", `${projectId}/${s.id}`, frames);
    await store.del("frames", `${prefix}${s.id}`);
  }
  const pool = { width, height, fps, shards, recipe: recipe(project, width, height) };
  await store.put("pools", projectId, pool);
  // Picks of shards that no longer exist fall back to the lane's choice.
  const ids = new Set(shards.map(s => s.id));
  for (const lane of project.lanes) {
    if (lane.shards) {
      lane.shards = lane.shards.filter(i => ids.has(i));
      if (!lane.shards.length) lane.shards = null;
    }
  }
  for (const pat of project.patterns) for (const row of pat.steps) for (const st of row) {
    if (st.shard !== null && !ids.has(st.shard)) st.shard = null;
  }
  return pool;
}

// ---- playing -------------------------------------------------------------------

// A pool's frames: the JPEGs kept as blobs, a budget of them decoded at a time.
export class PoolFrames {
  constructor(projectId, pool, budget = 160 * 1024 * 1024) {
    this.id = projectId;
    this.pool = pool;
    this.width = pool.width;
    this.height = pool.height;
    this.fps = pool.fps;
    this.blobs = new Map();
    this.cache = new Map();          // "shard:frame" -> ImageBitmap, oldest first
    this.pending = new Map();
    this.limit = Math.max(24, Math.floor(budget / (pool.width * pool.height * 4)));
  }

  async load() {
    for (const s of this.pool.shards) this.blobs.set(s.id, (await store.get("frames", `${this.id}/${s.id}`)) || []);
    return this;
  }

  length(id) {
    return (this.blobs.get(id) || []).length || 1;
  }

  _blob(id, index) {
    const frames = this.blobs.get(id) || [];
    return frames.length ? frames[((index % frames.length) + frames.length) % frames.length] : null;
  }

  // The decoded frame if it is ready, else null (and it starts decoding).
  get(id, index) {
    const key = `${id}:${index}`;
    const img = this.cache.get(key);
    if (img) {
      this.cache.delete(key);
      this.cache.set(key, img);
      return img;
    }
    this.decode(id, index);
    return null;
  }

  decode(id, index) {
    const key = `${id}:${index}`;
    if (this.cache.has(key)) return Promise.resolve(this.cache.get(key));
    let p = this.pending.get(key);
    if (!p) {
      const blob = this._blob(id, index);
      if (!blob) return Promise.resolve(null);
      p = createImageBitmap(blob).then(img => {
        this.pending.delete(key);
        this.cache.set(key, img);
        if (this.onReady) this.onReady();
        while (this.cache.size > this.limit) {
          const [old, oldImg] = this.cache.entries().next().value;
          this.cache.delete(old);
          oldImg.close();
        }
        return img;
      }, () => { this.pending.delete(key); return null; });
      this.pending.set(key, p);
    }
    return p;
  }

  // Starts decoding the next few frames of each [shard, frame, direction] that is playing.
  prefetch(playing, ahead = 6) {
    for (const [id, frame, dir] of playing) {
      for (let k = 1; k <= ahead; k++) {
        if (this.pending.size > 12) return;
        const n = this.length(id);
        this.decode(id, (((frame + dir * k) % n) + n) % n);
      }
    }
  }

  // Waits until these [shard, frame] are decoded.
  ensure(list) {
    return Promise.all(list.map(([id, f]) => this.decode(id, f)));
  }

  close() {
    for (const img of this.cache.values()) img.close();
    this.cache.clear();
  }
}
