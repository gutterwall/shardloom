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
