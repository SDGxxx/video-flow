from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
import os
import re

import requests


DEFAULT_TRANSCRIBE_MODEL = "gpt-4o-transcribe"
SUPPORTED_AUDIO_SUFFIXES = {".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm"}
OPENAI_AUDIO_LIMIT_BYTES = 25 * 1024 * 1024


@dataclass(slots=True)
class TranscriptionResult:
    ok: bool
    text: str = ""
    model: str = ""
    audio_path: str = ""
    error: str = ""


def load_local_env() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:  # noqa: BLE001
        return
    root = Path(__file__).resolve().parents[1]
    for path in (root / ".env", root / "config" / ".env"):
        if path.exists():
            load_dotenv(path, override=False)


def sanitize_cache_name(value: str, fallback: str = "audio") -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', "_", value).strip(" ._")
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned[:120] or fallback


def _infer_suffix(url: str, content_type: str = "") -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in SUPPORTED_AUDIO_SUFFIXES:
        return suffix
    if "mp4" in content_type:
        return ".mp4"
    if "mpeg" in content_type or "mp3" in content_type:
        return ".mp3"
    if "wav" in content_type:
        return ".wav"
    if "webm" in content_type:
        return ".webm"
    return ".mp3"


def download_audio(audio_url: str, cache_root: Path, item_id: str, headers: dict[str, str] | None = None) -> Path:
    cache_root.mkdir(parents=True, exist_ok=True)
    base = sanitize_cache_name(item_id, "audio")
    existing = sorted(cache_root.glob(f"{base}.*"))
    for path in existing:
        if path.suffix.lower() in SUPPORTED_AUDIO_SUFFIXES and path.stat().st_size > 0:
            return path
    response = requests.get(audio_url, headers=headers or {}, allow_redirects=True, timeout=60)
    response.raise_for_status()
    suffix = _infer_suffix(str(response.url), response.headers.get("Content-Type", ""))
    path = cache_root / f"{base}{suffix}"
    path.write_bytes(response.content)
    return path


def _extract_text(response: object) -> str:
    if isinstance(response, str):
        return response.strip()
    text = getattr(response, "text", "")
    if isinstance(text, str):
        return text.strip()
    if isinstance(response, dict):
        value = response.get("text", "")
        if isinstance(value, str):
            return value.strip()
    return ""


def transcribe_with_openai(audio_path: Path, model: str = "", prompt: str = "") -> TranscriptionResult:
    load_local_env()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return TranscriptionResult(ok=False, audio_path=str(audio_path), error="OPENAI_API_KEY is not set")
    if not audio_path.exists():
        return TranscriptionResult(ok=False, audio_path=str(audio_path), error=f"audio file not found: {audio_path}")
    size = audio_path.stat().st_size
    if size <= 0:
        return TranscriptionResult(ok=False, audio_path=str(audio_path), error=f"audio file is empty: {audio_path}")
    if size > OPENAI_AUDIO_LIMIT_BYTES:
        return TranscriptionResult(
            ok=False,
            audio_path=str(audio_path),
            error=f"audio file is {size / 1024 / 1024:.1f} MB, above OpenAI transcription upload limit of 25 MB",
        )
    try:
        from openai import OpenAI  # type: ignore

        base_url = os.getenv("OPENAI_BASE_URL", "").strip()
        client = OpenAI(api_key=api_key, base_url=base_url or None)
        selected_model = model or os.getenv("X1_TRANSCRIBE_MODEL", "").strip() or DEFAULT_TRANSCRIBE_MODEL
        kwargs: dict[str, object] = {"model": selected_model, "response_format": "text"}
        if prompt and "diarize" not in selected_model:
            kwargs["prompt"] = prompt
        with audio_path.open("rb") as audio_file:
            response = client.audio.transcriptions.create(file=audio_file, **kwargs)
        text = _extract_text(response)
    except Exception as exc:  # noqa: BLE001
        return TranscriptionResult(ok=False, model=model or DEFAULT_TRANSCRIBE_MODEL, audio_path=str(audio_path), error=f"OpenAI transcription failed: {exc}")
    if not text:
        return TranscriptionResult(ok=False, model=selected_model, audio_path=str(audio_path), error="OpenAI transcription returned empty text")
    return TranscriptionResult(ok=True, text=text, model=selected_model, audio_path=str(audio_path))


def transcribe_remote_audio(
    audio_url: str,
    cache_root: Path,
    item_id: str,
    title: str = "",
    model: str = "",
    headers: dict[str, str] | None = None,
) -> TranscriptionResult:
    if not audio_url:
        return TranscriptionResult(ok=False, error="audio_url is empty")
    try:
        audio_path = download_audio(audio_url, cache_root, item_id, headers=headers)
    except Exception as exc:  # noqa: BLE001
        return TranscriptionResult(ok=False, error=f"audio download failed: {exc}")
    prompt = (
        "这是一段中文短视频音频，请按原语言转写。"
        "保留产品名、工具名和专有名词，例如 Codex、Obsidian、OpenAI。"
    )
    if title:
        prompt += f" 视频标题：{title}"
    return transcribe_with_openai(audio_path, model=model, prompt=prompt)
