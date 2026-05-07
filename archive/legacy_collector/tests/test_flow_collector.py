from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from flow_collector.adapters import infer_platform, infer_item_id, infer_medium, load_queue_items
from flow_collector.cli import ingest, sync
from flow_collector.manifest import Manifest


class FlowCollectorTests(unittest.TestCase):
    def test_platform_detection(self) -> None:
        self.assertEqual(infer_platform("https://www.douyin.com/video/123"), "douyin")
        self.assertEqual(infer_platform("https://www.xiaohongshu.com/explore/abc"), "xiaohongshu")

    def test_item_id_fallback(self) -> None:
        item_id = infer_item_id("douyin", "https://example.com/any", "hello")
        self.assertEqual(len(item_id), 16)

    def test_douyin_modal_id(self) -> None:
        item_id = infer_item_id(
            "douyin",
            "https://video.example/item?modal_id=7634243350208704744",
            "",
        )
        self.assertEqual(item_id, "7634243350208704744")

    def test_ingest_and_sync_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue = root / "queue"
            vault = root / "vault"
            state = root / "state"
            (queue / "in").mkdir(parents=True, exist_ok=True)
            (vault / "00 Inbox" / "Media").mkdir(parents=True, exist_ok=True)
            (vault / "10 Sources" / "Media").mkdir(parents=True, exist_ok=True)
            url_file = queue / "in" / "links.txt"
            url_file.write_text("https://www.douyin.com/video/7621048932151414054\n", encoding="utf-8")
            url_file.with_suffix(".transcript.txt").write_text(
                "这是一段视频逐字稿，讨论个人知识库自动归档和视频内容提取。",
                encoding="utf-8",
            )
            manifest = Manifest.load(state / "manifest.json")
            written = ingest(queue, vault, manifest)
            self.assertEqual(written, 1)
            manifest.save()
            inbox_files = list((vault / "00 Inbox").rglob("*.md"))
            self.assertEqual(len(inbox_files), 1)
            source_written = sync(vault, manifest)
            self.assertEqual(source_written, 1)
            manifest.save()
            source_files = list((vault / "10 Sources").rglob("*.md"))
            self.assertEqual(len(source_files), 1)
            note = source_files[0].read_text(encoding="utf-8")
            self.assertIn("type: \"source\"", note)
            self.assertIn("source_inbox:", note)
            self.assertIn("这是一段视频逐字稿", note)

    def test_fallback_title_still_creates_needs_transcription_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue = root / "queue"
            vault = root / "vault"
            state = root / "state"
            (queue / "in").mkdir(parents=True, exist_ok=True)
            url_file = queue / "in" / "links.txt"
            url_file.write_text(
                "https://video.example/item?modal_id=7634243350208704744\n",
                encoding="utf-8",
            )
            manifest = Manifest.load(state / "manifest.json")
            ingest(queue, vault, manifest)
            sync(vault, manifest)
            source_files = list((vault / "10 Sources").rglob("*.md"))
            self.assertEqual(len(source_files), 1)
            source = source_files[0].read_text(encoding="utf-8")
            self.assertIn("status: \"needs_transcription\"", source)
            inbox_files = list((vault / "00 Inbox").rglob("*.md"))
            self.assertEqual(len(inbox_files), 1)
            intake = inbox_files[0].read_text(encoding="utf-8")
            self.assertIn("content_error:", intake)

    def test_sidecar_transcript_creates_content_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue = root / "queue"
            vault = root / "vault"
            state = root / "state"
            queue.mkdir(parents=True, exist_ok=True)
            url_file = queue / "video.md"
            url_file.write_text("https://www.douyin.com/video/7634243350208704744\n", encoding="utf-8")
            url_file.with_suffix(".transcript.txt").write_text(
                "这是一段视频逐字稿，讨论个人知识库自动归档和视频内容提取。",
                encoding="utf-8",
            )
            manifest = Manifest.load(state / "manifest.json")
            ingest(queue, vault, manifest)
            sync(vault, manifest)
            source_files = list((vault / "10 Sources").rglob("*.md"))
            self.assertEqual(len(source_files), 1)
            note = source_files[0].read_text(encoding="utf-8")
            self.assertIn("status: \"summarized\"", note)
            self.assertIn("这是一段视频逐字稿", note)

    def test_queue_loader_txt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.txt"
            path.write_text("https://www.douyin.com/video/123\n", encoding="utf-8")
            items = load_queue_items(path)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].platform, "douyin")


if __name__ == "__main__":
    unittest.main()
