# Shardloom

A step-sequenced video instrument and editor for Linux and Windows. Shardloom cuts your raw footage and photos into small random **shards**, plays them from a probability step sequencer locked to your song's beats, and lets the music's intensity and beat drive colour, palettes and effects. Add opening and closing titles, play it live, record your moves, and render frame-exact MP4s sized for YouTube or Instagram.

## Install

**Windows:** download `Shardloom-…-windows-x64.zip` from [Releases](https://github.com/gutterwall/shardloom/releases), unzip it, and double-click `Shardloom.exe`.

**Linux** (or Windows with Python 3.11+): everything installs with pip. Video goes through FFmpeg (PyAV) and sound through miniaudio, and you need OpenGL 3.3.

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/shardloom          # the editor
```

## The editor

Run `shardloom` with no arguments (or double-click `Shardloom.exe`) and make a new project. The window has two workspaces, switched at the top:

**Edit**
- **Media:** choose the song (WAV, MP3, FLAC, …) and add videos and photos to cut from. For each video, set its share of the cuts and use only part of it (from / to). Each photo becomes one or more shards: still, slow zoom in or out, or a pan. Choose which layers each file plays in. Set the number and length of the shards, the seed and the shard detail, then press **Cut shards**.
- **Preview:** the finished video, with its titles and fades, in the output's shape.
- **Timeline:** title cards, titles over the picture and the song section. Click to move the playhead. Drag the blue ends of the song strip to use only part of the song.
- **Video settings:**
  - **Format:** YouTube (720p, 1080p, 1440p, 4K, Shorts), Instagram (Reel/Story 9:16, feed 4:5, square, landscape), TikTok or a custom size; frame rate; quality; song section; fade in and out.
  - **Titles:** text and subtitle, at the start or end, over the picture or on their own card before or after the song. Each has a position, size, colour, font, shadow, fade, and a background colour or picture.
  - **Render:** render the current size, or several sizes one after the other into `renders/`.

**Perform:** the instrument (sequencer, step editor, pool and music links), described below.

### Projects keep everything

A project is a folder that keeps a copy of every file it uses, so it can be moved, edited again or rebuilt anywhere:

```
My video/
  project.json   the edit: media list, cut settings, sequencer, titles, output
  media/         copies of the song, videos, photos and title pictures
  pool/          the cut shards (rebuilt from media/ when missing)
  analysis.*     the song's beats and loudness (rebuilt when missing)
  renders/       your rendered videos
```

The **File** menu can save a copy as a new project, or pack a project into one `.zip` with its media. Opening a `.zip` unpacks it and rebuilds the shards and analysis. The same seed and settings always cut the same shards.

## The command line

```sh
# A project from a song, videos and photos (copied in), cut and analysed.
shardloom new myvideo --music song.mp3 --video clip1.mp4 clip2.mov photo.jpg --format instagram-reel

shardloom edit myvideo                       # the editor
shardloom play myvideo                       # just the instrument
shardloom render myvideo                     # into myvideo/renders/, at the project's format
shardloom render myvideo --format youtube-1080 --quality best -o out.mp4

shardloom pack myvideo                       # myvideo.zip: the project and its media
shardloom unpack myvideo.zip                 # unpack it and rebuild the shards
shardloom build myvideo                      # rebuild whatever is missing or out of date
shardloom info myvideo
```

## How it works

**Shards.** Each video is cut at random places into pieces between the shortest and longest length, without overlap, scaled and cropped to the output's shape. If you change the shape afterwards, shards are cropped to fit until you cut them again. Each shard gets an *energy* score (how much moves in it), ranked across the pool from 0 (calmest) to 1 (busiest).

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

Recorded moves play back, and export, until you clear them. In the editor these keys work in the Perform workspace. In Edit, Space plays the preview, ← → move 5 seconds and Home goes to the start.

## Inspiration

Shardloom was inspired by the description of [ABJEXT Studio](https://github.com/sxrvys/Abjext), a Mac video editor and performance instrument that sequences footage like samples, with probability, per-step effects and music-driven visuals. ABJEXT's source isn't public, so Shardloom is an independent program written from scratch. It shares no code with ABJEXT and isn't affiliated with it.

## Tests

```sh
.venv/bin/python -m pytest
```

The tests make their own media and need OpenGL 3.3 without a window (EGL on Linux); the render tests are skipped without it. CI runs them on Linux and Windows, builds `Shardloom.exe` with PyInstaller (`packaging/shardloom.spec`), and smoke-tests it: it makes a project, renders it, and opens both editor workspaces.

## License

MIT. See [LICENSE](LICENSE).
