import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from Loggers.base_logger import BaseLogger


class FileLogger(BaseLogger):
    """Per-dialog journal. Callers supply metadata, never message bodies or URLs."""

    def __init__(self, dialog_dir, peer_id, delegate):
        self.directory = Path(dialog_dir) / "Logs" / (datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + "_" + uuid4().hex[:8])
        self.directory.mkdir(parents=True, exist_ok=False)
        self.peer_id = peer_id
        self.delegate = delegate
        self.context = {}
        self.counts = Counter()

    def __enter__(self):
        self.LogEvent("run_started")
        return self

    def LogEvent(self, event, **details):
        self.counts[event] += 1
        record = dict(timestamp=datetime.now().astimezone().isoformat(), peer_id=self.peer_id,
                      **self.context, event=event, details=details)
        # Open/close on each event: completed records survive generator cancellation.
        with (self.directory / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        with (self.directory / "generation.log").open("a", encoding="utf-8") as stream:
            stream.write(f"{record['timestamp']} peer={self.peer_id} {self.context} {event} {json.dumps(details, ensure_ascii=False)}\n")

    def LogWarning(self, error_type, exception=None):
        # Existing warnings can contain signed URLs, document titles or message text.
        self.LogEvent("warning", exception_type=type(exception).__name__ if exception else None)
        self.delegate.LogWarning(error_type, exception)

    def __exit__(self, exc_type, exc, tb):
        status = "completed" if exc_type is None else ("cancelled" if exc_type is GeneratorExit else "failed")
        self.LogEvent("run_finished", status=status, exception_type=exc_type.__name__ if exc_type else None,
                      counts=dict(self.counts))
