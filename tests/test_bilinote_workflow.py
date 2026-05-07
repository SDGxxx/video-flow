from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "bilinote_workflow.py"
SPEC = importlib.util.spec_from_file_location("bilinote_workflow", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load workflow module: {MODULE_PATH}")
bw = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bw)


class BiliNoteWorkflowTests(unittest.TestCase):
    def test_strip_screenshots_removes_markers_and_embeds(self) -> None:
        text = "\n".join(
            [
                "# Title",
                "",
                "*Screenshot-[00:10]*",
                "",
                "![](/static/screenshots/demo.jpg)",
                "",
                "Body",
            ]
        )
        cleaned = bw.strip_screenshots(text)
        self.assertNotIn("Screenshot-[00:10]", cleaned)
        self.assertNotIn("/static/screenshots/demo.jpg", cleaned)
        self.assertIn("Body", cleaned)

    def test_copy_and_rewrite_images_deduplicates_screenshots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault_root = root / "vault"
            bilinote_root = root / "BiliNote"
            screenshot_root = bilinote_root / "static" / "screenshots"
            screenshot_root.mkdir(parents=True, exist_ok=True)
            (screenshot_root / "one.jpg").write_bytes(b"same-bytes")
            (screenshot_root / "two.jpg").write_bytes(b"same-bytes")
            (screenshot_root / "three.jpg").write_bytes(b"unique-bytes")

            original_vault_root = bw.VAULT_ROOT
            original_bilinote_root = bw.BILINOTE_ROOT
            original_asset_root = bw.ASSET_ROOT
            original_keep = bw.should_keep_screenshot
            try:
                bw.VAULT_ROOT = vault_root
                bw.BILINOTE_ROOT = bilinote_root
                bw.ASSET_ROOT = vault_root / "99 Assets" / "BiliNote"

                def limited_keep(kept, *, seconds=None, digest=None, max_count=bw.MAX_SCREENSHOTS_PER_NOTE, min_gap_seconds=bw.MIN_SCREENSHOT_GAP_SECONDS):
                    return original_keep(kept, seconds=seconds, digest=digest, max_count=2, min_gap_seconds=min_gap_seconds)

                bw.should_keep_screenshot = limited_keep
                rewritten = bw.copy_and_rewrite_images(
                    "\n".join(
                        [
                            "![](/static/screenshots/one.jpg)",
                            "![](/static/screenshots/two.jpg)",
                            "![](/static/screenshots/three.jpg)",
                        ]
                    ),
                    "task-123",
                    "Sample Title",
                    "2026-05-07",
                    item_id="",
                )
            finally:
                bw.VAULT_ROOT = original_vault_root
                bw.BILINOTE_ROOT = original_bilinote_root
                bw.ASSET_ROOT = original_asset_root
                bw.should_keep_screenshot = original_keep

            self.assertEqual(rewritten.count("![["), 2)
            assets = sorted((vault_root / "99 Assets" / "BiliNote" / "2026-05-07" / "Sample Title_task-123").glob("*.jpg"))
            self.assertEqual(len(assets), 2)

    def test_inbox_keeps_first_pass_while_source_stays_lightweight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault_root = root / "vault"
            bilinote_root = root / "BiliNote"
            state_root = root / "state"
            screenshot_root = bilinote_root / "static" / "screenshots"
            note_results = bilinote_root / "note_results"
            source_media = vault_root / "10 Sources" / "Media"
            inbox_media = vault_root / "00 Inbox" / "Media"
            asset_root = vault_root / "99 Assets" / "BiliNote"
            screenshot_root.mkdir(parents=True, exist_ok=True)
            note_results.mkdir(parents=True, exist_ok=True)
            source_media.mkdir(parents=True, exist_ok=True)
            inbox_media.mkdir(parents=True, exist_ok=True)
            asset_root.mkdir(parents=True, exist_ok=True)

            task_id = "task-123"
            markdown = "\n".join(
                [
                    "# Sample Title",
                    "",
                    "Intro paragraph.",
                    "",
                    "*Screenshot-[00:10]*",
                    "",
                    "![](/static/screenshots/one.jpg)",
                    "",
                    "Closing paragraph.",
                ]
            )
            (screenshot_root / "one.jpg").write_bytes(b"same-bytes")
            (note_results / f"{task_id}_markdown.md").write_text(markdown, encoding="utf-8")
            (note_results / f"{task_id}_transcript.json").write_text(json.dumps({"language": "zh"}, ensure_ascii=False), encoding="utf-8")
            (note_results / f"{task_id}.json").write_text(
                json.dumps(
                    {
                        "audio_meta": {"video_id": "vid-1", "title": "Sample Title"},
                        "transcript": {"language": "zh"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            original_values = {
                "VAULT_ROOT": bw.VAULT_ROOT,
                "BILINOTE_ROOT": bw.BILINOTE_ROOT,
                "STATE_ROOT": bw.STATE_ROOT,
                "INBOX_MEDIA_ROOT": bw.INBOX_MEDIA_ROOT,
                "SOURCE_MEDIA_ROOT": bw.SOURCE_MEDIA_ROOT,
                "ASSET_ROOT": bw.ASSET_ROOT,
                "DAILY_LINKS": bw.DAILY_LINKS,
            }
            try:
                bw.VAULT_ROOT = vault_root
                bw.BILINOTE_ROOT = bilinote_root
                bw.STATE_ROOT = state_root
                bw.INBOX_MEDIA_ROOT = inbox_media
                bw.SOURCE_MEDIA_ROOT = source_media
                bw.ASSET_ROOT = asset_root
                bw.DAILY_LINKS = vault_root / "35 Dashboards" / "bilinote_daily_links.md"

                source_path = bw.write_intake_note(
                    "https://example.com/video/1",
                    "douyin",
                    task_id,
                    "model-a",
                    source_media / "placeholder.md",
                )
                source_note = bw.write_source_note(
                    "https://example.com/video/1",
                    "douyin",
                    task_id,
                    "model-a",
                    source_path,
                )
            finally:
                bw.VAULT_ROOT = original_values["VAULT_ROOT"]
                bw.BILINOTE_ROOT = original_values["BILINOTE_ROOT"]
                bw.STATE_ROOT = original_values["STATE_ROOT"]
                bw.INBOX_MEDIA_ROOT = original_values["INBOX_MEDIA_ROOT"]
                bw.SOURCE_MEDIA_ROOT = original_values["SOURCE_MEDIA_ROOT"]
                bw.ASSET_ROOT = original_values["ASSET_ROOT"]
                bw.DAILY_LINKS = original_values["DAILY_LINKS"]

            inbox_text = Path(source_path).read_text(encoding="utf-8")
            source_text = Path(source_note).read_text(encoding="utf-8")
            self.assertIn("BiliNote First Pass", inbox_text)
            self.assertIn("Screenshot assets", inbox_text)
            self.assertIn("First Pass Summary", source_text)
            self.assertNotIn("/static/screenshots/", source_text)
            self.assertNotIn("Screenshot-[00:10]", source_text)


if __name__ == "__main__":
    unittest.main()
