"""Writes a short test song (WAV) and two test videos (MP4) into a folder, for smoke tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from conftest import click_track, make_video, make_wav  # noqa: E402

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
make_wav(out / "song.wav", click_track(120, 24.0, loud_from=12.0))
make_video(out / "ball.mp4", "ball", seconds=6.0)
make_video(out / "still.mp4", "still", seconds=6.0)
print("test media in", out)
