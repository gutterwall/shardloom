## Windows

1. Download `Shardloom-…-windows-x64.zip` below and unzip it anywhere.
2. Open the `Shardloom` folder and double-click `Shardloom.exe`.
3. Make a new project, choose a song, add videos and photos, and press **Cut shards**.

Needs 64-bit Windows 10 or 11 and a graphics driver with OpenGL 3.3, which almost every PC from the last ten years has.

Windows may warn that the app is from an unknown publisher, because it isn't code-signed. Choose **More info → Run anyway**.

`Shardloom.exe` also works from a terminal: `Shardloom.exe new`, `edit`, `render`, `pack` and the rest, the same as on Linux. See the README.

## New in this version: shards take a fraction of the space

- **Shards are stored as small videos.** The pool used to keep every frame of every shard raw, about 2.7 MB a frame at 720p, so a big pool could fill tens of gigabytes. Each shard is now a short H.264 video (`pool/0000.mp4`, …) with every frame a keyframe: 50 to 70 times smaller, and shards still play from any frame, at any speed and backwards. 40 shards of 720p phone footage take 53 MB instead of 2.9 GB.
- **Cutting shards uses far less memory.** Shards are cut a frame at a time, with one video open at a time, so a project with dozens of 1080p videos cut at 60 fps no longer runs out of memory (one long 1080p shard went from 3.3 GB to 0.2 GB).

Pools cut by an older version still play, but show **out of date**: press **Cut shards** to cut them again. You get the same shards, the same seed and the same edit, in a fraction of the space.

## Colour flip and leaf colours (0.8)

- **Colour flip** (Shard effects → Colour): a strobe that flashes in time with the beat (1, 2 or 4 **flashes** a beat), and each flash turns over one or more of the red, green and blue channels, a new mix each time.
- **Leaf colours** on the **Mirror**: each leaf of the mirror, quad mirror or kaleidoscope turns over its own share of red, green and blue, so every leaf has a different colour, with new colours on each beat when **on the beat** is ticked. It is off unless you turn it up, so existing projects look the same.

## Effects on single layers (0.7)

A **One layer** group on the Shard effects page, on the desktop and on phones:

- **Colour lens:** a single-colour lens fades in over one of the overlay layers, a new random colour each time, holds, and fades away.
- **Mirror:** folds one or more layers into a mirror, a quad mirror or a kaleidoscope (3 to 12 segments). It can zoom in from the middle or bring mirrored copies in from the edges. **On the beat**, the zoom punches in on every beat and the pattern turns a notch each beat; off, it zooms and turns smoothly through each strike.

They have the usual **how often**, **pulse** and **strength** sliders and the **try** button, and strike the same way in every play and render.

## Shardloom on phones (0.6)

Shardloom now runs on Android phones, iPhones and tablets, in the browser: open **https://gutterwall.github.io/shardloom/** and add it to your home screen. It is the same Shardloom, ported to the web: songs, videos and photos from your phone, shards cut on the phone, the sequencer, layers, shard effects, shard timing and titles, and MP4s rendered on the phone for YouTube, Instagram or TikTok. Nothing is uploaded. **Pack** a project to move it between your phone and a computer, either way.

## Shard timing and Colour (0.5)

### Shard timing

A **Shard timing** panel holds each layer on one shard for beats at a time instead of cutting on every step: it plays the shard over and over (4 times by default) for a length of ¼, ½, 1, 2, 4, 6 or 8 beats, then moves on. Weight the lengths, lean the overall speed faster or slower, and let intense parts of the song change shards more often.

### Colour that follows the music

Three more shard effects, in a **Colour** group: **hue shift** turns the colours round the wheel as the music gets more intense, **brightness** goes up when it's loud and down when it's quiet, and **saturation** gets richer when it's intense and greyer when it's calm. They have the same sliders (how often, pulse, strength), the **try** button, and **Surprise me** can pick them. At full rate they're on all the time.

## Shard effects (0.4)

A **Shard effects** panel with 13 effects that strike across the picture: datamosh, pixel sort, corruption and slice tears; VHS tracking, CRT, vertical roll and chroma bleed; film burns, light leaks and film damage; fireworks and explosions. Each has a slider for how often it strikes, whether it lands on the pulse (the beats and hits) or between, and its strength. **try** sets one off in the preview.

## The editor (0.3)

- **Media:** queue up videos and photos, and choose the song (WAV, MP3, …). For each video, set its share of the cuts, a from/to range and which layers it plays in. Photos become shards: still, zooming or panning.
- **Formats:** YouTube 720p, 1080p, 1440p, 4K and Shorts; Instagram Reel/Story, feed portrait, square and landscape; TikTok; or a custom size. Frame rate and quality. Render several sizes in one go.
- **Titles** at the start or end, over the picture or on their own card, with font, colour, size, position, shadow, fade and a background colour or picture.
- **Song section and fades:** use part of the song, trimmed on the timeline, with fades in and out.
- **Self-contained projects:** each project folder keeps copies of its song, videos and photos. Save a copy as a new project, or pack it into one `.zip`; opening a `.zip` rebuilds the shards and analysis.
- **The instrument is now a workspace in the same window**, docked, and fits smaller screens.

Projects from 0.2 open in the editor. Their media is copied into the project the first time they're saved.

## Linux

1. Download `Shardloom-…-linux-x64.tar.gz` below.
2. Unpack it and run the installer:
   ```sh
   tar xzf Shardloom-*-linux-x64.tar.gz
   Shardloom/install.sh
   ```
3. Start **Shardloom** from the applications menu, or type `shardloom`.

It installs into `~/.local` for your user only (`install.sh --remove` uninstalls it). Needs a 64-bit distribution from 2022 or later (glibc 2.35, e.g. Ubuntu 22.04, Fedora 36, Debian 12) and OpenGL 3.3.

Or from source: `pip install git+https://github.com/gutterwall/shardloom`

## Checking a download (0.6.2)

From 0.6.2 on, CI builds each download from the tagged commit and publishes a GitHub build provenance attestation and a `.sha256` checksum with it. With the [GitHub CLI](https://cli.github.com/):

```sh
gh attestation verify Shardloom-*-linux-x64.tar.gz --repo gutterwall/shardloom
sha256sum -c Shardloom-*-linux-x64.tar.gz.sha256
```

Nothing else changed in the app since 0.6.1.
