# Shardloom

A step-sequenced video instrument for Linux. Shardloom cuts your raw footage into small random **shards**, plays them from a probability step sequencer locked to your song's beats, and lets the music's intensity and beat drive colour, palettes and effects. Play it live, record your moves, and export a frame-exact MP4.

## Install

Needs Python 3.11+, GStreamer 1.20+ with PyGObject (`python3-gi`, `gir1.2-gst-plugins-base-1.0`, plus the good, bad and libav plugin sets for decoding and `openh264enc`/`avenc_aac` for export), and OpenGL 3.3.

```sh
python3 -m venv --system-site-packages .venv    # system site packages for PyGObject
.venv/bin/pip install -e '.[test]'
```

## Use

```sh
# Cut 48 shards from your videos and analyse the song (tempo, beats, intensity).
shardloom new myvideo --music song.mp3 --video clip1.mp4 clip2.mov --count 48 --min 0.25 --max 1.5

shardloom play myvideo              # the live instrument
shardloom render myvideo -o out.mp4 # export (about ten times faster than real time)

shardloom slice myvideo more.mp4 --count 16   # add shards
shardloom analyse myvideo --music other.mp3   # change song
shardloom info myvideo
```

A project is a folder: `project.json` (patterns, lanes, links, recorded moves), `pool/` (the shards as frames) and `analysis.*` (the song's beats and curves).

## How it works

**Shards.** Each video is cut at random places into pieces between `--min` and `--max` seconds, without overlap, scaled and cropped to the project size. Each shard gets an *energy* score (how much moves in it), ranked across the pool from 0 (calmest) to 1 (busiest).

**The sequencer.** Lanes are layers, blended over each other (normal, add, screen, multiply, difference). Each step has:
- an on/off switch and a *chance* of firing
- a gate (how many steps it holds) and a speed
- reverse, and a random start point within the shard
- an optional fixed shard
- its own effect amounts (parameter locks)

When a step doesn't name a shard, its lane chooses one:
- `random`
- `bag`: every shard once before any repeat
- `no_repeat`
- `intensity`: calm shards in quiet passages, busy shards when the song is intense

Patterns can be chained into a song, or switched live.

**The clock** follows the song's tracked beats (or a fixed BPM grid), so cuts stay on the beat even when a live band drifts. The live view and the export use the same seed and step numbers, so they produce the same picture.

**The music moves the picture.**
- Links send bass, mid, high, level, onset, section intensity or the beat to any effect, on the master, all lanes or one lane.
- The colour palette changes when the song moves between quiet, medium and loud sections (or every N bars).

**Effects:**
- On any layer or the master: zoom, shake, mirror, kaleido, pixelate, glitch, rgb_split, hue, saturate, bw, posterize, invert, scanlines, colorize (palette gradient map).
- On the master only: trails, flash, strobe.

## Playing live

| Key | |
|---|---|
| Space | play / pause |
| 1–9 | switch pattern at the end of the current one |
| F (hold) | fill: every step fires |
| Z X C V B N M (hold) | invert, rgb split, kaleido, strobe, trails, glitch, mirror |
| R | record pattern switches and held effects into the project |
| ← → | one bar back / forward |
| Tab | hide the controls (output only) |
| Ctrl+S | save (also saved on exit) |

Recorded moves play back, and export, until you clear them.

## Tests

```sh
.venv/bin/python -m pytest
```

The tests make their own media with GStreamer and need headless OpenGL (EGL); the render tests are skipped without it.
