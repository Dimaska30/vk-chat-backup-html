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
            self.assertEqual(len(report["groups"][0]["context"]), 41)
            self.assertEqual([m["cid"] for m in report["groups"][0]["context"] if m["target"]], [23])
            self.assertEqual(report["groups"][0]["context"][0]["cid"], 3)
            self.assertEqual(report["groups"][0]["context"][-1]["cid"], 43)
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
                self.assertEqual(response.status_code, 200)
                body = response.get_data(as_text=True)
                self.assertIn("Чтение исходных сообщений", body)
                import re
                location = re.search(r'<a href="([^"]+)">Открыть отчёт', body).group(1)
                self.assertEqual(len(list((root / "Reports").glob("*/missing-media.html"))), 1)
                with client.get(location) as page:
                    self.assertEqual(page.status_code, 200)


    def test_large_report_is_paginated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            originals = root / "OriginalMessages"
            originals.mkdir()
            (root / "MdFiles").mkdir()
            (root / "MdFiles" / "1.md").write_text("Synthetic", encoding="utf-8")
            message = dict(conversation_message_id=1, date=1700000000, text="Synthetic",
                           attachments=[{"type": "photo"} for _ in range(121)])
            (originals / "1.json").write_text(json.dumps(message), encoding="utf-8")
            progress = []
            path = save_report(root, 1, progress.append)
            pages = list(path.parent.glob("*.html"))
            self.assertEqual(len(pages), 1)
            self.assertEqual(sum(p.read_text(encoding="utf-8").count("<article>") for p in pages), 1)
            self.assertLessEqual(path.read_text(encoding="utf-8").count("<article>"), 50)
            self.assertEqual(path.read_text(encoding="utf-8").count("<section>"), 121)
            self.assertTrue(any("страница 1 из 1" in line for line in progress))


    def test_unprocessed_messages_are_counted_without_problem_cards(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            report = collect_report(root, 1)
            self.assertEqual(report["entries"], [])
            self.assertEqual(report["unprocessed_messages"], 45)
            self.assertEqual(report["unprocessed_attachments"], 2)
            logs = root / "Logs/run/events.jsonl"
            logs.write_text(json.dumps(dict(timestamp="2026-10-29T10:00:00+00:00", peer_id=1,
                event="message_started", cid=23, details={})), encoding="utf-8")
            report = collect_report(root, 1)
            self.assertEqual(len(report["entries"]), 2)
            self.assertEqual(report["unprocessed_messages"], 44)
            self.assertEqual(report["unprocessed_attachments"], 0)
            self.assertEqual(len(report["groups"][0]["context"]), 41)

    def test_existing_legacy_markdown_counts_as_processed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            md = root / "MdFiles/subfolder"
            md.mkdir(parents=True)
            (md / "Legacy.Id23_part_1.md").write_text("Synthetic", encoding="utf-8")
            report = collect_report(root, 1)
            self.assertEqual(len(report["entries"]), 2)
            self.assertEqual(report["entries"][0]["reasons"], ["unknown"])
            self.assertEqual(report["unprocessed_messages"], 44)


    def test_only_adjacent_messages_group_even_with_cid_gaps(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "OriginalMessages").mkdir()
            (root / "MdFiles").mkdir()
            for position, cid in enumerate([10, 90, 91, 200, 300]):
                attachments = [{"type": "photo"}] * (2 if position == 0 else 1) if position != 2 else []
                message = dict(conversation_message_id=cid, date=1700000000+position, text="Synthetic", attachments=attachments)
                (root / "OriginalMessages" / f"{cid}.json").write_text(json.dumps(message), encoding="utf-8")
                (root / "MdFiles" / f"{cid}.md").write_text("Synthetic", encoding="utf-8")
            report = collect_report(root, 1)
            self.assertEqual(report["problem_count"], 5)
            self.assertEqual(report["group_count"], 2)
            self.assertEqual([len(g["entry_indices"]) for g in report["groups"]], [3, 2])
            self.assertEqual([m["cid"] for m in report["groups"][0]["context"] if m["target"]], [10, 90])
            html = render_report(report)
            self.assertEqual(html.count("<article>"), 2)
            self.assertEqual(html.count("<details>"), 2)

    def test_group_pagination_keeps_groups_intact(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "OriginalMessages").mkdir()
            (root / "MdFiles").mkdir()
            for i in range(103):
                message = dict(conversation_message_id=i+1, date=1700000000+i, attachments=[{"type": "photo"}] if i % 2 == 0 else [])
                (root / "OriginalMessages" / f"{i}.json").write_text(json.dumps(message), encoding="utf-8")
                (root / "MdFiles" / f"{i+1}.md").write_text("Synthetic", encoding="utf-8")
            path = save_report(root, 1)
            self.assertEqual(path.read_text(encoding="utf-8").count("<article>"), 50)
            self.assertEqual((path.parent / "page-2.html").read_text(encoding="utf-8").count("<article>"), 2)
