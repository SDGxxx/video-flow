from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json
import re
from typing import Any

from .adapters import sanitize_filename
from .models import IntakeNote, QueueItem, SourceNote


RAW_TYPES = {"captured", "intake"}


class MissingContentError(ValueError):
    """Raised when a video intake note has no usable description or transcript."""


def today_text() -> str:
    return datetime.now().date().isoformat()


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def vault_relative_link(vault_root: Path, path: Path) -> str:
    rel = path.relative_to(vault_root).as_posix()
    if rel.lower().endswith(".md"):
        rel = rel[:-3]
    return f"[[{rel}]]"


def _yaml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _yaml_list(name: str, values: list[str]) -> str:
    if not values:
        return f"{name}:\n"
    out = [f"{name}:"]
    for value in values:
        out.append(f"  - {_yaml_quote(value)}")
    return "\n".join(out) + "\n"


def build_frontmatter(fields: dict[str, Any]) -> str:
    lines: list[str] = ["---"]
    for key, value in fields.items():
        if value is None or value == "":
            lines.append(f"{key}:")
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif isinstance(value, list):
            lines.append(f"{key}:")
            for entry in value:
                lines.append(f"  - {_yaml_quote(str(entry))}")
        else:
            lines.append(f"{key}: {_yaml_quote(str(value))}")
    lines.append("---")
    return "\n".join(lines)


def pick_section_root(vault_root: Path, medium: str, section: str) -> Path:
    if section == "inbox":
        base = vault_root / "00 Inbox"
    else:
        base = vault_root / "10 Sources"
    if medium == "article":
        return base / "Articles"
    return base / "Media"


def make_note_path(root: Path, date_text: str, title: str) -> Path:
    filename = f"{date_text} {sanitize_filename(title)}.md"
    return root / filename


def parse_frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    raw = parts[1].strip()
    body = parts[2].lstrip("\n")
    data: dict[str, Any] = {}
    key = ""
    list_mode: list[str] | None = None
    for line in raw.splitlines():
        if not line.strip():
            continue
        if line.startswith("  - ") and key:
            value = line[4:].strip()
            data.setdefault(key, []).append(json.loads(value))
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not value:
            data[key] = []
            continue
        try:
            data[key] = json.loads(value)
        except json.JSONDecodeError:
            data[key] = value
    return data, body


def parse_title(path: Path, body: str) -> str:
    match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return path.stem


def write_text_if_changed(path: Path, text: str) -> bool:
    if path.exists():
        existing = path.read_text(encoding="utf-8", errors="ignore")
        if existing == text:
            return False
    ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8")
    return True


def build_intake_note(vault_root: Path, item: QueueItem) -> IntakeNote:
    section_root = pick_section_root(vault_root, item.medium, "inbox")
    title = item.title or f"{item.platform} {item.item_id}"
    date_text = item.captured or today_text()
    note_path = make_note_path(section_root, date_text, f"{item.platform} 收藏 - {title}")
    raw_link = vault_relative_link(vault_root, note_path)
    fields = {
        "type": "intake",
        "platform": item.platform,
        "medium": item.medium,
        "status": "captured",
        "source_url": item.url,
        "item_id": item.item_id,
        "title": title,
        "title_is_fallback": item.title_is_fallback,
        "author": item.author,
        "published": item.published,
        "captured": date_text,
        "language": "zh",
        "source_queue": str(item.source_path),
        "confidence": "medium",
    }
    if item.description:
        fields["description"] = item.description
    if item.transcript:
        fields["has_transcript"] = True
    if item.content_error:
        fields["content_error"] = item.content_error
    summary = [
        build_frontmatter(fields),
        "",
        f"# {title}",
        "",
        "## 原始信息",
        f"- platform: {item.platform}",
        f"- url: {item.url}",
        f"- item_id: {item.item_id}",
        f"- source_queue: {item.source_path}",
        "",
        "## 原始摘录",
        item.raw_text.strip() if item.raw_text.strip() else "-",
        "",
    ]
    if item.description.strip():
        summary.extend(["## 视频描述", item.description.strip(), ""])
    if item.transcript.strip():
        summary.extend(["## 字幕 / 逐字稿", item.transcript.strip(), ""])
    if item.content_error.strip():
        summary.extend(["## 内容获取状态", item.content_error.strip(), ""])
    write_text_if_changed(note_path, "\n".join(summary))
    return IntakeNote(item=item, path=note_path, relative_link=raw_link)


def parse_inbox_note(path: Path) -> tuple[QueueItem, dict[str, Any], str]:
    fm, body = parse_frontmatter(path)
    if fm.get("type") not in RAW_TYPES:
        raise ValueError("not an intake note")
    title = str(fm.get("title") or parse_title(path, body))
    item = QueueItem(
        source_path=path,
        platform=str(fm.get("platform") or ""),
        url=str(fm.get("source_url") or ""),
        title=title,
        author=str(fm.get("author") or ""),
        published=str(fm.get("published") or ""),
        captured=str(fm.get("captured") or ""),
        medium=str(fm.get("medium") or "article"),
        description=str(fm.get("description") or _extract_first_section(body, ["视频描述", "瑙嗛鎻忚堪"])),
        transcript=_extract_first_section(body, ["字幕 / 逐字稿", "瀛楀箷 / 閫愬瓧绋?"]),
        content_error=str(fm.get("content_error") or _extract_first_section(body, ["内容获取状态", "鍐呭鑾峰彇鐘舵€?"])),
        item_id=str(fm.get("item_id") or ""),
        title_is_fallback=str(fm.get("title_is_fallback") or "").lower() == "true",
        raw_text=body,
        raw_payload=fm,
    )
    return item, fm, body


def _extract_section(body: str, heading: str) -> str:
    pattern = rf"^##\s+{re.escape(heading)}\s*$\n(.*?)(?=^##\s+|\Z)"
    match = re.search(pattern, body, re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


def _extract_first_section(body: str, headings: list[str]) -> str:
    for heading in headings:
        value = _extract_section(body, heading)
        if value:
            return value
    return ""


def _extract_subheading_section(body: str, heading: str) -> str:
    pattern = rf"^###\s+{re.escape(heading)}\s*$\n(.*?)(?=^###\s+|^##\s+|\Z)"
    match = re.search(pattern, body, re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


def extract_keywords(text: str, limit: int = 4) -> list[str]:
    parts = re.split(r"[^\w\u4e00-\u9fff]+", text)
    seen: list[str] = []
    for part in parts:
        token = part.strip()
        if len(token) < 2:
            continue
        if token in seen:
            continue
        seen.append(token)
        if len(seen) >= limit:
            break
    return seen


def build_source_note(vault_root: Path, intake_path: Path) -> SourceNote:
    item, fm, body = parse_inbox_note(intake_path)
    section_root = pick_section_root(vault_root, item.medium, "source")
    title = item.title or parse_title(intake_path, body)
    date_text = item.captured or today_text()
    note_path = make_note_path(section_root, date_text, title)
    has_video_content = bool(item.description.strip() or item.transcript.strip())
    has_transcript = bool(item.transcript.strip())
    chapter_summary = _extract_subheading_section(item.transcript, "平台章节摘要")
    summary_text = chapter_summary or item.description.strip() or item.transcript.strip() or item.content_error.strip() or title
    topics = [item.platform, item.medium]
    if item.author:
        topics.append(item.author)
    entities = [item.author] if item.author else []
    fm_out = {
        "type": "source",
        "medium": item.medium or "article",
        "status": "summarized" if has_transcript else ("needs_transcription" if item.medium == "video" else "captured"),
        "source_url": item.url,
        "source_inbox": vault_relative_link(vault_root, intake_path),
        "author": item.author,
        "site": item.raw_payload.get("site", "") if isinstance(item.raw_payload, dict) else "",
        "published": item.published,
        "captured": date_text,
        "language": "zh",
        "topics": topics,
        "entities": entities,
        "reviewed": False,
        "summary_model": "bilinote-metadata" if has_transcript else ("needs-asr" if item.medium == "video" else ""),
        "confidence": "medium" if has_transcript else "low",
    }
    keywords = extract_keywords(f"{title} {item.description} {item.transcript} {item.raw_text}")
    core_points = keywords[:3] or [title]
    concepts = keywords[1:4] if len(keywords) > 1 else [title]
    uncertain = []
    if not item.author:
        uncertain.append("作者信息待确认")
    if not item.published:
        uncertain.append("发布日期待确认")
    if not item.description.strip():
        uncertain.append("描述信息不足")
    if item.medium == "video" and not item.transcript.strip():
        uncertain.append("尚未取得逐字稿，需要进入转写阶段")
    body_lines = [
        build_frontmatter(fm_out),
        "",
        f"# {title}",
        "",
        "## AI 摘要",
        summary_text,
        "",
        "## 核心观点",
        *[f"- {point}" for point in core_points],
        "",
        "## 可沉淀概念",
        *[f"- {concept}" for concept in concepts],
        "",
        "## 待验证",
        *[f"- {item}" for item in (uncertain or ["待补充"])],
        "",
        "## 我的备注",
        f"- 来源：{vault_relative_link(vault_root, intake_path)}",
        f"- 原始链接：{item.url or '待补充'}",
        "",
        "## 原文 / 原始逐字稿",
        f"> 见：{vault_relative_link(vault_root, intake_path)}",
        "",
    ]
    if item.transcript.strip():
        body_lines.extend(["### 已获取逐字稿", item.transcript.strip(), ""])
    if item.content_error.strip():
        body_lines.extend(["### 转写状态", item.content_error.strip(), ""])
    write_text_if_changed(note_path, "\n".join(body_lines))
    return SourceNote(item=item, intake_note=intake_path, path=note_path)
