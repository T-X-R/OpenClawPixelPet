from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets"


@dataclass(frozen=True)
class Animation:
    name: str
    path: Path


def list_animations() -> list[Animation]:
    if not _ASSETS_DIR.exists():
        return []
    return [Animation(name=p.stem, path=p) for p in sorted(_ASSETS_DIR.glob("*.gif"))]
