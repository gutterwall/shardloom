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
    color = vec4(clamp(c, 0.0, 1.0), 1.0);
}
"""

BLEND = """
#version 330
uniform sampler2D base;
uniform sampler2D layer;
uniform int mode;          // index into fx.BLEND_MODES
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
# For effect i: amt[i] how hard it hits now (0 = off), age[i] 0..1 through the strike, sd[i] its random seed.
EVENTS = """
#version 330
uniform sampler2D tex;       // the picture so far
uniform sampler2D prev;      // the last finished frame, for datamosh
uniform vec2 res;
uniform float time;
uniform float amt[13];
uniform float age[13];
uniform float sd[13];
in vec2 uv;
out vec4 color;

const int DATAMOSH = 0, PIXEL_SORT = 1, CORRUPT = 2, TEAR = 3, VHS = 4, CRT = 5, ROLL = 6, BLEED = 7,
          BURN = 8, LEAK = 9, DAMAGE = 10, FIREWORKS = 11, EXPLOSION = 12;

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

    // ---- light and dirt ----
    a = amt[VHS];
    if (a > 0.0) {
        float snow = hash(uv * res + floor(time * 30.0));
        c = mix(c, vec3(snow), band_in * 0.6 * a);
        c = mix(c, vec3(luma(c)), 0.35 * a);
        c += (hash(uv * res * 0.5 + time) - 0.5) * 0.08 * a;
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
    color = vec4(clamp(c, 0.0, 1.0), 1.0);
}
"""
