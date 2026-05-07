from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha1
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.parse import parse_qs
from urllib.request import Request, urlopen
import json
import re

from .models import QueueItem
from .content import fetch_video_content, load_sidecar_transcript


URL_PATTERN = re.compile(r"https?://[^\s<>\]\)\"']+", re.IGNORECASE)


def today_text() -> str:
    return date.today().isoformat()


def sanitize_filename(value: str, fallback: str = "untitled") -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', " ", value).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or fallback


def infer_platform(url: str = "", text: str = "") -> str:
    host = urlparse(url).netloc.lower()
    blob = f"{url} {text}".lower()
    if "douyin.com" in host or "v.douyin.com" in host or "douyin" in blob:
        return "douyin"
    if "xiaohongshu.com" in host or "xhslink.com" in host or "xiaohongshu" in blob or "小红书" in text:
        return "xiaohongshu"
    return ""


def infer_medium(platform: str, url: str = "", title: str = "", description: str = "") -> str:
    blob = f"{url} {title} {description}".lower()
    if "video" in blob or platform == "douyin":
        return "video"
    return "article"


def infer_item_id(platform: str, url: str, title: str = "") -> str:
    patterns = {
        "douyin": [
            r"/video/(\d+)",
            r"/note/(\d+)",
            r"/share/video/(\d+)",
        ],
        "xiaohongshu": [
            r"/explore/([0-9a-zA-Z]+)",
            r"/discovery/item/([0-9a-zA-Z]+)",
            r"/note/([0-9a-zA-Z]+)",
        ],
    }
    for pattern in patterns.get(platform, []):
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key in ("modal_id", "item_id", "video_id", "aweme_id", "note_id"):
        values = query.get(key)
        if values and values[0]:
            return values[0]
    digest = sha1(f"{platform}|{url}|{title}".encode("utf-8")).hexdigest()
    return digest[:16]


def fallback_title(platform: str, item_id: str, source_name: str) -> str:
    if platform == "douyin" and item_id:
        return f"抖音收藏 {item_id}"
    if platform == "xiaohongshu" and item_id:
        return f"小红书收藏 {item_id}"
    return source_name


class MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.in_title = False
        self.meta: dict[str, str] = {}
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {k.lower(): (v or "") for k, v in attrs}
        if tag.lower() == "meta":
            key = attrs_map.get("property") or attrs_map.get("name")
            content = attrs_map.get("content", "")
            if key and content:
                self.meta[key.lower()] = content
        elif tag.lower() == "a":
            href = attrs_map.get("href", "")
            if href:
                self.links.append(href)
        elif tag.lower() == "title":
            self.in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title += data


def fetch_url_metadata(url: str, timeout: float = 20.0) -> dict[str, str]:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 X1-Collector/0.1"})
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="ignore")
    except (URLError, TimeoutError, ValueError):
        return {}
    parser = MetaParser()
    parser.feed(body)
    meta = {
        "title": parser.title.strip(),
        "description": parser.meta.get("og:description") or parser.meta.get("description", ""),
        "author": parser.meta.get("author", ""),
        "site": parser.meta.get("og:site_name", ""),
        "published": parser.meta.get("article:published_time", ""),
        "canonical_url": parser.meta.get("og:url", url),
    }
    if not meta["title"]:
        meta["title"] = parser.meta.get("og:title", "")
    return {key: value for key, value in meta.items() if value}


def extract_urls(text: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for match in URL_PATTERN.finditer(text):
        url = match.group(0).rstrip(".,;:!?)]\"'")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def parse_json_payload(text: str) -> list[QueueItem]:
    payload = json.loads(text)
    items: list[QueueItem] = []
    if isinstance(payload, list):
        objects: Iterable[object] = payload
    else:
        objects = payload.get("items") or payload.get("data") or [payload] if isinstance(payload, dict) else []
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        url = str(obj.get("url") or obj.get("source_url") or obj.get("link") or "").strip()
        if not url:
            continue
        title = str(obj.get("title") or obj.get("name") or "").strip()
        author = str(obj.get("author") or obj.get("creator") or "").strip()
        platform = str(obj.get("platform") or infer_platform(url, json.dumps(obj, ensure_ascii=False)) or "").strip()
        description = str(obj.get("description") or obj.get("summary") or "").strip()
        captured = str(obj.get("captured") or obj.get("captured_at") or "").strip()
        published = str(obj.get("published") or obj.get("published_at") or "").strip()
        medium = str(obj.get("medium") or infer_medium(platform, url, title, description)).strip()
        item_id = str(obj.get("item_id") or infer_item_id(platform, url, title)).strip()
        items.append(
            QueueItem(
                source_path=Path("<json>"),
                platform=platform,
                url=url,
                title=title,
                author=author,
                published=published,
                captured=captured,
                medium=medium,
                item_id=item_id,
                description=description,
                raw_text=text,
                raw_payload=obj,
            )
        )
    return items


def parse_text_payload(
    path: Path,
    text: str,
    cookies: Path | None = None,
    cookies_from_browser: str = "",
    remote_transcribe: bool = False,
    state_root: Path | None = None,
    transcribe_model: str = "",
) -> list[QueueItem]:
    items: list[QueueItem] = []
    for url in extract_urls(text):
        platform = infer_platform(url, text)
        if not platform:
            continue
        metadata = fetch_url_metadata(url)
        video_content = (
            fetch_video_content(
                url,
                cookies=cookies,
                cookies_from_browser=cookies_from_browser,
                remote_transcribe=remote_transcribe,
                state_root=state_root,
                transcribe_model=transcribe_model,
            )
            if platform in {"douyin", "xiaohongshu"}
            else None
        )
        sidecar_transcript = load_sidecar_transcript(path)
        if video_content and video_content.ok:
            metadata = {
                **metadata,
                "title": video_content.title or metadata.get("title", ""),
                "description": video_content.description or metadata.get("description", ""),
                "author": video_content.author or metadata.get("author", ""),
                "published": video_content.published or metadata.get("published", ""),
                "canonical_url": video_content.canonical_url or metadata.get("canonical_url", url),
            }
        item_id = infer_item_id(platform, url, metadata.get("title", ""))
        title_is_fallback = not bool(metadata.get("title", ""))
        title = metadata.get("title", "") or fallback_title(platform, item_id, path.stem)
        author = metadata.get("author", "")
        description = metadata.get("description", "")
        item = QueueItem(
            source_path=path,
            platform=platform,
            url=metadata.get("canonical_url", url),
            title=title,
            author=author,
            published=metadata.get("published", ""),
            captured=today_text(),
            medium=infer_medium(platform, url, title, description),
            item_id=item_id,
            description=description,
            transcript=sidecar_transcript or (video_content.transcript if video_content else ""),
            content_error=(video_content.error if video_content else "") or ("sidecar transcript not found" if not sidecar_transcript and not (video_content and video_content.ok) else ""),
            title_is_fallback=title_is_fallback,
            raw_text=text,
            raw_payload={"source_file": str(path), "metadata": metadata, "video_content": video_content.raw if video_content else None},
        )
        items.append(item)
    return items


def parse_url_shortcut(
    path: Path,
    cookies: Path | None = None,
    cookies_from_browser: str = "",
    remote_transcribe: bool = False,
    state_root: Path | None = None,
    transcribe_model: str = "",
) -> list[QueueItem]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    url_match = re.search(r"^URL=(.+)$", text, re.MULTILINE)
    if not url_match:
        return []
    return parse_text_payload(
        path,
        url_match.group(1),
        cookies=cookies,
        cookies_from_browser=cookies_from_browser,
        remote_transcribe=remote_transcribe,
        state_root=state_root,
        transcribe_model=transcribe_model,
    )


def parse_html_snapshot(path: Path) -> list[QueueItem]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    parser = MetaParser()
    parser.feed(text)
    url = parser.meta.get("og:url", "")
    if not url:
        anchors = parser.links
        if anchors:
            url = anchors[0]
    platform = infer_platform(url, text)
    if not platform:
        return []
    item_id = infer_item_id(platform, url, parser.title.strip() or parser.meta.get("og:title", ""))
    parsed_title = parser.title.strip() or parser.meta.get("og:title", "")
    title = parsed_title or fallback_title(platform, item_id, path.stem)
    description = parser.meta.get("og:description") or parser.meta.get("description", "")
    author = parser.meta.get("author", "")
    return [
        QueueItem(
            source_path=path,
            platform=platform,
            url=url,
            title=title,
            author=author,
            published=parser.meta.get("article:published_time", ""),
            captured=today_text(),
            medium=infer_medium(platform, url, title, description),
            item_id=item_id,
            description=description,
            title_is_fallback=not bool(parsed_title),
            raw_text=text,
            raw_payload={"source_file": str(path), "metadata": parser.meta},
        )
    ]


def load_queue_items(
    path: Path,
    cookies: Path | None = None,
    cookies_from_browser: str = "",
    remote_transcribe: bool = False,
    state_root: Path | None = None,
    transcribe_model: str = "",
) -> list[QueueItem]:
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8", errors="ignore")
    if suffix in {".txt", ".md"}:
        if text.lstrip().startswith("{") or text.lstrip().startswith("["):
            try:
                return parse_json_payload(text)
            except json.JSONDecodeError:
                return parse_text_payload(
                    path,
                    text,
                    cookies=cookies,
                    cookies_from_browser=cookies_from_browser,
                    remote_transcribe=remote_transcribe,
                    state_root=state_root,
                    transcribe_model=transcribe_model,
                )
        return parse_text_payload(
            path,
            text,
            cookies=cookies,
            cookies_from_browser=cookies_from_browser,
            remote_transcribe=remote_transcribe,
            state_root=state_root,
            transcribe_model=transcribe_model,
        )
    if suffix == ".json":
        return parse_json_payload(text)
    if suffix == ".url":
        return parse_url_shortcut(
            path,
            cookies=cookies,
            cookies_from_browser=cookies_from_browser,
            remote_transcribe=remote_transcribe,
            state_root=state_root,
            transcribe_model=transcribe_model,
        )
    if suffix in {".html", ".htm"}:
        return parse_html_snapshot(path)
    return []
