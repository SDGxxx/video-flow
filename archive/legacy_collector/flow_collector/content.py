from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import json
import os
import subprocess
import sys
import re
import shutil

from .bilinote_douyin import extract_content_fields, fetch_aweme_detail
from .transcriber import transcribe_remote_audio


DEFAULT_CHROME_CANDIDATES = [
    Path(os.getenv("CHROME_EXE", "")),
    Path("chrome"),
]


@dataclass(slots=True)
class ContentResult:
    ok: bool
    title: str = ""
    author: str = ""
    description: str = ""
    transcript: str = ""
    canonical_url: str = ""
    published: str = ""
    error: str = ""
    raw: dict[str, object] | None = None

    @property
    def has_content(self) -> bool:
        return bool(self.description.strip() or self.transcript.strip())


class _MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.in_title = False
        self.meta: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {k.lower(): (v or "") for k, v in attrs}
        if tag.lower() == "meta":
            key = attrs_map.get("property") or attrs_map.get("name")
            content = attrs_map.get("content", "")
            if key and content:
                self.meta[key.lower()] = content
        elif tag.lower() == "title":
            self.in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title += data


def normalize_video_url(url: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    modal_id = query.get("modal_id", [""])[0]
    host = parsed.netloc.lower()
    if modal_id and "douyin.com" in host:
        return f"https://www.douyin.com/video/{modal_id}"
    return url


def infer_browser_chrome_path() -> str:
    for candidate in DEFAULT_CHROME_CANDIDATES:
        if candidate.exists():
            return str(candidate)
    found = shutil.which("chrome.exe") or shutil.which("chrome")
    if found:
        return found
    return ""


def _run_ytdlp(url: str, cookies: Path | None = None, cookies_from_browser: str = "") -> tuple[int, str, str]:
    command = [sys.executable, "-m", "yt_dlp", "--dump-single-json", "--skip-download", normalize_video_url(url)]
    if cookies:
        command[3:3] = ["--cookies", str(cookies)]
    elif cookies_from_browser:
        command[3:3] = ["--cookies-from-browser", cookies_from_browser]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return completed.returncode, completed.stdout, completed.stderr


def _pick_caption(data: dict[str, object]) -> str:
    for key in ("subtitles", "automatic_captions"):
        captions = data.get(key)
        if not isinstance(captions, dict):
            continue
        for language in ("zh-Hans", "zh-CN", "zh", "en"):
            entries = captions.get(language)
            if isinstance(entries, list) and entries:
                urls = [str(entry.get("url", "")) for entry in entries if isinstance(entry, dict) and entry.get("url")]
                if urls:
                    return "\n".join(urls)
    return ""


def _load_netscape_cookies(path: Path) -> list[dict[str, object]]:
    cookies: list[dict[str, object]] = []
    if not path.exists():
        return cookies
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            continue
        domain, include_subdomains, cookie_path, secure, expires, name, value = parts
        if not name:
            continue
        cookie: dict[str, object] = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": cookie_path,
            "secure": secure.upper() == "TRUE",
            "httpOnly": False,
        }
        if expires.isdigit() and int(expires) > 0:
            cookie["expires"] = int(expires)
        cookies.append(cookie)
    return cookies


def _extract_section(text: str, heading: str, stop_markers: list[str]) -> str:
    idx = text.find(heading)
    if idx < 0:
        return ""
    chunk = text[idx + len(heading) :]
    stops = [chunk.find(marker) for marker in stop_markers if chunk.find(marker) >= 0]
    if stops:
        chunk = chunk[: min(stops)]
    return chunk.strip()


def _parse_browser_html(html: str) -> dict[str, str]:
    parser = _MetaParser()
    parser.feed(html)
    meta = dict(parser.meta)
    if parser.title:
        meta["title"] = parser.title.strip()
    return meta


def _fetch_douyin_browser_content(url: str, cookies: Path | None) -> ContentResult:
    chrome_path = infer_browser_chrome_path()
    if not chrome_path or cookies is None:
        return ContentResult(ok=False, canonical_url=normalize_video_url(url), error="browser fetch unavailable")
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return ContentResult(ok=False, canonical_url=normalize_video_url(url), error=f"playwright unavailable: {exc}")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                executable_path=chrome_path,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/136.0.0.0 Safari/537.36"
                ),
                locale="zh-CN",
                viewport={"width": 1440, "height": 1600},
            )
            context.add_cookies(_load_netscape_cookies(cookies))
            page = context.new_page()
            page.goto(normalize_video_url(url), wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(12000)
            html = page.content()
            text = page.locator("body").inner_text(timeout=10000)
            browser.close()
    except Exception as exc:  # noqa: BLE001
        return ContentResult(ok=False, canonical_url=normalize_video_url(url), error=f"browser fetch failed: {exc}")

    meta = _parse_browser_html(html)
    title = meta.get("lark:url:video_title") or meta.get("title") or ""
    description = meta.get("description", "")
    author = ""
    if description:
        author_match = re.search(r"-\s*([^\n]+?)于\d{8}发布在抖音", description)
        if author_match:
            author = author_match.group(1).strip()
    chapter_outline = _extract_section(
        text,
        "章节要点",
        ["全部评论", "推荐视频", "喜欢这个视频", "分享", "复制链接"],
    )
    if not chapter_outline:
        chapter_outline = _extract_section(text, "视频内容", ["全部评论", "推荐视频", "分享", "复制链接"])
    return ContentResult(
        ok=True,
        title=title,
        author=author,
        description=description or meta.get("lark:url:video_title", ""),
        transcript=chapter_outline,
        canonical_url=meta.get("lark:url:video_iframe_url", normalize_video_url(url)),
        raw={"meta": meta, "body_text": text},
    )


def _fetch_douyin_bilinote_content(
    url: str,
    cookies: Path | None,
    remote_transcribe: bool = False,
    state_root: Path | None = None,
    transcribe_model: str = "",
) -> ContentResult:
    api_result = fetch_aweme_detail(url, cookies=cookies)
    if not api_result.ok or not api_result.data:
        return ContentResult(ok=False, canonical_url=normalize_video_url(url), error=api_result.error)
    fields = extract_content_fields(api_result.data)
    if not fields:
        return ContentResult(ok=False, canonical_url=normalize_video_url(url), error="BiliNote returned empty content fields", raw=api_result.data)
    transcript = str(fields.get("transcript") or "")
    remote_error = ""
    if remote_transcribe:
        item_id = str(fields.get("item_id") or "")
        cache_root = (state_root or Path(os.getenv("FLOW_STATE_ROOT", "state"))) / "media_cache"
        transcribed = transcribe_remote_audio(
            str(fields.get("audio_url") or ""),
            cache_root=cache_root,
            item_id=item_id or "douyin_audio",
            title=str(fields.get("title") or ""),
            model=transcribe_model,
        )
        if transcribed.ok:
            remote_section = f"### 远端逐字稿\n{transcribed.text.strip()}"
            transcript = f"{remote_section}\n\n{transcript}".strip()
            fields["remote_transcription"] = {
                "ok": True,
                "model": transcribed.model,
                "audio_path": transcribed.audio_path,
            }
        else:
            remote_error = transcribed.error
            fields["remote_transcription"] = {
                "ok": False,
                "error": transcribed.error,
                "audio_path": transcribed.audio_path,
            }
            if not transcript.strip():
                transcript = f"### 远端转写状态\n{transcribed.error}"
    return ContentResult(
        ok=True,
        title=str(fields.get("title") or ""),
        author=str(fields.get("author") or ""),
        description=str(fields.get("description") or ""),
        transcript=transcript,
        canonical_url=str(fields.get("canonical_url") or normalize_video_url(url)),
        published=str(fields.get("published") or ""),
        error=remote_error,
        raw={"provider": "bilinote", "fields": fields, "response": api_result.data},
    )


def fetch_video_content(
    url: str,
    cookies: Path | None = None,
    cookies_from_browser: str = "",
    remote_transcribe: bool = False,
    state_root: Path | None = None,
    transcribe_model: str = "",
) -> ContentResult:
    host = urlparse(url).netloc.lower()
    if "douyin.com" in host:
        if cookies:
            bilinote_result = _fetch_douyin_bilinote_content(
                url,
                cookies,
                remote_transcribe=remote_transcribe,
                state_root=state_root,
                transcribe_model=transcribe_model,
            )
            if bilinote_result.ok:
                return bilinote_result
            browser_result = _fetch_douyin_browser_content(url, cookies)
            if browser_result.ok and browser_result.has_content:
                return browser_result
    elif cookies and "xiaohongshu.com" in host:
        browser_result = _fetch_douyin_browser_content(url, cookies)
        if browser_result.ok and browser_result.has_content:
            return browser_result
    code, stdout, stderr = _run_ytdlp(url, cookies=cookies, cookies_from_browser=cookies_from_browser)
    if code != 0:
        return ContentResult(ok=False, canonical_url=normalize_video_url(url), error=stderr.strip() or stdout.strip())
    text = stdout.strip()
    if not text or text == "null":
        return ContentResult(ok=False, canonical_url=normalize_video_url(url), error=stderr.strip() or "yt-dlp returned no metadata")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return ContentResult(ok=False, canonical_url=normalize_video_url(url), error=f"yt-dlp JSON parse failed: {exc}")
    title = str(data.get("title") or "")
    author = str(data.get("uploader") or data.get("creator") or data.get("channel") or "")
    description = str(data.get("description") or "")
    transcript = _pick_caption(data)
    canonical_url = str(data.get("webpage_url") or data.get("original_url") or normalize_video_url(url))
    return ContentResult(
        ok=True,
        title=title,
        author=author,
        description=description,
        transcript=transcript,
        canonical_url=canonical_url,
        raw=data,
    )


def load_sidecar_transcript(source_path: Path) -> str:
    candidates = [
        source_path.with_suffix(".transcript.md"),
        source_path.with_suffix(".transcript.txt"),
        source_path.with_suffix(".srt"),
        source_path.with_suffix(".vtt"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.read_text(encoding="utf-8", errors="ignore")
    return ""
