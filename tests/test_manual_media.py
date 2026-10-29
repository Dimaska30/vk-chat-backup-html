import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_generation_logs import ROOT
from manual_media import store, lookup, fingerprint, update_markdown
from media_report import collect_report, render_report
from config import Config
from main import main
from Loggers.buffer_logger import BufferLogger


class ManualMediaTests(unittest.TestCase):
    def fixture(self, root):
        dialog = root / "ExportMessages/Dialogs/dialog_1"
        (dialog / "OriginalMessages").mkdir(parents=True)
        (dialog / "MdFiles/sub").mkdir(parents=True)
        attachment = {"type": "audio_message", "audio_message": {"id": 7}}
        message = dict(conversation_message_id=1, from_id=1, date=1700000000, text="Synthetic", attachments=[attachment])
        (dialog / "OriginalMessages/fake_1.json").write_text(json.dumps(message), encoding="utf-8")
        (dialog / "MdFiles/sub/1.md").write_text("Keep my edits", encoding="utf-8")
        return dialog, attachment

    def test_store_validation_missing_and_source_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            att = {"type": "photo"}
            with self.assertRaises(ValueError):
                store(root, 1, "attachments[0]", att, "bad.html", io.BytesIO(b"bad"))
            with self.assertRaises(ValueError):
                store(root, 1, "attachments[0]", att, "empty.jpg", io.BytesIO())
            file = store(root, 1, "attachments[0]", att, "../../fake.jpg", io.BytesIO(b"image"))
            self.assertEqual(file.parent, root / "ManualMedia")
            self.assertEqual(lookup(root, 1, "attachments[0]", att)["state"], "manual_restored")
            self.assertEqual(lookup(root, 1, "attachments[0]", dict(att, id=2))["state"], "source_changed")
            file.unlink()
            self.assertEqual(lookup(root, 1, "attachments[0]", att)["state"], "manual_file_missing")

    def test_upload_markdown_report_and_regeneration(self):
        import web
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            dialog, att = self.fixture(root)
            with patch.object(web, "_get_dialog_dir", return_value=dialog):
                client = web.app.test_client()
                data = dict(cid="1", item_path="attachments[0]", fingerprint=fingerprint(att), file=(io.BytesIO(b"voice"), "voice.ogg"))
                response = client.post("/dialog/1/manual-media", data=data)
                self.assertEqual(response.status_code, 200)
                md = dialog / "MdFiles/sub/1.md"
                self.assertIn("Keep my edits", md.read_text(encoding="utf-8"))
                self.assertIn("<audio", md.read_text(encoding="utf-8"))
                file = lookup(dialog, 1, "attachments[0]", att)["file"]
                update_markdown(dialog, 1, "attachments[0]", file)
                self.assertEqual(md.read_text(encoding="utf-8").count("### Вложение добавлено вручную"), 1)
                report = collect_report(dialog, 1)
                self.assertEqual(report["entries"][0]["state"], "manual_restored")
                self.assertEqual(report["problem_count"], 0)
                config = Config(export_root=temp, overwrite_existing_md=True)
                with patch("md_item_builder.download_file", side_effect=AssertionError("No download expected")):
                    list(main(config, None, BufferLogger()))
                self.assertTrue(file.is_file())
                self.assertIn("<audio", (dialog / "MdFiles/1.md").read_text(encoding="utf-8"))
                file.unlink()
                self.assertEqual(collect_report(dialog, 1)["entries"][0]["reasons"], ["manual_file_missing"])

    def test_rejects_stale_report_and_nonexistent_attachment(self):
        import web
        with tempfile.TemporaryDirectory() as temp:
            dialog, att = self.fixture(Path(temp))
            with patch.object(web, "_get_dialog_dir", return_value=dialog):
                client = web.app.test_client()
                for path, digest, code in [("attachments[0]", "stale", 409), ("../../anything", fingerprint(att), 404)]:
                    response = client.post("/dialog/1/manual-media", data=dict(cid="1", item_path=path, fingerprint=digest, file=(io.BytesIO(b"voice"), "voice.ogg")))
                    self.assertEqual(response.status_code, code)
                self.assertFalse((dialog / "ManualMedia").exists())
