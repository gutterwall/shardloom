"""GLSL for the effects, the lane blending and the master pass."""

VERTEX = """
#version 330
in vec2 pos;
out vec2 uv;
void main() {
    uv = pos * 0.5 + 0.5;
    gl_Position = vec4(pos, 0.0, 1.0);
}
"""

# One shader for every effect in fx.FX; each amount is 0 (off) .. 1.
EFFECTS = """
#version 330
uniform sampler2D tex;
uniform vec2 res;
uniform float time;
uniform float seed;
uniform bool flip;          // shard frames are stored top row first
uniform vec2 crop;          // the part of the source that covers the output's shape
uniform vec3 pal0, pal1, pal2;
uniform float zoom, shake, mirror, kaleido, pixelate, glitch, rgb_split;
uniform float hue, saturate, bw, posterize, invert, scanlines, colorize;
// One layer shard effects (events.layer_strikes), 0 when off:
uniform float lens;          // a single-colour lens over the layer, 0..1
uniform vec3 lens_rgb;       // its colour
uniform float fold;          // mirror / kaleidoscope, 0..1 (fades between the plain and the folded picture)
uniform float fold_style;    // 0 mirror, 1 quad, 2 kaleidoscope
uniform float fold_segments; // kaleidoscope segments
uniform float fold_zoom;     // > 0 zooms in from the middle, < 0 brings mirrored copies in from the edges
uniform float fold_turn;     // radians
uniform float fold_leaves;   // 0..1: how far each leaf turns over its own mix of red, green and blue
uniform float fold_seed;     // the leaves' colours
in vec2 uv;
out vec4 color;

float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }

vec2 wrap(vec2 p) { return 1.0 - abs(1.0 - mod(p, 2.0)); }   // mirrored repeat

vec3 fetch(vec2 p) {
    p = (wrap(p) - 0.5) * crop + 0.5;
    if (flip) p.y = 1.0 - p.y;
    return texture(tex, p).rgb;
}

vec3 hue_rotate(vec3 c, float a) {
    const mat3 toYIQ = mat3(0.299, 0.596, 0.211, 0.587, -0.274, -0.523, 0.114, -0.322, 0.312);
    const mat3 toRGB = mat3(1.0, 1.0, 1.0, 0.956, -0.272, -1.106, 0.621, -0.647, 1.703);
    vec3 yiq = toYIQ * c;
    float h = atan(yiq.z, yiq.y) + a;
    float chroma = length(yiq.yz);
    return toRGB * vec3(yiq.x, chroma * cos(h), chroma * sin(h));
}

// Where the mirror / kaleidoscope reads the picture for point p, and which leaf of it p is in.
vec2 folded(vec2 p, float aspect, out float leaf) {
    vec2 q = (p - 0.5) * vec2(aspect, 1.0);
    q = fold_zoom >= 0.0 ? q / (1.0 + 1.5 * fold_zoom) : q * (1.0 - 1.5 * fold_zoom);
    float cs = cos(fold_turn), sn = sin(fold_turn);
    q = mat2(cs, sn, -sn, cs) * q;
    if (fold_style < 0.5) {
        leaf = q.x < 0.0 ? 1.0 : 0.0;
        q.x = abs(q.x);
    } else if (fold_style < 1.5) {
        leaf = (q.x < 0.0 ? 1.0 : 0.0) + (q.y < 0.0 ? 2.0 : 0.0);
        q = abs(q);
    } else {
        float wedge = 6.2831853 / max(fold_segments, 2.0);
        float at = mod(atan(q.y, q.x), 6.2831853);
        float a = mod(at, wedge);
        leaf = floor(at / wedge) * 2.0 + (a > wedge * 0.5 ? 1.0 : 0.0);   // each segment is two mirrored leaves
        a = abs(a - wedge * 0.5);
        q = vec2(cos(a), sin(a)) * length(q);
    }
    return q / vec2(aspect, 1.0) + 0.5;
}

void main() {
    vec2 p = uv;
    float aspect = res.x / res.y;

    if (shake > 0.0) {
        float t = floor(time * 24.0);
        p += (vec2(hash(vec2(t, seed)), hash(vec2(seed, t))) - 0.5) * 0.08 * shake;
    }
    if (zoom > 0.0) p = (p - 0.5) / (1.0 + 1.5 * zoom) + 0.5;
    if (mirror > 0.25) p.x = p.x > 0.5 ? 1.0 - p.x : p.x;
    if (kaleido > 0.0) {
        float segments = 2.0 + floor(kaleido * 10.0);
        vec2 c = (p - 0.5) * vec2(aspect, 1.0);
        float r = length(c);
        float a = atan(c.y, c.x);
        float wedge = 6.2831853 / segments;
        a = mod(a, wedge);
        a = abs(a - wedge * 0.5);
        p = vec2(cos(a), sin(a)) * r / vec2(aspect, 1.0) + 0.5;
    }
    if (pixelate > 0.0) {
        vec2 cells = mix(res, vec2(12.0 * aspect, 12.0), pow(pixelate, 0.5));
        p = (floor(p * cells) + 0.5) / cells;
    }
    if (glitch > 0.0) {
        float band = floor(p.y * 24.0);
        float t = floor(time * 12.0);
        float r = hash(vec2(band, t + seed));
        if (r < glitch * 0.7) p.x += (hash(vec2(t, band + seed)) - 0.5) * 0.4 * glitch;
        float block = hash(floor(p * vec2(8.0, 6.0)) + t);
        if (block < glitch * 0.25) p = p + vec2(0.1, 0.0) * (block - 0.1);
    }
    if (scanlines > 0.0) p.x += sin(p.y * 240.0 + time * 18.0) * 0.003 * scanlines;

    vec3 c;
    if (rgb_split > 0.0) {
        vec2 off = vec2(0.025 * rgb_split, 0.004 * rgb_split);
        c = vec3(fetch(p + off).r, fetch(p).g, fetch(p - off).b);
    } else {
        c = fetch(p);
    }
    if (fold > 0.0) {
        float leaf;
        vec3 f = fetch(folded(p, aspect, leaf));
        if (fold_leaves > 0.0) {
            // Each leaf turns its channels over by its own amounts: its own colour.
            vec3 w = vec3(hash(vec2(leaf, fold_seed * 97.0)), hash(vec2(leaf + 31.0, fold_seed * 97.0)),
                          hash(vec2(leaf + 67.0, fold_seed * 97.0)));
            f = mix(f, 1.0 - f, w * fold_leaves);
        }
        c = mix(c, f, fold);
    }

    if (hue > 0.0) c = hue_rotate(c, hue * 6.2831853);
    float lum = dot(c, vec3(0.299, 0.587, 0.114));
    if (saturate > 0.0) c = mix(vec3(lum), c, 1.0 + 2.0 * saturate);
    if (bw > 0.0) {
        float hard = smoothstep(0.25, 0.75, lum);
        c = mix(c, vec3(mix(lum, hard, 0.6)), bw);
    }
    if (posterize > 0.0) {
        float levels = mix(24.0, 2.0, posterize);
        c = floor(c * levels + 0.5) / levels;
    }
    if (invert > 0.0) c = mix(c, 1.0 - c, invert);
    if (scanlines > 0.0) {
        float line = 0.5 + 0.5 * sin(uv.y * res.y * 3.14159);
        c *= 1.0 - scanlines * 0.35 * line;
        c += (hash(uv * res + time) - 0.5) * 0.12 * scanlines;
    }
    if (colorize > 0.0) {
        float l = clamp(dot(c, vec3(0.299, 0.587, 0.114)), 0.0, 1.0);
        vec3 g = l < 0.5 ? mix(pal0, pal1, l * 2.0) : mix(pal1, pal2, l * 2.0 - 1.0);
        c = mix(c, g, colorize);
    }
    if (lens > 0.0) {
        // The picture's light through a coloured glass: its brightness in the lens colour.
        float l = dot(c, vec3(0.299, 0.587, 0.114));
        vec3 through = l * lens_rgb / max(dot(lens_rgb, vec3(0.299, 0.587, 0.114)), 0.3);
        c = mix(c, through, lens);
    }
    color = vec4(clamp(c, 0.0, 1.0), 1.0);
}
"""

BLEND = """
#version 330
uniform sampler2D base;
uniform sampler2D layer;
uniform int mode;          // index into fx.BLEND_MODES; 5 and up for the visuals (visuals.BLENDS)
uniform float opacity;
in vec2 uv;
out vec4 color;
void main() {
    vec3 a = texture(base, uv).rgb;
    vec3 b = texture(layer, uv).rgb;
    vec3 m;
    if (mode == 1) m = a + b;
    else if (mode == 2) m = 1.0 - (1.0 - a) * (1.0 - b);
    else if (mode == 3) m = a * b;
    else if (mode == 4) m = abs(a - b);
    // 5 and up: how the visuals (visuals.BLENDS) mix in.
    else if (mode == 5) m = a - b;                                                  // minus
    else if (mode == 6) m = mix(2.0 * a * b, 1.0 - 2.0 * (1.0 - a) * (1.0 - b), step(0.5, a));   // overlay
    else if (mode == 7) m = mix(a, b, smoothstep(0.04, 0.3, max(b.r, max(b.g, b.b))));          // occlude
    else if (mode == 8) m = mix(b, a, smoothstep(0.03, 0.25, dot(a, vec3(0.299, 0.587, 0.114))));  // behind
    else if (mode == 9) m = a * clamp(b * 2.0 + 0.15, 0.0, 1.0);                    // multiply, lit where it's drawn
    else m = b;
    color = vec4(clamp(mix(a, m, opacity), 0.0, 1.0), 1.0);
}
"""

MASTER = """
#version 330
uniform sampler2D current;
uniform sampler2D previous;
uniform float trails, flash, strobe, step_phase;
in vec2 uv;
out vec4 color;
void main() {
    vec3 c = texture(current, uv).rgb;
    if (trails > 0.0) {
        vec3 prev = texture(previous, (uv - 0.5) * 0.985 + 0.5).rgb;
        c = max(c, prev * (0.55 + 0.42 * trails));
    }
    c += flash * 0.6;
    if (strobe > 0.0 && step_phase < 0.5) c *= 1.0 - strobe;
    color = vec4(clamp(c, 0.0, 1.0), 1.0);
}
"""

# A title (straight alpha) over the picture, then the fade to black.
OVERLAY = """
#version 330
uniform sampler2D base;
uniform sampler2D layer;
uniform float alpha;
uniform float fade;
uniform bool flip_layer;    // title pictures are stored top row first
in vec2 uv;
out vec4 color;
void main() {
    vec3 c = texture(base, uv).rgb;
    vec4 t = texture(layer, flip_layer ? vec2(uv.x, 1.0 - uv.y) : uv);
    c = mix(c, t.rgb, t.a * alpha);
    color = vec4(c * fade, 1.0);
}
"""

# The shard effects (events.EVENTS), on the whole picture after the lanes are mixed.
# For effect i: amt[i] how hard it hits now (0 = off; the Colour ones -1..1), age[i] 0..1 through the strike,
# sd[i] its random seed.
EVENTS = """
#version 330
uniform sampler2D tex;       // the picture so far
uniform sampler2D prev;      // the last finished frame, for datamosh
uniform vec2 res;
uniform float time;
uniform float amt[18];
uniform float age[18];
uniform float sd[18];
in vec2 uv;
out vec4 color;

const int DATAMOSH = 0, PIXEL_SORT = 1, CORRUPT = 2, TEAR = 3, VHS = 4, CRT = 5, ROLL = 6, BLEED = 7,
          BURN = 8, LEAK = 9, DAMAGE = 10, FIREWORKS = 11, EXPLOSION = 12, HUE = 13, BRIGHT = 14, SAT = 15,
          FLIP = 16, ERASE = 17;

float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float hash1(float x) { return fract(sin(x * 91.3458 + 3.17) * 47453.5453); }

float noise(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), f.x), mix(hash(i + vec2(0.0, 1.0)), hash(i + 1.0), f.x), f.y);
}

float fbm(vec2 p) {
    float v = 0.0, a = 0.5;
    for (int i = 0; i < 5; i++) { v += a * noise(p); p *= 2.03; a *= 0.5; }
    return v;
}

vec3 src(vec2 p) { return texture(tex, clamp(p, 0.0, 1.0)).rgb; }
float luma(vec3 c) { return dot(c, vec3(0.299, 0.587, 0.114)); }
vec3 screen(vec3 a, vec3 b) { return 1.0 - (1.0 - a) * (1.0 - b); }

// Hot colours from black through red, orange and yellow to white (h 0..1).
vec3 fire(float h) {
    h = clamp(h, 0.0, 1.0);
    return vec3(smoothstep(0.0, 0.35, h), smoothstep(0.25, 0.7, h), smoothstep(0.6, 1.0, h));
}

vec3 hue_rotate(vec3 c, float a) {
    const mat3 toYIQ = mat3(0.299, 0.596, 0.211, 0.587, -0.274, -0.523, 0.114, -0.322, 0.312);
    const mat3 toRGB = mat3(1.0, 1.0, 1.0, 0.956, -0.272, -1.106, 0.621, -0.647, 1.703);
    vec3 yiq = toYIQ * c;
    float h = atan(yiq.z, yiq.y) + a;
    float chroma = length(yiq.yz);
    return toRGB * vec3(yiq.x, chroma * cos(h), chroma * sin(h));
}

vec3 hsv(float h, float s, float v) {
    vec3 k = clamp(abs(mod(h * 6.0 + vec3(0.0, 4.0, 2.0), 6.0) - 3.0) - 1.0, 0.0, 1.0);
    return v * mix(vec3(1.0), k, s);
}

float segment(vec2 p, vec2 a, vec2 b) {
    vec2 pa = p - a, ba = b - a;
    float h = clamp(dot(pa, ba) / max(dot(ba, ba), 1e-8), 0.0, 1.0);
    return length(pa - ba * h);
}

// Sparks flying out from centre c (in aspect space) life 0..1 after the burst; returns light.
vec3 sparks(vec2 q, vec2 c, float life, float seed, vec3 tint, float speed, int n) {
    vec3 light = vec3(0.0);
    if (life <= 0.0 || life >= 1.0) return light;
    float fade = pow(1.0 - life, 1.6);
    float travel = 1.0 - pow(1.0 - life, 3.0);              // fast out, slowing down
    for (int k = 0; k < 48; k++) {
        if (k >= n) break;
        float fk = float(k);
        float ang = (fk + hash1(fk + seed * 13.0) * 0.8) / float(n) * 6.2831853;
        float v = speed * (0.55 + 0.45 * hash1(fk * 1.7 + seed * 31.0));
        vec2 dir = vec2(cos(ang), sin(ang));
        vec2 fall = vec2(0.0, -0.22 * life * life);
        vec2 head = c + dir * v * travel + fall;
        float back = max(0.0, travel - 0.22);
        vec2 tail = c + dir * v * back + vec2(0.0, -0.22 * max(0.0, life - 0.06) * max(0.0, life - 0.06));
        float d = segment(q, tail, head);
        float twinkle = 0.6 + 0.4 * sin(time * 40.0 + fk * 7.0);
        light += tint * fade * twinkle * (0.0009 / (d * d + 0.00025)) * 0.06;
        light += vec3(1.0) * fade * 0.00004 / (length(q - head) * length(q - head) + 0.00002);
    }
    return light;
}

void main() {
    vec2 p = uv;
    float aspect = res.x / res.y;
    vec2 asp = vec2(aspect, 1.0);
    vec2 px = 1.0 / res;

    // ---- moving the picture ----
    float a;
    a = amt[EXPLOSION];
    vec2 boom = vec2(0.2 + 0.6 * hash1(sd[EXPLOSION] * 17.0), 0.3 + 0.4 * hash1(sd[EXPLOSION] * 29.0)) * asp;
    if (a > 0.0) {
        float g = age[EXPLOSION];
        float t = floor(time * 30.0);
        p += (vec2(hash(vec2(t, 1.3)), hash(vec2(2.1, t))) - 0.5) * 0.05 * a * pow(1.0 - g, 2.0);
        vec2 q = p * asp - boom;
        float r = length(q);
        float ring = exp(-pow((r - g * 1.4) / 0.05, 2.0));
        p -= normalize(q + 1e-5) / asp * ring * 0.04 * a;
    }
    a = amt[DAMAGE];
    if (a > 0.0) {
        float t = floor(time * 24.0);
        p += (vec2(hash(vec2(t, 4.0)), hash(vec2(5.0, t))) - 0.5) * vec2(0.004, 0.008) * a;
    }
    a = amt[ROLL];
    float seam = 1.0;
    if (a > 0.0) {
        float shift = a * (0.25 + 0.6 * sd[ROLL]) * (0.4 + age[ROLL]);
        float y = p.y + shift;
        p.y = fract(y);
        seam = smoothstep(0.0, 0.035, fract(y)) * smoothstep(1.0, 0.985, fract(y));
        p.x += sin(y * 40.0) * 0.004 * a;
    }
    a = amt[TEAR];
    float torn = 0.0;
    if (a > 0.0) {
        float bands = 5.0 + floor(hash1(sd[TEAR] * 7.0) * 20.0);
        float band = floor(p.y * bands);
        float t = floor(age[TEAR] * 6.0);
        if (hash(vec2(band, sd[TEAR] * 50.0 + t)) < 0.25 + 0.5 * a) {
            torn = (hash(vec2(t + sd[TEAR], band)) - 0.5) * 0.5 * a;
            p.x = fract(p.x + torn);
        }
    }
    a = amt[VHS];
    float band_in = 0.0;
    if (a > 0.0) {
        float y0 = fract(sd[VHS] + age[VHS] * 0.9);
        float h = 0.05 + 0.12 * a;
        band_in = smoothstep(h, 0.0, abs(p.y - y0));
        p.x += (noise(vec2(p.y * 80.0, time * 20.0)) - 0.5) * 0.08 * a * band_in;
        p.x += (hash(vec2(floor(p.y * res.y * 0.5), floor(time * 30.0))) - 0.5) * 0.003 * a;
    }
    a = amt[CRT];
    bool outside = false;
    if (a > 0.0) {
        vec2 q = p * 2.0 - 1.0;
        q *= 1.0 + 0.18 * a * dot(q, q);
        p = q * 0.5 + 0.5;
        outside = any(lessThan(p, vec2(0.0))) || any(greaterThan(p, vec2(1.0)));
    }

    // ---- reading the picture ----
    vec3 c = src(p);
    a = amt[DATAMOSH];
    if (a > 0.0) {
        float size = 12.0 + 28.0 * hash1(sd[DATAMOSH] * 3.0);
        vec2 blk = floor(p * res / size);
        float h = hash(blk + sd[DATAMOSH] * 91.0);
        if (h < 0.65 * a) {
            vec2 mv = (vec2(hash(blk * 1.3 + 2.0), hash(blk * 0.7 + 9.0)) - 0.5) * 0.04 * a;
            mv += (vec2(hash1(sd[DATAMOSH] * 5.0), hash1(sd[DATAMOSH] * 8.0)) - 0.5) * 0.03;
            c = mix(c, texture(prev, clamp(p + mv, 0.0, 1.0)).rgb, min(1.0, a * 1.3));
        }
    }
    a = amt[PIXEL_SORT];
    if (a > 0.0) {
        bool down = hash1(sd[PIXEL_SORT] * 11.0) < 0.5;
        vec2 dir = down ? vec2(0.0, 1.0) : vec2(-1.0, 0.0);
        float col = down ? floor(p.x * res.x / 3.0) : floor(p.y * res.y / 3.0);
        if (hash(vec2(col, sd[PIXEL_SORT] * 37.0)) < 0.35 + 0.6 * a) {
            float thresh = 0.8 - 0.45 * a;
            float reach = (0.05 + 0.35 * a) * (0.5 + hash(vec2(col, 3.0)));
            vec3 best = c;
            float bl = luma(c);
            if (bl > thresh * 0.4) {
                for (int k = 1; k <= 24; k++) {
                    vec3 s = src(p + dir * reach * float(k) / 24.0);
                    float l = luma(s);
                    if (l < thresh * 0.4) break;
                    if (l > bl) { bl = l; best = s; }
                }
                c = mix(c, best, smoothstep(thresh * 0.4, thresh, bl));
            }
        }
    }
    a = amt[BLEED];
    if (a > 0.0) {
        vec3 sum = vec3(0.0);
        for (int k = 0; k < 8; k++) sum += src(p - vec2(float(k) * 0.008 * a, 0.0));
        sum /= 8.0;
        c = mix(c, sum + (luma(c) - luma(sum)) + vec3(0.06, -0.03, 0.05), a);           // smeared colour, sharp brightness
        c = mix(c, screen(c, src(p - vec2(0.03 * a, 0.0)) * 0.6), 0.5 * a);
        c.r = mix(c.r, src(p + vec2(0.008 * a, 0.0)).r, a);
    }
    if (torn != 0.0) c.r = mix(c.r, src(p + vec2(0.02 * sign(torn), 0.0)).r, amt[TEAR]);
    a = amt[CORRUPT];
    if (a > 0.0) {
        float size = 8.0 * (1.0 + floor(hash1(sd[CORRUPT] * 5.0) * 4.0));
        vec2 blk = floor(p * res / size);
        float t = floor(age[CORRUPT] * 5.0);
        float h = hash(blk + vec2(sd[CORRUPT] * 71.0, t));
        if (h < 0.3 * a) {
            float kind = hash(blk * 3.1 + t);
            if (kind < 0.3) c = c.gbr;
            else if (kind < 0.55) c = floor(c * 3.0) / 3.0 + vec3(0.0, 0.25, 0.0);
            else if (kind < 0.75) c = hsv(hash(blk), 0.9, 0.9);
            else c = src((blk + vec2(floor(hash(blk + 1.0) * 6.0) - 3.0, 0.0)) * size / res + fract(p * res / size) * size / res);
        }
    }

    // ---- colour, following the music ----
    a = amt[HUE];
    if (a != 0.0) c = hue_rotate(c, a * 3.14159 * (sd[HUE] < 0.5 ? -1.0 : 1.0));   // up to half way round
    a = amt[SAT];
    if (a != 0.0) c = mix(vec3(luma(c)), c, max(0.0, 1.0 + 1.5 * a));
    a = amt[BRIGHT];
    if (a != 0.0) c = a > 0.0 ? screen(c * (1.0 + 0.6 * a), c * 0.5 * a) : c * (1.0 + 0.6 * a);

    // ---- light and dirt ----
    a = amt[VHS];
    if (a > 0.0) {
        float snow = hash(uv * res + floor(time * 30.0));
        c = mix(c, vec3(snow), band_in * 0.6 * a);
        c = mix(c, vec3(luma(c)), 0.35 * a);
        c += (hash(uv * res * 0.5 + time) - 0.5) * 0.08 * a;
    }
    a = amt[ERASE];
    if (a > 0.0) {
        // Worn, half-erased tape: blocky snow eats in from the edges along a ragged front that wanders, and fills
        // the dark parts; blocks of snow scatter just inside the front, and dropouts streak across in white.
        // Sizes go by the picture's height, so a render looks like the preview at any size.
        float t = floor(time * 30.0);
        float s = sd[ERASE] * 100.0;
        vec2 e = min(uv, 1.0 - uv) * asp;
        float edge = min(e.x, e.y);
        float front = (0.04 + 0.3 * a) * (0.45 + 1.1 * fbm(uv * vec2(3.0, 4.0) + vec2(s, time * 0.6)));
        float eaten = smoothstep(front, front * 0.3, edge);
        float dark = smoothstep(0.08 + 0.3 * a, 0.0, luma(c)) * a;
        float cellh = max(1.0, res.y / (110.0 + 90.0 * hash1(s)));            // tape snow: blocks wider than tall
        vec2 blk = floor(uv * res / vec2(cellh * 2.5, cellh));
        float n = hash(blk + vec2(t * 13.1, s));
        vec3 snow = vec3(n) * vec3(0.92 + 0.16 * hash(blk + t + 1.7), 1.0, 0.92 + 0.16 * hash(blk + t + 3.1));
        float m = clamp(max(eaten, dark * 0.85), 0.0, 1.0) * (0.55 + 0.45 * a);
        m = max(m, step(hash(floor(blk / 2.0) + vec2(t * 7.0, s)), 0.18 * a) * smoothstep(front * 1.7, front, edge));
        c = mix(c, snow, m);
        float row = floor(uv.y * res.y / max(1.0, res.y / 240.0));
        if (hash(vec2(row, t + s)) < 0.01 * a) {
            float x0 = hash(vec2(row + 5.0, t)) * 1.1 - 0.1;
            float len = 0.03 + 0.25 * hash(vec2(t, row + 9.0));
            c = mix(c, vec3(0.96), 0.85 * step(x0, uv.x) * step(uv.x, x0 + len));
        }
        c = mix(c, vec3(luma(c)), 0.3 * a) * (1.0 - 0.1 * a) + 0.05 * a;    // faded: less colour, lifted blacks
    }
    a = amt[CRT];
    if (a > 0.0) {
        float line = 0.5 + 0.5 * cos(uv.y * res.y * 3.14159);
        c *= 1.0 - 0.45 * a * line;
        int sub = int(mod(gl_FragCoord.x, 3.0));
        vec3 mask = sub == 0 ? vec3(1.0, 0.7, 0.7) : sub == 1 ? vec3(0.7, 1.0, 0.7) : vec3(0.7, 0.7, 1.0);
        c *= mix(vec3(1.0), mask * 1.25, a);
        vec2 q = uv * 2.0 - 1.0;
        c *= 1.0 - 0.5 * a * dot(q * q, q * q);
        c += 0.08 * a * sin(uv.y * 3.0 - time * 2.0) ;
        if (outside) c = vec3(0.0);
    }
    c *= seam;
    a = amt[DAMAGE];
    if (a > 0.0) {
        float t = floor(time * 24.0);
        c *= 1.0 + (hash(vec2(t, 9.0)) - 0.5) * 0.35 * a;
        c = mix(c, vec3(luma(c)) * vec3(1.07, 0.98, 0.82), 0.4 * a);
        for (int k = 0; k < 3; k++) {
            float fk = float(k);
            if (hash(vec2(t * 0.25 + fk, sd[DAMAGE])) < 0.6) {
                float x = hash(vec2(floor(t / 3.0) + fk * 7.0, sd[DAMAGE] * 13.0)) + sin(uv.y * 9.0 + fk) * 0.004;
                float line = exp(-pow((uv.x - x) * res.x / 1.2, 2.0));
                c = mix(c, vec3(k == 0 ? 0.08 : 0.9), line * 0.7 * a);
            }
        }
        vec2 cell = floor(uv * res / 3.0);
        float dust = hash(cell + t * 1.7);
        if (dust > 1.0 - 0.004 * a) c *= 0.15;
        float hair = noise(uv * vec2(6.0, 3.0) + t);
        c *= 1.0 - 0.6 * a * smoothstep(0.012, 0.0, abs(hair - 0.5)) * step(0.8, hash(vec2(t, 2.0)));
    }
    a = amt[LEAK];
    if (a > 0.0) {
        float side = hash1(sd[LEAK] * 3.0);
        vec2 from = side < 0.5 ? vec2(side < 0.25 ? -0.05 : 1.05, 0.3 + 0.5 * sd[LEAK]) : vec2(0.2 + 0.6 * sd[LEAK], side < 0.75 ? -0.05 : 1.05);
        vec2 q = (uv - from - vec2(0.15, 0.05) * age[LEAK]) * asp;
        float glow = exp(-dot(q, q) / (0.12 + 0.25 * age[LEAK]));
        glow *= 0.8 + 0.4 * noise(uv * 3.0 + time * 0.7);
        vec3 tint = mix(vec3(1.0, 0.35, 0.08), vec3(1.0, 0.75, 0.4), noise(uv * 2.0 + sd[LEAK] * 9.0));
        if (hash1(sd[LEAK] * 19.0) < 0.3) tint = tint.rbg * vec3(1.0, 0.6, 1.0);
        c = screen(c, tint * glow * a * 1.8);
    }
    a = amt[BURN];
    if (a > 0.0) {
        float g = age[BURN];
        vec2 start = vec2(0.2 + 0.6 * hash1(sd[BURN] * 23.0), 0.2 + 0.6 * hash1(sd[BURN] * 41.0)) * asp;
        vec2 q = uv * asp - start;
        float d = length(q) + (fbm(q * 5.0 + sd[BURN] * 40.0) - 0.5) * 0.45 + (fbm(q * 18.0 - time * 0.5) - 0.5) * 0.06;
        float r = pow(g, 1.6) * (0.9 + 0.6 * aspect);
        float e = d - r;
        vec3 burnt = c;
        burnt = mix(burnt, burnt * vec3(0.55, 0.32, 0.15), smoothstep(0.18, 0.02, e));          // scorched film
        burnt = mix(burnt, vec3(0.05, 0.02, 0.0), smoothstep(0.05, 0.0, e));                     // charred
        burnt += fire(1.0 - abs(e) / 0.035) * 2.0 * step(-0.02, e);                               // the burning edge
        vec3 hole = vec3(1.0, 0.96, 0.88) * (0.9 + 0.1 * hash(vec2(floor(time * 24.0), 1.0)));  // projector light
        burnt = mix(burnt, hole, smoothstep(0.0, -0.03, e));
        c = mix(c, burnt, min(1.0, a * 1.4));
    }
    a = amt[FIREWORKS];
    if (a > 0.0) {
        vec2 q = uv * asp;
        vec3 light = vec3(0.0);
        for (int b = 0; b < 3; b++) {
            float fb = float(b);
            float s = sd[FIREWORKS] * 10.0 + fb * 3.7;
            float born = fb * 0.17 * hash1(s);
            float life = (age[FIREWORKS] - born) / 0.75;
            vec2 at = vec2(0.15 + 0.7 * hash1(s * 1.3), 0.5 + 0.35 * hash1(s * 2.9)) * asp;
            vec3 tint = hsv(hash1(s * 4.1), 0.75, 1.0);
            float lift = clamp((age[FIREWORKS] - born + 0.12) / 0.12, 0.0, 1.0);
            if (life < 0.0 && lift > 0.0) {                                                   // the rocket going up
                vec2 rocket = mix(vec2(at.x, -0.05), at, lift);
                light += vec3(1.0, 0.8, 0.5) * 0.00008 / (dot(q - rocket, q - rocket) + 0.00003);
            }
            light += sparks(q, at, life, s, tint, 0.36 + 0.2 * hash1(s * 5.3), 44) * 2.2;
            light += tint * exp(-dot(q - at, q - at) / 0.004) * max(0.0, 1.0 - life * 6.0) * step(0.0, life);
        }
        c = screen(c, min(light * a, vec3(4.0)));
    }
    a = amt[EXPLOSION];
    if (a > 0.0) {
        float g = age[EXPLOSION];
        vec2 q = uv * asp - boom;
        float r = 0.04 + 0.42 * sqrt(g);
        float n = fbm(q * 7.0 / (0.5 + r) - vec2(0.0, time * 1.5) + sd[EXPLOSION] * 30.0);
        float d = length(q * vec2(1.0, 0.85)) / r + (n - 0.5) * 0.9;
        float body = smoothstep(1.0, 0.55, d);
        float heat = clamp((1.0 - d) * 1.6 + n * 0.5 - g * 1.3, 0.0, 1.0);
        vec3 ball = mix(vec3(0.08, 0.07, 0.07) * (0.5 + n), fire(heat) * 1.8, smoothstep(0.0, 0.25, heat));
        c = mix(c, ball, body * min(1.0, a * 1.5));
        float ringd = length(q) - g * 1.4;
        c += vec3(1.0, 0.75, 0.5) * exp(-pow(ringd / 0.015, 2.0)) * (1.0 - g) * 0.6 * a;
        c += sparks(q + boom, boom, g * 1.2, sd[EXPLOSION] * 7.0, vec3(1.0, 0.55, 0.15), 0.45, 32) * a;
        c += vec3(1.0, 0.9, 0.75) * max(0.0, 1.0 - g * 9.0) * 0.9 * a;                     // the flash
    }
    a = amt[FLIP];
    if (a > 0.0) {
        // One or more channels turned over (events.flip_channels).
        int m = 1 + min(6, int(sd[FLIP] * 7.0));
        vec3 on = vec3(float(m & 1), float((m >> 1) & 1), float((m >> 2) & 1));
        c = mix(c, mix(c, 1.0 - clamp(c, 0.0, 1.0), on), a);
    }
    color = vec4(clamp(c, 0.0, 1.0), 1.0);
}
"""

# The MilkDrop-style visuals (visuals.py). One step of a visual's feedback loop: the last picture warped and
# dimmed, with a new wave and shapes drawn over it. pre[] holds the preset's numbers (visuals.PARAMS order).
VISUAL_WARP = """
#version 330
uniform sampler2D prev;     // the visual's last step
uniform bool first;         // the first step: nothing before it
uniform vec2 res;           // the feedback picture's size
uniform float tau;          // seconds since the strike
uniform float vseed;        // the strike's seed
uniform vec4 music;         // bass, mid, high, onset (0..1)
uniform float beat;         // 1 on the beat, falling away
uniform float pre[24];
in vec2 uv;
out vec4 color;

const int DECAY = 0, ZOOM = 1, ZOOM_BASS = 2, ROT = 3, ROT_MID = 4, WARP = 5, WARP_SPEED = 6, DX = 7, DY = 8,
          WANDER = 9, WAVE = 10, WAVE_SIZE = 11, WAVE_WIDTH = 12, HUE = 13, HUE_SPEED = 14, SAT = 15, SIDES = 16,
          SHAPE_SIZE = 17, SHAPE_SPIN = 18, SHAPES = 19;

vec3 hsv(float h, float s, float v) {
    vec3 k = clamp(abs(mod(h * 6.0 + vec3(0.0, 4.0, 2.0), 6.0) - 3.0) - 1.0, 0.0, 1.0);
    return v * mix(vec3(1.0), k, s);
}

vec2 wrap(vec2 p) { return 1.0 - abs(1.0 - mod(p, 2.0)); }   // mirrored repeat, like MilkDrop's wrapped edges

float segment(vec2 p, vec2 a, vec2 b) {
    vec2 pa = p - a, ba = b - a;
    float h = clamp(dot(pa, ba) / max(dot(ba, ba), 1e-8), 0.0, 1.0);
    return length(pa - ba * h);
}

// Distance from q to the outline of a regular polygon with n sides and radius r, turned by a.
float polygon(vec2 q, float n, float r, float a) {
    float at = atan(q.y, q.x) + a;
    float wedge = 6.2831853 / n;
    float k = mod(at, wedge) - wedge * 0.5;
    float edge = r * cos(wedge * 0.5) / cos(k);
    return abs(length(q) - edge);
}

void main() {
    float aspect = res.x / res.y;
    vec2 asp = vec2(aspect, 1.0);
    float bass = music.x, mid = music.y, high = music.z, onset = music.w;

    // ---- the last picture, moved ----
    vec2 c = 0.5 + pre[WANDER] * 0.08 * vec2(sin(tau * 0.37 + vseed * 6.2831853), cos(tau * 0.29 + vseed * 3.1));
    vec2 q = (uv - c) * asp;
    float rad = length(q);
    float zoom = max(0.5, pre[ZOOM] + pre[ZOOM_BASS] * (bass - 0.5));
    q /= pow(zoom, 1.0 + 0.6 * rad);                     // further at the edges, as MilkDrop's zoom exponent
    float rot = pre[ROT] + pre[ROT_MID] * (mid - 0.5);
    float cs = cos(rot), sn = sin(rot);
    q = mat2(cs, sn, -sn, cs) * q;
    float w = pre[WARP] * 0.012 * (0.6 + 0.8 * high);
    q += w * vec2(sin(tau * pre[WARP_SPEED] + q.y * 7.0 + vseed * 20.0),
                  cos(tau * pre[WARP_SPEED] * 1.13 + q.x * 6.0 + vseed * 9.0));
    q -= vec2(pre[DX], pre[DY]) * 0.01;
    vec3 old = first ? vec3(0.0) : texture(prev, wrap(q / asp + c)).rgb * pre[DECAY];

    // ---- the wave ----
    vec2 p = (uv - 0.5) * asp;
    float r = length(p);
    float a = atan(p.y, p.x);
    float size = pre[WAVE_SIZE];
    float width = pre[WAVE_WIDTH] * 0.006 * (1.0 + 1.5 * onset);
    int mode = int(pre[WAVE] + 0.5);
    float d = 10.0;
    if (mode == 0) {                                     // circle
        float R = size * (0.2 + 0.12 * bass + 0.04 * (sin(a * 5.0 + tau * 2.0) * mid + sin(a * 11.0 - tau * 3.1) * high
                                                    + sin(a * 3.0 + tau * 1.3) * onset));
        d = abs(r - R);
    } else if (mode == 1) {                              // line across
        float y = size * 0.16 * (sin(p.x * 9.0 + tau * 2.7) * (0.3 + mid) + 0.6 * sin(p.x * 23.0 - tau * 4.3) * high
                                 + 0.5 * sin(p.x * 4.0 + tau) * bass);
        d = abs(p.y - y);
    } else if (mode == 2) {                              // spiral arms
        float v = sin(log(r + 1e-3) * 7.0 - a * 3.0 + tau * 2.0 + bass * 3.0);
        d = abs(v) * r * 0.12 / size + (1.0 - smoothstep(0.03, 0.12, r)) * 0.05 + smoothstep(0.45, 0.8, r / size) * 0.05;
    } else if (mode == 3) {                              // scope: a Lissajous figure
        vec2 last = vec2(0.0);
        for (int k = 0; k <= 64; k++) {
            float s = float(k) / 64.0 * 6.2831853;
            vec2 pt = vec2(sin(s * 3.0 + tau * 0.9 + mid * 2.0), sin(s * 4.0 + tau * 1.1)) * size * 0.3
                      * (0.7 + 0.5 * bass) * vec2(1.2, 1.0);
            if (k > 0) d = min(d, segment(p, last, pt));
            last = pt;
        }
    } else if (mode == 4) {                              // rays from the middle
        float n = 12.0;
        float wedge = 6.2831853 / n;
        float k = mod(a + tau * 0.3, wedge) - wedge * 0.5;
        float reach = size * (0.12 + 0.35 * max(bass, onset));
        d = abs(k) * r + max(0.0, r - reach) + max(0.0, 0.03 - r);
    } else {                                             // two waves, one above and one below
        float y = size * 0.08 * (sin(p.x * 7.0 + tau * 1.9) * (0.4 + bass) + 0.5 * sin(p.x * 19.0 - tau * 3.3) * high);
        d = min(abs(p.y - 0.18 - y), abs(p.y + 0.18 + y));
    }
    float hue = pre[HUE] + tau * pre[HUE_SPEED] + r * 0.25;
    vec3 ink = hsv(fract(hue), pre[SAT], 1.0) * (0.55 + 0.6 * max(mid, beat));
    vec3 c2 = old + ink * exp(-(d * d) / (width * width));

    // ---- shapes ----
    if (pre[SIDES] >= 3.0) {
        int n = int(pre[SHAPES] + 0.5);
        for (int i = 0; i < 8; i++) {
            if (i >= n) break;
            float fi = float(i);
            float at = fi / float(n) * 6.2831853 + tau * pre[SHAPE_SPIN];
            vec2 at2 = n > 1 ? vec2(cos(at), sin(at)) * 0.28 : vec2(0.0);
            float sr = pre[SHAPE_SIZE] * (0.06 + 0.07 * bass + 0.04 * beat);
            float e = polygon(p - at2, pre[SIDES], sr, -tau * pre[SHAPE_SPIN] * 2.0 + fi);
            c2 += hsv(fract(hue + 0.5 + fi * 0.09), pre[SAT], 1.0) * 0.8 * exp(-(e * e) / (0.004 * 0.004));
        }
    }
    color = vec4(clamp(c2, 0.0, 1.0), 1.0);
}
"""

# A visual's feedback picture made ready to mix in, at the output's size: folded, echoed, brightened, solarized.
VISUAL_COMP = """
#version 330
uniform sampler2D feed;
uniform vec2 res;           // the output's size
uniform float tau;
uniform float pre[24];
in vec2 uv;
out vec4 color;

const int ECHO = 20, KALEIDO = 21, GAMMA = 22, SOLARIZE = 23;

void main() {
    vec2 p = uv;
    float aspect = res.x / res.y;
    if (pre[KALEIDO] >= 2.0) {
        vec2 q = (p - 0.5) * vec2(aspect, 1.0);
        float wedge = 6.2831853 / pre[KALEIDO];
        float a = mod(atan(q.y, q.x) + tau * 0.1, wedge);
        a = abs(a - wedge * 0.5);
        p = vec2(cos(a), sin(a)) * length(q) / vec2(aspect, 1.0) + 0.5;
    }
    vec3 c = texture(feed, clamp(p, 0.0, 1.0)).rgb;
    if (pre[ECHO] > 0.0) {
        vec2 e = (vec2(1.0 - p.x, p.y) - 0.5) / (1.0 + 0.35 * pre[ECHO]) + 0.5;
        c = max(c, texture(feed, e).rgb * 0.7 * pre[ECHO]);
    }
    if (pre[GAMMA] > 0.0) c = pow(c, vec3(1.0 / (1.0 + 1.5 * pre[GAMMA])));
    if (pre[SOLARIZE] > 0.0) c = mix(c, abs(c - 0.5) * 2.0, pre[SOLARIZE] * smoothstep(0.3, 0.7, max(c.r, max(c.g, c.b))));
    color = vec4(clamp(c, 0.0, 1.0), 1.0);
}
"""
