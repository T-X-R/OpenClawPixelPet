from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image


@dataclass
class Frame:
    rgba_bytes: bytes
    width: int
    height: int
    duration_ms: int


def decode_gif(path: Path) -> list[Frame]:
    im = Image.open(path)
    frames: list[Frame] = []
    try:
        i = 0
        while True:
            im.seek(i)
            rgba = im.convert("RGBA")
            duration = int(im.info.get("duration", 80))
            frames.append(
                Frame(
                    rgba_bytes=rgba.tobytes(),
                    width=rgba.width,
                    height=rgba.height,
                    duration_ms=max(20, duration),
                )
            )
            i += 1
    except EOFError:
        pass
    return frames
