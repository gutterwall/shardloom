"""The web app's files: shaders made from the desktop ones, everything cached for offline, versions in step."""

import re
import subprocess
import sys
from pathlib import Path

import shardloom

WEB = Path(__file__).resolve().parents[1] / "web"


def test_shaders_are_generated_from_the_desktop_ones():
    done = subprocess.run([sys.executable, str(WEB / "tools" / "gen_shaders.py"), "--check"], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_offline_cache_lists_every_file():
    sw = (WEB / "sw.js").read_text(encoding="utf-8")
    listed = set(re.findall(r'"([^"]+\.(?:js|mjs|css|html|png|svg|webmanifest))"', sw))
    on_disk = {p.relative_to(WEB).as_posix() for p in WEB.rglob("*")
               if p.is_file() and p.suffix in {".js", ".mjs", ".css", ".html", ".png", ".svg", ".webmanifest"}
               and p.name != "sw.js" and "tools" not in p.parts}
    assert on_disk <= listed, sorted(on_disk - listed)


def test_versions_match():
    app = re.search(r'VERSION = "([^"]+)"', (WEB / "js" / "app.js").read_text(encoding="utf-8")).group(1)
    cache = re.search(r'CACHE = "shardloom-([^"]+)"', (WEB / "sw.js").read_text(encoding="utf-8")).group(1)
    assert app == cache == shardloom.__version__
