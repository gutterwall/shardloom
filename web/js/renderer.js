// Draws a frame state with WebGL2, as renderer.py and compose.Compositor: lanes through the effects, blended,
// the master pass, the shard effects, then the titles and the fade, onto a canvas.

import * as fxlib from "./fx.js";
import * as eventlib from "./events.js";
import * as shaders from "./shaders.js";
import { renderTitle } from "./titles.js";

class Prog {
  constructor(gl, fragment) {
    this.gl = gl;
    const compile = (type, src) => {
      const s = gl.createShader(type);
      gl.shaderSource(s, src);
      gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(`shader: ${gl.getShaderInfoLog(s)}`);
      return s;
    };
    const p = gl.createProgram();
    gl.attachShader(p, compile(gl.VERTEX_SHADER, shaders.VERTEX));
    gl.attachShader(p, compile(gl.FRAGMENT_SHADER, fragment));
    gl.bindAttribLocation(p, 0, "pos");
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(`shader: ${gl.getProgramInfoLog(p)}`);
    this.p = p;
    this.loc = {};
    this.sizes = {};
    for (let i = 0; i < gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS); i++) {
      const u = gl.getActiveUniform(p, i);
      const name = u.name.replace(/\[0\]$/, "");
      this.loc[name] = gl.getUniformLocation(p, name);
      this.sizes[name] = u.size;
    }
  }

  use() {
    this.gl.useProgram(this.p);
    return this;
  }

  // Sets a uniform the shader uses (the driver drops the ones it doesn't).
  f(name, ...v) {
    const l = this.loc[name];
    if (!l) return;
    const gl = this.gl;
    if (v.length === 1) gl.uniform1f(l, v[0]);
    else if (v.length === 2) gl.uniform2f(l, v[0], v[1]);
    else gl.uniform3f(l, v[0], v[1], v[2]);
  }

  i(name, v) {
    if (this.loc[name]) this.gl.uniform1i(this.loc[name], v);
  }

  fv(name, values) {
    if (this.loc[name]) this.gl.uniform1fv(this.loc[name], values.slice(0, this.sizes[name]));
  }
}

export class Renderer {
  // frames: {get(shardId, index) -> ImageBitmap | null}; width x height: the picture drawn.
  constructor(canvas, width, height, frames) {
    this.canvas = canvas;
    canvas.width = width;
    canvas.height = height;
    const gl = canvas.getContext("webgl2", { alpha: false, antialias: false, depth: false, stencil: false,
      premultipliedAlpha: false, preserveDrawingBuffer: false, powerPreference: "high-performance" });
    if (!gl) throw new Error("This browser can't draw with WebGL2.");
    this.gl = gl;
    this.width = width;
    this.height = height;
    this.frames = frames;
    this.effects = new Prog(gl, shaders.EFFECTS);
    this.blend = new Prog(gl, shaders.BLEND);
    this.master = new Prog(gl, shaders.MASTER);
    this.events = new Prog(gl, shaders.EVENTS);
    this.overlay = new Prog(gl, shaders.OVERLAY);
    this.present = new Prog(gl, `#version 300 es
precision highp float;
uniform sampler2D tex;
in vec2 uv;
out vec4 color;
void main() { color = vec4(texture(tex, uv).rgb, 1.0); }`);
    const vao = gl.createVertexArray();
    gl.bindVertexArray(vao);
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
    gl.disable(gl.BLEND);
    gl.disable(gl.DEPTH_TEST);
    this.layer = this.target();
    this.accum = [this.target(), this.target()];
    this.mastered = this.target();
    this.struck = this.target();
    this.out = [this.target(), this.target()];
    this._out = 0;
    this.comp = [this.target(), this.target()];
    this._comp = 0;
    this.inputs = [];
    this.uploaded = [];
    this.black = this.texture(1, 1, new Uint8Array([0, 0, 0, 0]));
    this.titles = new Map();
    this.titleImages = {};       // title.image path -> ImageBitmap, set by the app
  }

  texture(w, h, data = null) {
    const gl = this.gl;
    const tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, data);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
    tex.w = w;
    tex.h = h;
    return tex;
  }

  target() {
    const gl = this.gl;
    const tex = this.texture(this.width, this.height);
    const fbo = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
    return { tex, fbo };
  }

  draw(target) {
    const gl = this.gl;
    gl.bindFramebuffer(gl.FRAMEBUFFER, target ? target.fbo : null);
    gl.viewport(0, 0, this.width, this.height);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }

  bind(unit, tex) {
    const gl = this.gl;
    gl.activeTexture(gl.TEXTURE0 + unit);
    gl.bindTexture(gl.TEXTURE_2D, tex);
  }

  upload(tex, image) {
    const gl = this.gl;
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE, image);
    tex.w = image.width;
    tex.h = image.height;
  }

  // Scale of the source's texture coordinates so it covers the output without stretching.
  crop(tex) {
    const src = tex.w / tex.h, out = this.width / this.height;
    return out < src ? [out / src, 1] : [1, src / out];
  }

  runEffects(source, target, amounts, state, flip, seed) {
    const p = this.effects.use();
    this.bind(0, source);
    p.i("tex", 0);
    p.f("crop", ...this.crop(source));
    p.f("res", this.width, this.height);
    p.f("time", state.t);
    p.f("seed", seed);
    p.i("flip", flip ? 1 : 0);
    state.palette.slice(0, 3).forEach((c, i) => p.f(`pal${i}`, ...fxlib.hexRgb(c)));
    for (const name of Object.keys(fxlib.FX)) p.f(name, amounts[name] || 0);
    this.draw(target);
  }

  // The sequencer's picture for a frame state; returns its texture.
  render(state) {
    let [a, b] = this.accum;
    const gl = this.gl;
    gl.bindFramebuffer(gl.FRAMEBUFFER, a.fbo);
    gl.viewport(0, 0, this.width, this.height);
    gl.clearColor(0, 0, 0, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
    state.lanes.forEach((lane, li) => {
      if (!lane.voice || lane.opacity <= 0) return;
      while (this.inputs.length <= li) {
        this.inputs.push(this.texture(1, 1, new Uint8Array(4)));
        this.uploaded.push(null);
      }
      const tex = this.inputs[li];
      const key = `${lane.voice.shard}:${lane.frame}`;
      if (this.uploaded[li] !== key) {
        const img = this.frames.get(lane.voice.shard, lane.frame);
        // Not decoded yet: keep showing the last frame this lane had.
        if (img) {
          this.upload(tex, img);
          this.uploaded[li] = key;
        }
      }
      this.runEffects(tex, this.layer, lane.fx, state, true, li * 17 + lane.voice.step);
      // Blend the lane over what is there so far.
      const p = this.blend.use();
      this.bind(0, a.tex);
      this.bind(1, this.layer.tex);
      p.i("base", 0);
      p.i("layer", 1);
      p.i("mode", Math.max(0, fxlib.BLEND_MODES.indexOf(lane.blend)));
      p.f("opacity", lane.opacity);
      this.draw(b);
      [a, b] = [b, a];
    });
    this.runEffects(a.tex, this.mastered, state.master, state, false, state.step);
    const prev = this.out[this._out];
    let picture = this.mastered.tex;
    if (Object.keys(state.events).length) {
      const p = this.events.use();
      this.bind(0, picture);
      this.bind(1, prev.tex);
      p.i("tex", 0);
      p.i("prev", 1);
      p.f("res", this.width, this.height);
      p.f("time", state.t);
      const hits = eventlib.NAMES.map(n => state.events[n] || [0, 0, 0]);
      ["amt", "age", "sd"].forEach((name, k) => p.fv(name, hits.map(h => h[k])));
      this.draw(this.struck);
      picture = this.struck.tex;
    }
    this._out ^= 1;
    const cur = this.out[this._out];
    const m = this.master.use();
    this.bind(0, picture);
    this.bind(1, prev.tex);
    m.i("current", 0);
    m.i("previous", 1);
    m.f("trails", state.master.trails || 0);
    m.f("flash", state.master.flash || 0);
    m.f("strobe", state.master.strobe || 0);
    const steps = (state.music.beat_phase || 0) * 4;
    m.f("step_phase", steps - Math.floor(steps));
    this.draw(cur);
    return cur.tex;
  }

  titleTexture(title, card) {
    const key = JSON.stringify([title, card]);
    let tex = this.titles.get(key);
    if (!tex) {
      if (this.titles.size > 24) {
        for (const t of this.titles.values()) this.gl.deleteTexture(t);
        this.titles.clear();
      }
      const canvas = renderTitle(title, this.width, this.height, this.titleImages[title.image] || null, card);
      tex = this.texture(1, 1);
      this.upload(tex, canvas);
      this.titles.set(key, tex);
    }
    return tex;
  }

  compPass(base, layer, alpha, fade, flipLayer) {
    this._comp ^= 1;
    const t = this.comp[this._comp];
    const p = this.overlay.use();
    this.bind(0, base);
    this.bind(1, layer);
    p.i("base", 0);
    p.i("layer", 1);
    p.f("alpha", alpha);
    p.f("fade", fade);
    p.i("flip_layer", flipLayer ? 1 : 0);
    this.draw(t);
    return t.tex;
  }

  // The finished picture at program time T onto the canvas: engine state, titles over it, the fade.
  // Returns the engine's state (null on a title card).
  frame(engine, program, T) {
    const t = program.songTime(T);
    const state = t !== null ? engine.state(t) : null;
    let base = state ? this.render(state) : this.black;
    const titles = program.titlesAt(T);
    const fade = program.fade(T);
    if (!titles.length) base = this.compPass(base, this.black, 0, fade, false);
    titles.forEach(([placed, alpha], k) => {
      const last = k === titles.length - 1;
      base = this.compPass(base, this.titleTexture(placed.title, placed.card), alpha, last ? fade : 1, true);
    });
    const p = this.present.use();
    this.bind(0, base);
    p.i("tex", 0);
    this.draw(null);
    return state;
  }

  // The frames the picture at T needs, to decode before drawing it (for export).
  needs(engine, program, T) {
    const t = program.songTime(T);
    if (t === null) return [];
    return engine.state(t).lanes.filter(l => l.voice && l.opacity > 0).map(l => [l.voice.shard, l.frame]);
  }

  dispose() {
    const ext = this.gl.getExtension("WEBGL_lose_context");
    if (ext) ext.loseContext();
  }
}
