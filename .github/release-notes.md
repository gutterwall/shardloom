## Windows

1. Download `Shardloom-…-windows-x64.zip` below and unzip it anywhere.
2. Open the `Shardloom` folder and double-click `Shardloom.exe`.
3. Pick a song and some videos, then press **Make it**.

Needs 64-bit Windows 10 or 11 and a graphics driver with OpenGL 3.3, which almost every PC from the last ten years has.

Windows may warn that the app is from an unknown publisher, because it isn't code-signed. Choose **More info → Run anyway**.

`Shardloom.exe` also works from a terminal: `Shardloom.exe new`, `render`, `play` and `info`, the same as on Linux. See the README.

## New in this version

- **Start window:** choose a song and videos, set how many shards to cut, and press Make it.
- **Export from the instrument:** the Export MP4 button renders the whole song with a progress bar.
- **Media through FFmpeg (PyAV) and miniaudio:** Shardloom no longer needs GStreamer, so it installs with pip alone on Linux and Windows.

## Linux

```sh
pip install git+https://github.com/gutterwall/shardloom
shardloom
```
