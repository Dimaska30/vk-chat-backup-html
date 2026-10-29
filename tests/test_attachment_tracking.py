import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_generation_logs import ROOT
from config import Config
from md_item_builder import build_md_items
from Loggers.buffer_logger import BufferLogger
from Loggers.file_logger import FileLogger


class AttachmentTrackingTests(unittest.TestCase):
    def test_nested_paths_are_unique_and_repeatable(self):
        photo = {"type": "photo", "photo": {}}
        nested = {"conversation_message_id": 9, "attachments": [photo]}
        message = {"conversation_message_id": 20, "attachments": [photo, photo,
            {"type": "wall", "wall": {"attachments": [photo], "copy_history": [
                {"attachments": [photo], "copy_history": [{"attachments": [photo]}]}]}}],
            "fwd_messages": [dict(nested, fwd_messages=[nested]), nested], "reply_message": nested}
        expected = {"attachments[0]", "attachments[1]", "attachments[2]",
                    "attachments[2].wall.attachments[0]",
                    "attachments[2].wall.copy_history[0].attachments[0]",
                    "attachments[2].wall.copy_history[0].copy_history[0].attachments[0]",
                    "fwd_messages[0].attachments[0]", "fwd_messages[0].fwd_messages[0].attachments[0]",
                    "fwd_messages[1].attachments[0]", "reply_message.attachments[0]"}
        with tempfile.TemporaryDirectory() as temp:
            results = []
            for run in range(2):
                with FileLogger(temp, 123, BufferLogger()) as logger:
                    logger.context = {"cid": 20}
                    build_md_items(message, 20, 20, "fake.json", temp, temp, temp, {}, Config(), {}, logger)
                records = [json.loads(line) for line in (logger.directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]
                starts = [r for r in records if r["event"] == "attachment_started"]
                ends = [r for r in records if r["event"] == "attachment_finished"]
                self.assertEqual(len(starts), len(expected))
                self.assertEqual({r["details"]["item_path"] for r in ends}, expected)
                self.assertTrue(all(r["peer_id"] == 123 and r["details"]["root_cid"] == 20 for r in starts))
                self.assertEqual([r["details"]["message_cid"] for r in starts if r["details"]["item_path"] == "reply_message.attachments[0]"], [9])
                results.append({r["details"]["item_path"] for r in starts})
            self.assertEqual(*results)

    def test_failure_has_same_identity_as_start(self):
        with tempfile.TemporaryDirectory() as temp:
            logger = FileLogger(temp, 123, BufferLogger())
            with self.assertRaises(ValueError), logger, patch("md_item_builder._resolve_photo", side_effect=ValueError("private")):
                build_md_items({"attachments": [{"type": "photo"}]}, 20, 20, "fake.json", temp, temp, temp, {}, Config(), {}, logger)
            records = [json.loads(line) for line in (logger.directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            events = [r for r in records if r["event"] in ("attachment_started", "attachment_failed")]
            self.assertEqual(len(events), 2)
            self.assertEqual(events[0]["details"]["item_path"], events[1]["details"]["item_path"])
            self.assertNotIn("private", json.dumps(records))
