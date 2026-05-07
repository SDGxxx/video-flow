from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json
from hashlib import sha1


@dataclass
class Manifest:
    path: Path
    data: dict[str, dict[str, str]] = field(default_factory=lambda: {"raw": {}, "source": {}})

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if "raw" not in data:
                data["raw"] = {}
            if "source" not in data:
                data["source"] = {}
            return cls(path=path, data=data)
        return cls(path=path)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def item_key(platform: str, item_id: str, url: str) -> str:
        source = f"{platform}|{item_id}|{url}".strip("|")
        return sha1(source.encode("utf-8")).hexdigest()

    def seen_raw(self, platform: str, item_id: str, url: str) -> bool:
        return self.item_key(platform, item_id, url) in self.data["raw"]

    def seen_source(self, inbox_note: str) -> bool:
        return inbox_note in self.data["source"]

    def mark_raw(self, platform: str, item_id: str, url: str, note_path: str) -> None:
        self.data["raw"][self.item_key(platform, item_id, url)] = note_path

    def mark_source(self, inbox_note: str, source_note: str) -> None:
        self.data["source"][inbox_note] = source_note

    def raw_note_path(self, platform: str, item_id: str, url: str) -> str:
        return self.data["raw"].get(self.item_key(platform, item_id, url), "")
