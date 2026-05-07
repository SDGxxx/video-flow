from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse
import importlib.util
import json
import re

import requests


DOUYIN_DOMAIN = "https://www.douyin.com"
CN_TIMEZONE = timezone(timedelta(hours=8))
BILINOTE_ROOT = Path(__file__).resolve().parents[1] / "_deps" / "BiliNote-src" / "BiliNote-master"
BILINOTE_ABOGUS = BILINOTE_ROOT / "backend" / "app" / "downloaders" / "douyin_helper" / "abogus.py"
BILINOTE_DOWNLOADER = BILINOTE_ROOT / "backend" / "app" / "downloaders" / "douyin_downloader.py"


@dataclass(slots=True)
class DouyinApiResult:
    ok: bool
    data: dict[str, object] | None = None
    error: str = ""


def is_available() -> bool:
    return BILINOTE_ABOGUS.exists() and BILINOTE_DOWNLOADER.exists()


def _load_abogus_class() -> type:
    if not BILINOTE_ABOGUS.exists():
        raise FileNotFoundError(f"BiliNote ABogus not found: {BILINOTE_ABOGUS}")
    spec = importlib.util.spec_from_file_location("bilinote_abogus", BILINOTE_ABOGUS)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load BiliNote ABogus from {BILINOTE_ABOGUS}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ABogus


def _extract_bilinote_ms_payload() -> dict[str, object]:
    source = BILINOTE_DOWNLOADER.read_text(encoding="utf-8", errors="ignore")
    str_data_match = re.search(r'"strData":\s*"([^"]+)"', source)
    magic_match = re.search(r'"magic":\s*(\d+)', source)
    version_match = re.search(r'"version":\s*(\d+)', source)
    data_type_match = re.search(r'"dataType":\s*(\d+)', source)
    if not str_data_match:
        raise ValueError("BiliNote Douyin msToken payload not found")
    return {
        "magic": int(magic_match.group(1)) if magic_match else 538969122,
        "version": int(version_match.group(1)) if version_match else 1,
        "dataType": int(data_type_match.group(1)) if data_type_match else 8,
        "strData": str_data_match.group(1),
    }


def netscape_cookie_header(path: Path, domain_hint: str = "douyin") -> str:
    if not path.exists():
        return ""
    pairs: list[str] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            continue
        domain, _include_subdomains, _cookie_path, _secure, _expires, name, value = parts
        if not name or domain_hint not in domain.lower() or name in seen:
            continue
        seen.add(name)
        pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def extract_aweme_id(url: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key in ("modal_id", "aweme_id", "item_id", "video_id"):
        values = query.get(key)
        if values and values[0]:
            return values[0]
    for pattern in (r"/video/(\d+)", r"/note/(\d+)", r"/share/video/(\d+)"):
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return ""


def _headers(cookie_header: str) -> dict[str, str]:
    headers = {
        "Accept-Language": "zh-CN,zh;q=0.9",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/130.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.douyin.com/",
    }
    if cookie_header:
        headers["Cookie"] = cookie_header
    return headers


def _gen_ms_token(headers: dict[str, str]) -> str:
    payload = _extract_bilinote_ms_payload()
    payload["tspFromClient"] = int(datetime.now(UTC).timestamp() * 1000)
    response = requests.post(
        "https://mssdk.bytedance.com/web/report",
        data=json.dumps(payload, ensure_ascii=False),
        headers={"User-Agent": headers["User-Agent"], "Content-Type": "application/json"},
        timeout=15,
    )
    response.raise_for_status()
    token = response.cookies.get("msToken", "")
    if not token:
        raise ValueError("Douyin msToken API returned empty token")
    return token


def _base_params(ms_token: str, aweme_id: str) -> dict[str, object]:
    return {
        "device_platform": "webapp",
        "aid": "6383",
        "channel": "channel_pc_web",
        "pc_client_type": 1,
        "version_code": "290100",
        "version_name": "29.1.0",
        "cookie_enabled": "true",
        "screen_width": 1920,
        "screen_height": 1080,
        "browser_language": "zh-CN",
        "browser_platform": "Win32",
        "browser_name": "Chrome",
        "browser_version": "130.0.0.0",
        "browser_online": "true",
        "engine_name": "Blink",
        "engine_version": "130.0.0.0",
        "os_name": "Windows",
        "os_version": "10",
        "cpu_core_num": 12,
        "device_memory": 8,
        "platform": "PC",
        "downlink": "10",
        "effective_type": "4g",
        "from_user_page": "1",
        "locate_query": "false",
        "need_time_list": "1",
        "pc_libra_divert": "Windows",
        "publish_video_strategy_type": "2",
        "round_trip_time": "0",
        "show_live_replay_strategy": "1",
        "time_list_query": "0",
        "whale_cut_token": "",
        "update_version_code": "170400",
        "msToken": ms_token,
        "aweme_id": aweme_id,
    }


def fetch_aweme_detail(url: str, cookies: Path | None = None) -> DouyinApiResult:
    if not is_available():
        return DouyinApiResult(ok=False, error="BiliNote source is not available")
    aweme_id = extract_aweme_id(url)
    if not aweme_id:
        return DouyinApiResult(ok=False, error="Douyin aweme id not found")
    try:
        cookie_header = netscape_cookie_header(cookies) if cookies else ""
        headers = _headers(cookie_header)
        ms_token = _gen_ms_token(headers)
        params = _base_params(ms_token, aweme_id)
        ABogus = _load_abogus_class()
        a_bogus = quote(ABogus().get_value(params), safe="")
        api_url = f"{DOUYIN_DOMAIN}/aweme/v1/web/aweme/detail/?{urlencode(params)}&a_bogus={a_bogus}"
        response = requests.get(api_url, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:  # noqa: BLE001
        return DouyinApiResult(ok=False, error=f"BiliNote Douyin API failed: {exc}")
    if not isinstance(data, dict) or not data.get("aweme_detail"):
        return DouyinApiResult(ok=False, data=data if isinstance(data, dict) else None, error="Douyin API returned no aweme_detail")
    return DouyinApiResult(ok=True, data=data)


def format_ms(timestamp_ms: object) -> str:
    try:
        value = int(timestamp_ms)
    except (TypeError, ValueError):
        return "00:00"
    seconds = max(0, value // 1000)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def extract_content_fields(data: dict[str, object]) -> dict[str, object]:
    aweme = data.get("aweme_detail")
    if not isinstance(aweme, dict):
        return {}
    author = aweme.get("author") if isinstance(aweme.get("author"), dict) else {}
    video = aweme.get("video") if isinstance(aweme.get("video"), dict) else {}
    music = aweme.get("music") if isinstance(aweme.get("music"), dict) else {}
    play_url = music.get("play_url") if isinstance(music.get("play_url"), dict) else {}
    cover = video.get("cover_original_scale") if isinstance(video.get("cover_original_scale"), dict) else {}
    tags = []
    for tag in aweme.get("video_tag") or []:
        if isinstance(tag, dict) and tag.get("tag_name"):
            tags.append(str(tag["tag_name"]))

    transcript_sections: list[str] = []
    chapter_abstract = str(aweme.get("chapter_abstract") or "").strip()
    if chapter_abstract:
        transcript_sections.extend(["### 平台章节摘要", chapter_abstract, ""])

    chapter_list = aweme.get("chapter_list") or []
    if isinstance(chapter_list, list) and chapter_list:
        transcript_sections.extend(["### 平台章节 / 正文线索"])
        for chapter in chapter_list:
            if not isinstance(chapter, dict):
                continue
            title = str(chapter.get("desc") or "").strip()
            detail = str(chapter.get("detail") or "").strip()
            stamp = format_ms(chapter.get("timestamp"))
            if detail and title:
                transcript_sections.append(f"- [{stamp}] {title}: {detail}")
            elif title:
                transcript_sections.append(f"- [{stamp}] {title}")
            elif detail:
                transcript_sections.append(f"- [{stamp}] {detail}")
        transcript_sections.append("")

    caption = str(aweme.get("caption") or "").strip()
    if caption:
        transcript_sections.extend(["### 发布文案", caption, ""])
    if tags:
        transcript_sections.extend(["### 平台标签", "、".join(tags), ""])

    create_time = aweme.get("create_time")
    published = ""
    if isinstance(create_time, int) and create_time > 0:
        published = datetime.fromtimestamp(create_time, CN_TIMEZONE).date().isoformat()

    cover_url = ""
    if isinstance(cover.get("url_list"), list) and cover["url_list"]:
        cover_url = str(cover["url_list"][0])
    audio_url = str(play_url.get("uri") or "")
    return {
        "item_id": str(aweme.get("aweme_id") or ""),
        "title": str(aweme.get("item_title") or aweme.get("desc") or "").strip(),
        "author": str(author.get("nickname") or "").strip() if isinstance(author, dict) else "",
        "description": str(aweme.get("desc") or aweme.get("caption") or chapter_abstract or "").strip(),
        "transcript": "\n".join(transcript_sections).strip(),
        "canonical_url": f"https://www.douyin.com/video/{aweme.get('aweme_id') or extract_aweme_id(str(aweme.get('share_url') or ''))}",
        "published": published,
        "duration_ms": aweme.get("duration") or (video.get("duration") if isinstance(video, dict) else ""),
        "audio_url": audio_url,
        "cover_url": cover_url,
        "tags": tags,
        "raw_aweme": aweme,
    }
