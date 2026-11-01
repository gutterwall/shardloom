## Windows

1. Download `Shardloom-…-windows-x64.zip` below and unzip it anywhere.
2. Open the `Shardloom` folder and double-click `Shardloom.exe`.
3. Make a new project, choose a song, add videos and photos, and press **Cut shards**.

Needs 64-bit Windows 10 or 11 and a graphics driver with OpenGL 3.3, which almost every PC from the last ten years has.

Windows may warn that the app is from an unknown publisher, because it isn't code-signed. Choose **More info → Run anyway**.

`Shardloom.exe` also works from a terminal: `Shardloom.exe new`, `edit`, `render`, `pack` and the rest, the same as on Linux. See the README.

## New in this version: shard effects

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
