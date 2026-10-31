"""Output formats (sizes for YouTube and Instagram), render quality and frame rates."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Format:
    key: str
    group: str
    name: str
    width: int
    height: int
    note: str = ""

    @property
    def label(self) -> str:
        return f"{self.group}: {self.name} ({self.width}x{self.height})"

    @property
    def aspect(self) -> float:
        return self.width / self.height


FORMATS = [
    Format("youtube-720", "YouTube", "720p", 1280, 720),
    Format("youtube-1080", "YouTube", "1080p HD", 1920, 1080, "The usual YouTube size."),
    Format("youtube-1440", "YouTube", "1440p", 2560, 1440),
    Format("youtube-4k", "YouTube", "4K", 3840, 2160, "Slow to render; set shard detail to High or Full."),
    Format("youtube-shorts", "YouTube", "Shorts (vertical)", 1080, 1920, "Shorts can be up to 3 minutes long."),
    Format("instagram-reel", "Instagram", "Reel / Story (9:16)", 1080, 1920, "Reels can be up to 3 minutes long."),
    Format("instagram-portrait", "Instagram", "Feed portrait (4:5)", 1080, 1350, "Takes the most room in the feed."),
    Format("instagram-square", "Instagram", "Feed square (1:1)", 1080, 1080),
    Format("instagram-landscape", "Instagram", "Feed landscape (1.91:1)", 1080, 566),
    Format("tiktok", "Other", "TikTok (9:16)", 1080, 1920),
    Format("small", "Other", "Small preview", 640, 360, "Quick to render, for checking the edit."),
]
BY_KEY = {f.key: f for f in FORMATS}

# Quality: (label, bits per pixel per frame, x264 preset).
QUALITY = {
    "draft": ("Draft (fast)", 0.06, "veryfast"),
    "good": ("Good", 0.13, "medium"),
    "best": ("Best (slow)", 0.2, "slow"),
}

FRAME_RATES = [24.0, 25.0, 30.0, 50.0, 60.0]

# Shard detail: the short side of the stored shard frames.
DETAIL = {360: "Draft (360)", 540: "Standard (540)", 720: "High (720)", 1080: "Full (1080)"}


def even(x: float) -> int:
    return max(16, int(round(x / 2)) * 2)


def output_size(output: dict) -> tuple[int, int]:
    """(width, height) of a project's output settings."""
    f = BY_KEY.get(output.get("format", ""))
    if f:
        return f.width, f.height
    return even(output.get("width", 1920)), even(output.get("height", 1080))


def bitrate(width: int, height: int, fps: float, quality: str) -> int:
    bpp = QUALITY.get(quality, QUALITY["good"])[1]
    return int(max(1_000_000, width * height * fps * bpp))


def preset(quality: str) -> str:
    return QUALITY.get(quality, QUALITY["good"])[2]


def pool_size(width: int, height: int, detail: int) -> tuple[int, int]:
    """Shard frame size for an output shape: the output's aspect, short side detail (never above the output)."""
    short = min(detail, min(width, height))
    if width >= height:
        return even(short * width / height), even(short)
    return even(short), even(short * height / width)


def preview_size(width: int, height: int, longest: int = 960) -> tuple[int, int]:
    scale = min(1.0, longest / max(width, height))
    return even(width * scale), even(height * scale)
