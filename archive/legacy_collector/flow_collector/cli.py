from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .adapters import load_queue_items
from .manifest import Manifest
from .vault import MissingContentError, build_intake_note, build_source_note, ensure_dir


ARCHIVE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUEUE_ROOT = Path(os.getenv("FLOW_QUEUE_ROOT", str(ARCHIVE_ROOT / "queue")))
DEFAULT_VAULT_ROOT = Path(os.getenv("FLOW_VAULT_ROOT", str(ARCHIVE_ROOT / "vault")))
DEFAULT_STATE_ROOT = Path(os.getenv("FLOW_STATE_ROOT", str(ARCHIVE_ROOT / "state")))


def iter_queue_files(queue_root: Path) -> list[Path]:
    if not queue_root.exists():
        return []
    files = [
        path
        for path in queue_root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".txt", ".md", ".json", ".url", ".html", ".htm"}
    ]
    return sorted(files)


def append_log(path: Path, message: str) -> None:
    ensure_dir(path.parent)
    timestamp = datetime.now().isoformat(timespec="seconds")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{timestamp} {message}\n")


def ingest(
    queue_root: Path,
    vault_root: Path,
    manifest: Manifest,
    dry_run: bool = False,
    error_log: Path | None = None,
    cookies: Path | None = None,
    cookies_from_browser: str = "",
    state_root: Path | None = None,
    remote_transcribe: bool = False,
    transcribe_model: str = "",
) -> int:
    written = 0
    for path in iter_queue_files(queue_root):
        try:
            items = load_queue_items(
                path,
                cookies=cookies,
                cookies_from_browser=cookies_from_browser,
                remote_transcribe=remote_transcribe,
                state_root=state_root,
                transcribe_model=transcribe_model,
            )
        except Exception as exc:  # noqa: BLE001
            message = f"[ingest] parse failed: {path} -> {exc}"
            print(message, file=sys.stderr)
            if error_log:
                append_log(error_log, message)
            continue
        for item in items:
            if manifest.seen_raw(item.platform, item.item_id, item.url):
                existing = manifest.raw_note_path(item.platform, item.item_id, item.url)
                if existing and (item.description.strip() or item.transcript.strip()):
                    if dry_run:
                        print(f"[dry-run] refresh intake content {existing}")
                    else:
                        note = build_intake_note(vault_root, item)
                        manifest.mark_raw(item.platform, item.item_id, item.url, str(note.path))
                continue
            if dry_run:
                print(f"[dry-run] intake {item.platform} {item.item_id} {item.url}")
                continue
            note = build_intake_note(vault_root, item)
            manifest.mark_raw(item.platform, item.item_id, item.url, str(note.path))
            written += 1
    return written


def sync(vault_root: Path, manifest: Manifest, dry_run: bool = False, error_log: Path | None = None) -> int:
    count = 0
    raw_paths = list(manifest.data.get("raw", {}).values())
    files = sorted(Path(path) for path in raw_paths)
    for path in files:
        if not path.exists():
            message = f"[sync] inbox note missing: {path}"
            print(message, file=sys.stderr)
            if error_log:
                append_log(error_log, message)
            continue
        if manifest.seen_source(str(path)):
            continue
        try:
            note = build_source_note(vault_root, path)
        except MissingContentError as exc:
            message = f"[sync] source skipped, missing video content: {path} -> {exc}"
            print(message, file=sys.stderr)
            if error_log:
                append_log(error_log, message)
            continue
        except ValueError:
            continue
        except Exception as exc:  # noqa: BLE001
            message = f"[sync] source failed: {path} -> {exc}"
            print(message, file=sys.stderr)
            if error_log:
                append_log(error_log, message)
            continue
        if dry_run:
            print(f"[dry-run] source {path} -> {note.path}")
            continue
        manifest.mark_source(str(path), str(note.path))
        count += 1
    return count


def create_scheduled_task(task_name: str, time_text: str, queue_root: Path, vault_root: Path, state_root: Path) -> int:
    python_exe = Path(sys.executable)
    module_root = Path(__file__).resolve().parents[1]
    script_path = module_root / "run_collector.py"
    command = [
        "schtasks",
        "/Create",
        "/F",
        "/SC",
        "DAILY",
        "/ST",
        time_text,
        "/TN",
        task_name,
        "/TR",
        f'"{python_exe}" "{script_path}" run --queue "{queue_root}" --vault "{vault_root}" --state "{state_root}"',
    ]
    completed = subprocess.run(command, capture_output=True, text=True, cwd=module_root)
    if completed.returncode != 0:
        print(completed.stdout)
        print(completed.stderr, file=sys.stderr)
    return completed.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="flow_collector")
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE_ROOT)
    parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT_ROOT)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_ROOT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--cookies", type=Path, default=None)
    parser.add_argument("--cookies-from-browser", default="")
    parser.add_argument("--remote-transcribe", action="store_true")
    parser.add_argument("--transcribe-model", default="")
    sub = parser.add_subparsers(dest="command")

    def add_common_options(command_parser: argparse.ArgumentParser, include_dry_run: bool = True) -> None:
        command_parser.add_argument("--queue", type=Path, default=argparse.SUPPRESS)
        command_parser.add_argument("--vault", type=Path, default=argparse.SUPPRESS)
        command_parser.add_argument("--state", type=Path, default=argparse.SUPPRESS)
        command_parser.add_argument("--cookies", type=Path, default=argparse.SUPPRESS)
        command_parser.add_argument("--cookies-from-browser", default=argparse.SUPPRESS)
        command_parser.add_argument("--remote-transcribe", action="store_true", default=argparse.SUPPRESS)
        command_parser.add_argument("--transcribe-model", default=argparse.SUPPRESS)
        if include_dry_run:
            command_parser.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS)

    add_common_options(sub.add_parser("run"))
    add_common_options(sub.add_parser("ingest"))
    add_common_options(sub.add_parser("sync"))
    install = sub.add_parser("install-task")
    add_common_options(install, include_dry_run=False)
    install.add_argument("--name", default="X1 收藏归档")
    install.add_argument("--time", default="23:30")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "run"
    ensure_dir(args.state)
    manifest = Manifest.load(args.state / "manifest.json")
    error_log = args.state / "errors.log"
    run_log = args.state / "runs.log"
    total = 0
    if command in {"run", "ingest"}:
        total += ingest(
            args.queue,
            args.vault,
            manifest,
            dry_run=args.dry_run,
            error_log=error_log,
            cookies=args.cookies,
            cookies_from_browser=args.cookies_from_browser,
            state_root=args.state,
            remote_transcribe=args.remote_transcribe,
            transcribe_model=args.transcribe_model,
        )
    if command in {"run", "sync"}:
        total += sync(args.vault, manifest, dry_run=args.dry_run, error_log=error_log)
    if command == "install-task":
        code = create_scheduled_task(args.name, args.time, args.queue, args.vault, args.state)
        if code == 0:
            manifest.save()
        return code
    if not args.dry_run:
        manifest.save()
        append_log(run_log, f"{command} completed total={total}")
    print(f"done: {total}")
    return 0
