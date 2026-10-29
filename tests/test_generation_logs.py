import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for directory in ("Config", "ExportMessageToMd", "WebApp"):
    sys.path.insert(0, str(ROOT / directory))
from config import Config
from main import main
from Loggers.buffer_logger import BufferLogger
from Loggers.file_logger import FileLogger


class GenerationLogsTests(unittest.TestCase):
    def test_generation_privacy_unsupported_and_repeat(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sources = root / "ExportMessages" / "Sources"
            sources.mkdir(parents=True)
            message = dict(conversation_message_id=12, from_id=1, date=1700000000,
                           text="PRIVATE <img src=x onerror=alert(1)>",
                           attachments=[dict(type="audio_message", audio_message={}),
                                        dict(type="photo", photo={})])
            (sources / "test.json").write_text(json.dumps(dict(peer_id=1, items=[message])), encoding="utf-8")
            config = Config(export_root=temp)
            output = list(main(config, None, BufferLogger()))
            self.assertTrue(any(message["text"] in line for line in output))
            list(main(config, None, BufferLogger()))
            logs = list((root / "ExportMessages/Dialogs/dialog_1/Logs").iterdir())
            self.assertEqual(len(logs), 2)
            events = []
            for folder in logs:
                for file in folder.iterdir():
                    self.assertNotIn("PRIVATE", file.read_text(encoding="utf-8"))
                records = [json.loads(line) for line in (folder / "events.jsonl").read_text(encoding="utf-8").splitlines()]
                self.assertEqual(records[-1]["details"]["status"], "completed")
                events.extend(records)
            self.assertTrue(any(e["details"].get("status") == "unsupported" for e in events))
            self.assertTrue(any(e["event"] == "message_skipped" for e in events))

    def test_failure_and_cancellation_preserve_records(self):
        for error, expected in [(ValueError("PRIVATE"), "failed"), (GeneratorExit(), "cancelled")]:
            with tempfile.TemporaryDirectory() as temp:
                logger = FileLogger(temp, 1, BufferLogger())
                with self.assertRaises(type(error)):
                    with logger:
                        logger.LogWarning("https://private/?token=secret", error)
                        raise error
                text = (logger.directory / "events.jsonl").read_text(encoding="utf-8")
                self.assertNotIn("secret", text)
                self.assertNotIn("PRIVATE", text)
                self.assertEqual(json.loads(text.splitlines()[-1])["details"]["status"], expected)

    def test_web_escapes_message_text(self):
        import web
        web.app.config["TESTING"] = True
        with patch.object(web, "load_config", return_value=Config()), patch.object(web, "run_pipeline", return_value=iter(["<img src=x onerror=alert(1)>"])):
            response = web.app.test_client().post("/export/generate")
            self.assertIn(b"&lt;img", response.data)
            self.assertNotIn(b"<img", response.data)


if __name__ == "__main__":
    unittest.main()
