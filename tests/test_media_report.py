import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_generation_logs import ROOT
from media_report import collect_report, render_report, save_report


class MediaReportTests(unittest.TestCase):
    def fixture(self, root):
        originals = root / "OriginalMessages"
        originals.mkdir()
        for i in range(45):
            message = dict(conversation_message_id=i+1, date=1700000000+i, from_id=1, text="<script>PRIVATE</script>")
            if i == 22:
                message["attachments"] = [{"type": "photo", "photo": {}}]
                message["reply_message"] = {"conversation_message_id": 4, "attachments": [{"type": "audio_message"}]}
            (originals / f"{i}.json").write_text(json.dumps(message), encoding="utf-8")
        logs = root / "Logs" / "run"
        logs.mkdir(parents=True)
        return logs / "events.jsonl"

    def event(self, event, time, **details):
        return dict(timestamp=f"2026-10-29T10:00:{time:02d}+00:00", peer_id=1, event=event,
                    details=dict(root_cid=23, item_path="attachments[0]", attachment_type="photo", **details))

    def test_latest_success_replaces_error_and_bounds_context(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log = self.fixture(root)
            records = [self.event("attachment_finished", 1, status="error", downloads={"download_result": "error"}),
                       self.event("attachment_finished", 2, status="processed", downloads={"download_result": "downloaded"})]
            log.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
            report = collect_report(root, 1)
            photo, reply = report["entries"]
            self.assertEqual(photo["state"], "downloaded")
            self.assertEqual(photo["reasons"], [])
            self.assertEqual(reply["reasons"], ["unknown"])
            self.assertEqual(len(reply["context"]), 41)
            self.assertEqual([m["cid"] for m in reply["context"] if m["target"]], [23])
            self.assertEqual(reply["context"][0]["cid"], 3)
            self.assertEqual(reply["context"][-1]["cid"], 43)
            html = render_report(report)
            self.assertNotIn("<script>", html)
            self.assertIn("&lt;script&gt;", html)
            path = save_report(root, 1)
            self.assertTrue(path.is_file())
            self.assertTrue(path.with_suffix(".json").is_file())

    def test_interruption_and_damaged_log(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log = self.fixture(root)
            log.write_text(json.dumps(self.event("attachment_started", 3)) + "\n{partial", encoding="utf-8")
            report = collect_report(root, 1)
            self.assertEqual(report["entries"][0]["reasons"], ["interrupted"])
            self.assertEqual(len(report["issues"]), 1)

    def test_video_reasons_and_nested_walls(self):
        from media_report import walk_attachments
        message = {"attachments": [{"type": "wall", "wall": {"copy_history": [{"attachments": [{"type": "video"}]}]}}]}
        paths = [path for path, _, _ in walk_attachments(message)]
        self.assertIn("attachments[0].wall.copy_history[0].attachments[0]", paths)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log = self.fixture(root)
            records = [self.event("attachment_finished", 2, status="processed", downloads={"preview_download_result": "downloaded", "mp4_download_result": "disabled_by_settings"})]
            log.write_text(json.dumps(records[0]), encoding="utf-8")
            report = collect_report(root, 1)
            self.assertEqual(report["entries"][0]["reasons"], ["disabled_by_settings"])
            self.assertEqual(report["reason_summary"]["disabled_by_settings"], 1)

    def test_web_generates_only_on_explicit_post(self):
        import web
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            with patch.object(web, "_get_dialog_dir", return_value=root), patch.object(web, "export_serve_abs", root.parent):
                client = web.app.test_client()
                self.assertEqual(client.get("/dialog/1/media-report").status_code, 405)
                self.assertFalse((root / "Reports").exists())
                response = client.post("/dialog/1/media-report")
                self.assertEqual(response.status_code, 302)
                self.assertEqual(len(list((root / "Reports").glob("*/missing-media.html"))), 1)
                with client.get(response.location) as page:
                    self.assertEqual(page.status_code, 200)
