from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen


WORKSPACE = Path(os.getenv("BILINOTE_WORKSPACE", str(Path(__file__).resolve().parents[1])))
BILINOTE_ROOT = Path(
    os.getenv("BILINOTE_ROOT", str(WORKSPACE / "_deps" / "BiliNote-src" / "BiliNote-master"))
)
BILINOTE_PYTHON = BILINOTE_ROOT / ".venv" / "Scripts" / "python.exe"
BILINOTE_BACKEND = "http://127.0.0.1:8483"
STATE_ROOT = Path(os.getenv("BILINOTE_STATE_ROOT", str(WORKSPACE / "state")))
VAULT_ROOT = Path(os.getenv("BILINOTE_VAULT_ROOT", str(WORKSPACE / "vault")))
INBOX_MEDIA_ROOT = VAULT_ROOT / "00 Inbox" / "Media"
SOURCE_MEDIA_ROOT = VAULT_ROOT / "10 Sources" / "Media"
ASSET_ROOT = VAULT_ROOT / "99 Assets" / "BiliNote"
DAILY_LINKS = Path(os.getenv("BILINOTE_DAILY_LINKS", str(WORKSPACE / "queue" / "bilinote_daily_links.md")))
DASHBOARD_GUIDE = VAULT_ROOT / "35 Dashboards" / "BiliNote Knowledge Flow.md"
MANIFEST_PATH = STATE_ROOT / "bilinote_manifest.json"

URL_RE = re.compile(r"https?://[^\s<>\]\)\"']+", re.IGNORECASE)
MAX_SCREENSHOTS_PER_NOTE = int(os.getenv("BILINOTE_MAX_SCREENSHOTS", "12"))
MIN_SCREENSHOT_GAP_SECONDS = int(os.getenv("BILINOTE_MIN_SCREENSHOT_GAP_SECONDS", "30"))


def ensure_daily_links_file() -> None:
    DAILY_LINKS.parent.mkdir(parents=True, exist_ok=True)
    if DAILY_LINKS.exists():
        return
    DAILY_LINKS.write_text(
        "\n".join(
            [
                "# BiliNote 每日链接入口",
                "",
                "把当天要处理的抖音 / 小红书链接粘到这里，一行一个。",
                "",
                f"- 文件地址：`{DAILY_LINKS}`",
                "- Obsidian 内链：[[35 Dashboards/bilinote_daily_links]]",
                "- 工作流说明：[[35 Dashboards/BiliNote 知识流]]",
                "",
                "脚本会读取这个文件，先写入 `00 Inbox/Media` 作为原始归档，再写入 `10 Sources/Media` 作为结构化 Source note。",
                f"已处理的链接会被 `{MANIFEST_PATH}` 跳过。",
                "",
                "## Today",
                "",
            ]
        ),
        encoding="utf-8",
    )


def ensure_dashboard_guide() -> None:
    DASHBOARD_GUIDE.parent.mkdir(parents=True, exist_ok=True)
    if DASHBOARD_GUIDE.exists():
        return
    DASHBOARD_GUIDE.write_text(
        "\n".join(
            [
                "---",
                "type: dashboard",
                "status: active",
                f"created: {datetime.now().date().isoformat()}",
                "---",
                "",
                "# BiliNote 知识流",
                "",
                "## 每日入口",
                "- 日链接文件：[[35 Dashboards/bilinote_daily_links]]",
                f"- 文件地址：`{DAILY_LINKS}`",
                "",
                "## Inbox 与 Source 的区别",
                "- `00 Inbox/Media`：原始投递、处理记录、本地转写文件、任务号、失败排查线索。",
                "- `10 Sources/Media`：面向阅读和复盘的结构化总结，包含截图、章节、要点、术语、可执行启发和回链。",
                "- `99 Assets/BiliNote`：BiliNote 生成或补截的图片资产，Source note 会直接嵌入。",
                "",
                "## 日常用法",
                "1. 打开 [[35 Dashboards/bilinote_daily_links]]。",
                "2. 在 `## Today` 下方粘贴链接，一行一个。",
                f"3. 自动化或手动运行 `python {WORKSPACE / 'scripts' / 'bilinote_workflow.py'} run`。",
                "4. 阅读 `10 Sources/Media` 中生成的 Source note。",
                "",
                "## 维护",
                f"- 去重与处理状态：`{MANIFEST_PATH}`",
                f"- 清理候选报告：`python {WORKSPACE / 'scripts' / 'bilinote_workflow.py'} cleanup-plan --days 7`",
                "- 清理报告只列候选文件，不自动删除。",
                "",
            ]
        ),
        encoding="utf-8",
    )


def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def vault_wiki_link(path: Path) -> str:
    rel = path.relative_to(VAULT_ROOT).as_posix()
    if rel.lower().endswith(".md"):
        rel = rel[:-3]
    return f"[[{rel}]]"


def request_json(method: str, path: str, payload: dict | None = None, timeout: int = 30) -> dict:
    url = f"{BILINOTE_BACKEND}{path}"
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=headers, method=method)
    with urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def backend_is_alive() -> bool:
    try:
        res = request_json("GET", "/api/sys_health", timeout=5)
        return res.get("code") == 0
    except Exception:
        return False


def start_backend() -> None:
    if backend_is_alive():
        return
    logs = BILINOTE_ROOT / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    stdout = (logs / "backend.workflow.out.log").open("a", encoding="utf-8")
    stderr = (logs / "backend.workflow.err.log").open("a", encoding="utf-8")
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("FFMPEG_BIN_PATH", str(BILINOTE_ROOT / "tools" / "ffmpeg"))
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    subprocess.Popen(
        [str(BILINOTE_PYTHON), "backend\\main.py"],
        cwd=str(BILINOTE_ROOT),
        stdout=stdout,
        stderr=stderr,
        env=env,
        creationflags=creationflags,
    )
    for _ in range(30):
        if backend_is_alive():
            return
        time.sleep(1)
    raise RuntimeError("BiliNote backend did not become healthy on http://127.0.0.1:8483")


def extract_urls(text: str) -> list[str]:
    seen = set()
    urls = []
    for match in URL_RE.finditer(text):
        url = match.group(0).rstrip(".,;:!?)]\"'")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def infer_platform(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "douyin.com" in host:
        return "douyin"
    if "xiaohongshu.com" in host or "xhslink.com" in host:
        return "xiaohongshu"
    return ""


def item_key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


def get_default_model() -> tuple[str, str]:
    res = request_json("GET", "/api/model_list")
    models = res.get("data") or []
    if not models:
        raise RuntimeError("No BiliNote model configured. Add one in the BiliNote UI first.")
    model = models[0]
    return str(model["provider_id"]), str(model["model_name"])


def submit_note(url: str, platform: str, provider_id: str, model_name: str) -> str:
    payload = {
        "video_url": url,
        "platform": platform,
        "quality": "fast",
        "model_name": model_name,
        "provider_id": provider_id,
        "format": ["link", "screenshot", "summary"],
        "style": "knowledge_base",
        "extras": "",
        "video_understanding": True,
        "video_interval": 20,
        "grid_size": [2, 2],
    }
    res = request_json("POST", "/api/generate_note", payload=payload, timeout=60)
    if res.get("code") != 0:
        raise RuntimeError(f"BiliNote submit failed: {res.get('msg')}")
    return str((res.get("data") or {})["task_id"])


def wait_task(task_id: str, timeout_seconds: int = 1800) -> None:
    deadline = time.time() + timeout_seconds
    last_status = ""
    while time.time() < deadline:
        res = request_json("GET", f"/api/task_status/{task_id}", timeout=30)
        if res.get("code") != 0:
            raise RuntimeError(f"BiliNote task failed: {res.get('msg')}")
        data = res.get("data") or {}
        status = str(data.get("status") or "")
        if status and status != last_status:
            print(f"{task_id}: {status}")
            last_status = status
        if status == "SUCCESS":
            return
        if status == "FAILED":
            raise RuntimeError(f"BiliNote task failed: {data.get('message') or res.get('msg')}")
        time.sleep(10)
    raise TimeoutError(f"BiliNote task timed out: {task_id}")


def sanitize_filename(value: str, fallback: str = "untitled") -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', " ", value).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:120] or fallback


def yaml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def build_frontmatter(fields: dict[str, object]) -> str:
    lines = ["---"]
    for key, value in fields.items():
        if isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {yaml_quote(str(item))}")
        elif value is None or value == "":
            lines.append(f"{key}:")
        else:
            lines.append(f"{key}: {yaml_quote(str(value))}")
    lines.append("---")
    return "\n".join(lines)


def read_task_result(task_id: str) -> tuple[str, dict]:
    md_path = BILINOTE_ROOT / "note_results" / f"{task_id}_markdown.md"
    json_path = BILINOTE_ROOT / "note_results" / f"{task_id}.json"
    if not md_path.exists():
        raise FileNotFoundError(f"BiliNote markdown result missing: {md_path}")
    markdown = md_path.read_text(encoding="utf-8", errors="ignore").strip()
    data = load_json(json_path, {}) if json_path.exists() else {}
    return markdown, data


def resolve_ffmpeg_bin() -> str:
    env_path = os.getenv("FFMPEG_BIN_PATH", str(BILINOTE_ROOT / "tools" / "ffmpeg"))
    if env_path.lower().endswith(".exe") and Path(env_path).exists():
        return env_path
    candidate = Path(env_path) / "ffmpeg.exe"
    if candidate.exists():
        return str(candidate)
    return "ffmpeg"


def render_screenshot(video_path: Path, target: Path, seconds: int) -> bool:
    if target.exists():
        return True
    if not video_path.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            resolve_ffmpeg_bin(),
            "-ss",
            str(seconds),
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(target),
            "-y",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0 and target.exists()


def file_sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def should_keep_screenshot(
    kept: list[tuple[int | None, str | None]],
    *,
    seconds: int | None = None,
    digest: str | None = None,
    max_count: int = MAX_SCREENSHOTS_PER_NOTE,
    min_gap_seconds: int = MIN_SCREENSHOT_GAP_SECONDS,
) -> bool:
    if max_count >= 0 and len(kept) >= max_count:
        return False
    if digest and any(existing_digest == digest for _, existing_digest in kept):
        return False
    if seconds is not None:
        for existing_seconds, _ in kept:
            if existing_seconds is not None and abs(existing_seconds - seconds) < min_gap_seconds:
                return False
    kept.append((seconds, digest))
    return True


def strip_screenshots(markdown: str) -> str:
    image_pattern = re.compile(r"^\s*!\[\]\((?:/static/screenshots/|https?://[^)]+/static/screenshots/)[^)]+\)\s*$", re.MULTILINE)
    marker_pattern = re.compile(r"\*?Screenshot-\[\s*\d{2}:\d{2}\s*\]\*?", re.MULTILINE)
    markdown = image_pattern.sub("", markdown)
    markdown = marker_pattern.sub("", markdown)
    return re.sub(r"\n{3,}", "\n\n", markdown).strip()


def copy_and_rewrite_images(markdown: str, task_id: str, title: str, date_text: str, item_id: str = "") -> str:
    screenshot_root = BILINOTE_ROOT / "static" / "screenshots"
    asset_dir = ASSET_ROOT / date_text / f"{sanitize_filename(title)}_{task_id[:8]}"
    asset_dir.mkdir(parents=True, exist_ok=True)
    image_pattern = re.compile(r"!\[\]\((?:/static/screenshots/|https?://[^)]+/static/screenshots/)([^)]+)\)")
    kept: list[tuple[int | None, str | None]] = []

    def image_repl(match: re.Match[str]) -> str:
        filename = match.group(1)
        source = screenshot_root / filename
        target = asset_dir / filename
        if not source.exists():
            return ""
        digest = file_sha1(source)
        if not should_keep_screenshot(kept, digest=digest):
            return ""
        if source.exists() and not target.exists():
            shutil.copy2(source, target)
        if target.exists():
            return f"![[{target.relative_to(VAULT_ROOT).as_posix()}]]"
        return match.group(0)

    markdown = image_pattern.sub(image_repl, markdown)

    video_path = BILINOTE_ROOT / "backend" / "data" / "data" / f"{item_id}.mp4" if item_id else Path("")
    marker_pattern = re.compile(r"\*?Screenshot-\[\s*(\d{2}):(\d{2})\s*\]\*?", re.MULTILINE)

    def marker_repl(match: re.Match[str]) -> str:
        mm, ss = match.group(1), match.group(2)
        seconds = int(mm) * 60 + int(ss)
        if not should_keep_screenshot(kept, seconds=seconds):
            return ""
        filename = f"screenshot_{mm}_{ss}.jpg"
        target = asset_dir / filename
        if render_screenshot(video_path, target, seconds):
            return f"![[{target.relative_to(VAULT_ROOT).as_posix()}]]"
        return match.group(0)

    markdown = marker_pattern.sub(marker_repl, markdown)
    return re.sub(r"\n{3,}", "\n\n", markdown).strip()


def extract_title(markdown: str, fallback: str) -> str:
    match = re.search(r"^#\s+(.+)$", markdown, re.MULTILINE)
    return match.group(1).strip() if match else fallback


def strip_leading_h1(markdown: str) -> str:
    lines = markdown.strip().splitlines()
    if lines and re.match(r"^#\s+", lines[0]):
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines = lines[1:]
    return "\n".join(lines).strip()


def resolve_note_title_and_paths(url: str, platform: str, task_id: str) -> tuple[str, str, Path]:
    markdown, result = read_task_result(task_id)
    audio_meta = result.get("audio_meta") or {}
    item_id = str(audio_meta.get("video_id") or "")
    title = extract_title(markdown, str(audio_meta.get("title") or f"{platform} video {item_id or task_id}"))
    date_text = datetime.now().date().isoformat()
    target = find_note_by_source_url(SOURCE_MEDIA_ROOT, url)
    if not target:
        filename = f"{date_text} {sanitize_filename(title)}.md"
        target = SOURCE_MEDIA_ROOT / filename
        if target.exists():
            target = SOURCE_MEDIA_ROOT / f"{date_text} {sanitize_filename(title)} {task_id[:8]}.md"
    return markdown, date_text, target


def write_source_note_legacy(url: str, platform: str, task_id: str, model_name: str, intake_path: Path) -> Path:
    markdown, result = read_task_result(task_id)
    audio_meta = result.get("audio_meta") or {}
    transcript = result.get("transcript") or {}
    item_id = str(audio_meta.get("video_id") or "")
    title = extract_title(markdown, str(audio_meta.get("title") or f"{platform} video {item_id or task_id}"))
    date_text = datetime.now().date().isoformat()
    text_only_markdown = strip_screenshots(markdown)
    summary_body = strip_leading_h1(text_only_markdown) or text_only_markdown.strip()
    asset_dir = ASSET_ROOT / date_text / f"{sanitize_filename(title)}_{task_id[:8]}"
    target = find_note_by_source_url(SOURCE_MEDIA_ROOT, url)
    if not target:
        filename = f"{date_text} {sanitize_filename(title)}.md"
        target = SOURCE_MEDIA_ROOT / filename
        if target.exists():
            target = SOURCE_MEDIA_ROOT / f"{date_text} {sanitize_filename(title)} {task_id[:8]}.md"

    fm = build_frontmatter(
        {
            "type": "source",
            "platform": platform,
            "medium": "video",
            "status": "summarized",
            "source_url": url,
            "item_id": item_id,
            "title": title,
            "author": "",
            "captured": date_text,
            "language": "zh",
            "reviewed": False,
            "source_inbox": vault_wiki_link(intake_path),
            "source_queue": vault_wiki_link(DAILY_LINKS),
            "summary_tool": "BiliNote",
            "summary_style": "knowledge_base",
            "summary_model": model_name,
            "bilinote_task_id": task_id,
            "local_bilinote_markdown": str(BILINOTE_ROOT / "note_results" / f"{task_id}_markdown.md"),
            "local_bilinote_transcript": str(BILINOTE_ROOT / "note_results" / f"{task_id}_transcript.json"),
            "asset_dir": str(asset_dir),
        }
    )
    body = "\n".join(
        [
            fm,
            "",
            f"# {title}",
            "",
            "> [!info] Source note",
            f"> 原始投递记录和处理证据保留在 {vault_wiki_link(intake_path)}。",
            "",
            "## Source Context",
            f"- 原始链接：{url}",
            f"- Inbox 回链：{vault_wiki_link(intake_path)}",
            f"- 队列入口：{vault_wiki_link(DAILY_LINKS)}",
            f"- 图片资产：`{asset_dir}`",
            "",
            "## AI 摘要",
            "",
            summary_body,
            "",
            "## Transcript Reference",
            f"- BiliNote transcript file: `{BILINOTE_ROOT / 'note_results' / f'{task_id}_transcript.json'}`",
            f"- Transcript language: `{transcript.get('language', '')}`",
            "",
            "## Local Processing",
            f"- BiliNote task: `{task_id}`",
            f"- BiliNote markdown: `{BILINOTE_ROOT / 'note_results' / f'{task_id}_markdown.md'}`",
            f"- BiliNote transcript: `{BILINOTE_ROOT / 'note_results' / f'{task_id}_transcript.json'}`",
            f"- Source inbox: {vault_wiki_link(intake_path)}",
            "",
            "## Notes",
            "- 图片已复制到知识库资产目录并在正文中嵌入。",
            "- 如果只想快速扫结论，看 Source Context 和 AI 摘要即可。",
            "",
        ]
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return target


def write_source_note(url: str, platform: str, task_id: str, model_name: str, intake_path: Path) -> Path:
    markdown, result = read_task_result(task_id)
    audio_meta = result.get("audio_meta") or {}
    transcript = result.get("transcript") or {}
    item_id = str(audio_meta.get("video_id") or "")
    title = extract_title(markdown, str(audio_meta.get("title") or f"{platform} video {item_id or task_id}"))
    date_text = datetime.now().date().isoformat()
    text_only_markdown = strip_screenshots(markdown)
    summary_body = strip_leading_h1(text_only_markdown) or text_only_markdown.strip()
    target = find_note_by_source_url(SOURCE_MEDIA_ROOT, url)
    if not target:
        filename = f"{date_text} {sanitize_filename(title)}.md"
        target = SOURCE_MEDIA_ROOT / filename
        if target.exists():
            target = SOURCE_MEDIA_ROOT / f"{date_text} {sanitize_filename(title)} {task_id[:8]}.md"

    fm = build_frontmatter(
        {
            "type": "source",
            "platform": platform,
            "medium": "video",
            "status": "summarized",
            "source_url": url,
            "item_id": item_id,
            "title": title,
            "author": "",
            "captured": date_text,
            "language": "zh",
            "reviewed": False,
            "source_inbox": vault_wiki_link(intake_path),
            "source_queue": vault_wiki_link(DAILY_LINKS),
            "summary_tool": "BiliNote",
            "summary_style": "knowledge_base",
            "summary_model": model_name,
            "bilinote_task_id": task_id,
            "local_bilinote_markdown": str(BILINOTE_ROOT / "note_results" / f"{task_id}_markdown.md"),
            "local_bilinote_transcript": str(BILINOTE_ROOT / "note_results" / f"{task_id}_transcript.json"),
        }
    )
    body = "\n".join(
        [
            fm,
            "",
            f"# {title}",
            "",
            "## Source Context",
            f"- Original URL: {url}",
            f"- Inbox link: {vault_wiki_link(intake_path)}",
            f"- Queue link: {vault_wiki_link(DAILY_LINKS)}",
            "",
            "## First Pass Summary",
            "",
            summary_body,
            "",
            "## Transcript Reference",
            f"- Transcript file: `{BILINOTE_ROOT / 'note_results' / f'{task_id}_transcript.json'}`",
            f"- Transcript language: `{transcript.get('language', '')}`",
            "",
            "## Notes",
            "- Source keeps the first reading pass and the links needed to revisit Inbox.",
            "- Screenshots are retained in Inbox when they pass the filter.",
            "",
        ]
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return target


def find_note_by_source_url(root: Path, url: str) -> Path | None:
    if not root.exists():
        return None
    for path in root.rglob("*.md"):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if f"source_url: {json.dumps(url, ensure_ascii=False)}" in text or f"source_url: \"{url}\"" in text:
            return path
    return None


def write_intake_note_legacy(url: str, platform: str, task_id: str, model_name: str, source_path: Path) -> Path:
    _, result = read_task_result(task_id)
    audio_meta = result.get("audio_meta") or {}
    item_id = str(audio_meta.get("video_id") or "")
    title = str(audio_meta.get("title") or f"{platform} video {item_id or task_id}")
    date_text = datetime.now().date().isoformat()
    target = find_note_by_source_url(INBOX_MEDIA_ROOT, url)
    if not target:
        filename = f"{date_text} {sanitize_filename(title)}.md"
        target = INBOX_MEDIA_ROOT / filename
        if target.exists():
            target = INBOX_MEDIA_ROOT / f"{date_text} {sanitize_filename(title)} {task_id[:8]}.md"
    fm = build_frontmatter(
        {
            "type": "intake",
            "platform": platform,
            "medium": "video",
            "status": "captured",
            "source_url": url,
            "item_id": item_id,
            "title": title,
            "captured": date_text,
            "language": "zh",
            "reviewed": False,
            "source_queue": vault_wiki_link(DAILY_LINKS),
            "source_note": vault_wiki_link(source_path),
            "summary_tool": "BiliNote",
            "summary_style": "knowledge_base",
            "summary_model": model_name,
            "bilinote_task_id": task_id,
            "local_bilinote_markdown": str(BILINOTE_ROOT / "note_results" / f"{task_id}_markdown.md"),
            "local_bilinote_transcript": str(BILINOTE_ROOT / "note_results" / f"{task_id}_transcript.json"),
        }
    )
    body = "\n".join(
        [
            fm,
            "",
            "# 原始归档",
            "",
            "## 原始链接",
            f"- {url}",
            "",
            "## 处理记录",
            f"- BiliNote task: `{task_id}`",
            f"- Source note: {vault_wiki_link(source_path)}",
            f"- 转写文件: `{BILINOTE_ROOT / 'note_results' / f'{task_id}_transcript.json'}`",
            f"- 原始 Markdown: `{BILINOTE_ROOT / 'note_results' / f'{task_id}_markdown.md'}`",
            "",
            "## 说明",
            "- 这里只保留投递记录和处理痕迹，不复制完整总结。",
            "- 详细总结请看 Source note。",
            "- 如果后续要追查处理过程，先看 manifest 和本页。",
            "",
        ]
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return target


def write_intake_note(url: str, platform: str, task_id: str, model_name: str, source_path: Path) -> Path:
    markdown, result = read_task_result(task_id)
    audio_meta = result.get("audio_meta") or {}
    transcript = result.get("transcript") or {}
    item_id = str(audio_meta.get("video_id") or "")
    title = extract_title(markdown, str(audio_meta.get("title") or f"{platform} video {item_id or task_id}"))
    date_text = datetime.now().date().isoformat()
    markdown = copy_and_rewrite_images(markdown, task_id, title, date_text, item_id=item_id)
    first_pass_body = strip_leading_h1(markdown) or markdown.strip()
    asset_dir = ASSET_ROOT / date_text / f"{sanitize_filename(title)}_{task_id[:8]}"
    target = find_note_by_source_url(INBOX_MEDIA_ROOT, url)
    if not target:
        filename = f"{date_text} {sanitize_filename(title)}.md"
        target = INBOX_MEDIA_ROOT / filename
        if target.exists():
            target = INBOX_MEDIA_ROOT / f"{date_text} {sanitize_filename(title)} {task_id[:8]}.md"
    fm = build_frontmatter(
        {
            "type": "intake",
            "platform": platform,
            "medium": "video",
            "status": "captured",
            "source_url": url,
            "item_id": item_id,
            "title": title,
            "captured": date_text,
            "language": "zh",
            "reviewed": False,
            "source_queue": vault_wiki_link(DAILY_LINKS),
            "source_note": vault_wiki_link(source_path),
            "summary_tool": "BiliNote",
            "summary_style": "knowledge_base",
            "summary_model": model_name,
            "bilinote_task_id": task_id,
            "local_bilinote_markdown": str(BILINOTE_ROOT / "note_results" / f"{task_id}_markdown.md"),
            "local_bilinote_transcript": str(BILINOTE_ROOT / "note_results" / f"{task_id}_transcript.json"),
            "asset_dir": str(asset_dir),
        }
    )
    body = "\n".join(
        [
            fm,
            "",
            f"# {title}",
            "",
            "## Source Link",
            f"- {url}",
            "",
            "## Processing Record",
            f"- BiliNote task: `{task_id}`",
            f"- Source note: {vault_wiki_link(source_path)}",
            f"- BiliNote markdown: `{BILINOTE_ROOT / 'note_results' / f'{task_id}_markdown.md'}`",
            f"- BiliNote transcript: `{BILINOTE_ROOT / 'note_results' / f'{task_id}_transcript.json'}`",
            f"- Screenshot assets: `{asset_dir}`",
            "",
            "## BiliNote First Pass",
            "",
            first_pass_body,
            "",
            "## Transcript Reference",
            f"- Transcript file: `{BILINOTE_ROOT / 'note_results' / f'{task_id}_transcript.json'}`",
            f"- Transcript language: `{transcript.get('language', '')}`",
            "",
            "## Notes",
            "- Inbox keeps the fuller first-pass capture and processing evidence.",
            "- Source keeps the lighter first-round reading note and links back here.",
            "",
        ]
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return target


def run_links(args: argparse.Namespace) -> int:
    ensure_daily_links_file()
    ensure_dashboard_guide()
    manifest = load_json(MANIFEST_PATH, {"items": {}})
    links = list(args.url or [])
    if args.input.exists():
        links.extend(extract_urls(args.input.read_text(encoding="utf-8", errors="ignore")))
    pending: list[tuple[str, str, str]] = []
    for url in links:
        platform = infer_platform(url)
        if not platform:
            print(f"skip unsupported url: {url}")
            continue
        key = item_key(url)
        if key in manifest.get("items", {}) and not args.force:
            print(f"skip already processed: {url}")
            continue
        pending.append((url, platform, key))
    if not pending:
        print("done: 0")
        return 0

    start_backend()
    provider_id = args.provider_id
    model_name = args.model_name
    if not provider_id or not model_name:
        provider_id, model_name = get_default_model()

    total = 0
    errors = 0
    for url, platform, key in pending:
        try:
            print(f"submit: {url}")
            task_id = submit_note(url, platform, provider_id, model_name)
            wait_task(task_id, timeout_seconds=args.timeout)
            _, _, source_path = resolve_note_title_and_paths(url, platform, task_id)
            intake_path = write_intake_note(url, platform, task_id, model_name, source_path)
            source_note = write_source_note(url, platform, task_id, model_name, intake_path)
            manifest["items"][key] = {
                "url": url,
                "platform": platform,
                "task_id": task_id,
                "intake_note": str(intake_path),
                "source_note": str(source_note),
                "processed_at": datetime.now().isoformat(timespec="seconds"),
            }
            save_json(MANIFEST_PATH, manifest)
            print(f"wrote: {intake_path}")
            print(f"wrote: {source_note}")
            total += 1
        except Exception as exc:
            errors += 1
            manifest.setdefault("errors", {})[key] = {
                "url": url,
                "platform": platform,
                "error": str(exc),
                "failed_at": datetime.now().isoformat(timespec="seconds"),
            }
            save_json(MANIFEST_PATH, manifest)
            print(f"error: {url} -> {exc}")
    print(f"done: {total}")
    if errors:
        print(f"errors: {errors}")
    return 0


def cleanup_plan(args: argparse.Namespace) -> int:
    cutoff = datetime.now() - timedelta(days=args.days)
    roots = [
        BILINOTE_ROOT / "backend" / "data" / "data",
        BILINOTE_ROOT / "backend" / "data" / "output_frames",
        BILINOTE_ROOT / "backend" / "data" / "grid_output",
        BILINOTE_ROOT / "note_results",
        BILINOTE_ROOT / "static" / "screenshots",
    ]
    candidates: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
                candidates.append(path)
    report = STATE_ROOT / f"bilinote_cleanup_candidates_{datetime.now().date().isoformat()}.txt"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(str(path) for path in sorted(candidates)), encoding="utf-8")
    size = sum(path.stat().st_size for path in candidates if path.exists())
    print(f"cleanup candidates: {len(candidates)} files, {size / 1024 / 1024:.1f} MB")
    print(f"report: {report}")
    print("No files were deleted. Batch deletion is disabled by workspace rules.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bilinote_workflow")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run")
    run.add_argument("--input", type=Path, default=DAILY_LINKS)
    run.add_argument("--url", action="append", default=[])
    run.add_argument("--provider-id", default="")
    run.add_argument("--model-name", default="")
    run.add_argument("--timeout", type=int, default=1800)
    run.add_argument("--force", action="store_true")

    sub.add_parser("init")

    cleanup = sub.add_parser("cleanup-plan")
    cleanup.add_argument("--days", type=int, default=7)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "init" or not args.command:
        ensure_daily_links_file()
        ensure_dashboard_guide()
        print(f"daily input: {DAILY_LINKS}")
        return 0
    if args.command == "run":
        return run_links(args)
    if args.command == "cleanup-plan":
        return cleanup_plan(args)
    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
