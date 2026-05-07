from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class QueueItem:
    source_path: Path
    platform: str
    url: str
    title: str = ""
    author: str = ""
    published: str = ""
    captured: str = ""
    medium: str = ""
    item_id: str = ""
    description: str = ""
    transcript: str = ""
    content_error: str = ""
    title_is_fallback: bool = False
    raw_text: str = ""
    raw_payload: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class IntakeNote:
    item: QueueItem
    path: Path
    relative_link: str


@dataclass(slots=True)
class SourceNote:
    item: QueueItem
    intake_note: Path
    path: Path
