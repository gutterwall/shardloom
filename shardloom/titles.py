"""Title pictures: a title's text (and background) drawn with Pillow into an RGBA image the size of the video."""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np

from .sequencer import Title, project_path

POSITIONS = ["upper", "center", "lower"]
BACKGROUNDS = ["none", "colour", "image"]


def hex_rgb(colour: str) -> tuple[int, int, int]:
    c = colour.lstrip("#")
    try:
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except (ValueError, IndexError):
        return 255, 255, 255


def _font_dirs() -> list[Path]:
    if sys.platform == "win32":
        dirs = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"]
    elif sys.platform == "darwin":
        dirs = [Path("/System/Library/Fonts"), Path("/Library/Fonts"), Path.home() / "Library" / "Fonts"]
    else:
        dirs = [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".local" / "share" / "fonts",
                Path.home() / ".fonts"]
    return [d for d in dirs if d.is_dir()]


@lru_cache(maxsize=1)
def system_fonts() -> dict[str, str]:
    """Font file name -> path, for the fonts installed on this computer."""
    found: dict[str, str] = {}
    for d in _font_dirs():
        for path in d.rglob("*"):
            if path.suffix.lower() in (".ttf", ".otf") and path.name not in found:
                found[path.name] = str(path)
            if len(found) >= 2000:
                break
    return dict(sorted(found.items(), key=lambda kv: kv[0].lower()))


def font_names() -> list[str]:
    return list(system_fonts())


@lru_cache(maxsize=64)
def _font(name: str, px: int):
    from PIL import ImageFont

    path = system_fonts().get(name) if name else None
    if path:
        try:
            return ImageFont.truetype(path, px)
        except OSError:
            pass
    # The font built into Pillow; a project from another computer still renders without its font.
    return ImageFont.load_default(px)


def _wrap(draw, text: str, font, width: float) -> list[str]:
    lines = []
    for para in text.split("\n"):
        words = para.split(" ")
        line = ""
        for w in words:
            trial = f"{line} {w}" if line else w
            if line and draw.textlength(trial, font=font) > width:
                lines.append(line)
                line = w
            else:
                line = trial
        lines.append(line)
    return lines


def render_title(title: Title, width: int, height: int, directory: str | os.PathLike = ".",
                 card: bool = False) -> np.ndarray:
    """uint8 (height, width, 4), straight (not premultiplied) alpha, top row first.

    card: the title has its own time, so it always gets a background (black when it has none).
    """
    from PIL import Image, ImageDraw, ImageFilter

    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    background = title.background
    if background == "image" and title.image:
        try:
            from .library import cover_box, load_photo

            img = load_photo(project_path(directory, title.image))
            box = cover_box(img.size[0], img.size[1], width, height)
            canvas = img.resize((width, height), Image.Resampling.LANCZOS, box=box).convert("RGBA")
        except OSError:
            background = "colour"
    if background == "colour" or (card and background == "none"):
        canvas = Image.new("RGBA", (width, height), (*hex_rgb(title.colour if background == "colour" else "#000000"), 255))

    text_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(text_layer)
    # Sized by the short side, so a title looks the same in a vertical video as in a wide one.
    main_px = max(8, int(title.size * min(width, height)))
    sub_px = max(8, int(main_px * 0.45))
    main_font, sub_font = _font(title.font, main_px), _font(title.font, sub_px)
    blocks = []
    if title.text.strip():
        blocks += [(line, main_font, main_px) for line in _wrap(draw, title.text, main_font, width * 0.88)]
    if title.subtitle.strip():
        blocks += [(line, sub_font, sub_px) for line in _wrap(draw, title.subtitle, sub_font, width * 0.88)]
    if blocks:
        gap = [int(px * 0.25) for _, _, px in blocks]
        heights = [int(px * 1.15) for _, _, px in blocks]
        total = sum(heights) + sum(gap[:-1])
        centre = {"upper": 0.24, "center": 0.5, "lower": 0.76}.get(title.position, 0.5) * height
        y = max(0.0, min(centre - total / 2, height - total))
        colour = (*hex_rgb(title.text_colour), 255)
        placed = []
        for (line, font, px), h, g in zip(blocks, heights, gap):
            x = width / 2
            placed.append((x, y + h / 2, line, font))
            y += h + g
        if title.shadow:
            shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            sd = ImageDraw.Draw(shadow)
            off = max(1, main_px // 24)
            for x, yy, line, font in placed:
                sd.text((x + off, yy + off), line, font=font, fill=(0, 0, 0, 190), anchor="mm")
            shadow = shadow.filter(ImageFilter.GaussianBlur(max(1, main_px // 16)))
            text_layer = Image.alpha_composite(shadow, text_layer)
            draw = ImageDraw.Draw(text_layer)
        for x, yy, line, font in placed:
            draw.text((x, yy), line, font=font, fill=colour, anchor="mm")
    return np.asarray(Image.alpha_composite(canvas, text_layer))
