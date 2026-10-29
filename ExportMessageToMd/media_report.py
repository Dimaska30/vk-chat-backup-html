"""Explicit, local-only audit. Never runs while listing dialogs or exporting messages."""
import json
from collections import Counter
from datetime import datetime
from pathlib import Path


def walk_attachments(message, path=""):
    for index, attachment in enumerate(message.get("attachments", [])):
        location = f"{path}attachments[{index}]"
        yield location, attachment, message
        kind = attachment.get("type")
        if kind in ("wall", "post"):
            yield from walk_wall(attachment.get(kind, {}), location + "." + kind + ".")
    for index, child in enumerate(message.get("fwd_messages", [])):
        yield from walk_attachments(child, f"{path}fwd_messages[{index}].")
    if message.get("reply_message"):
        yield from walk_attachments(message["reply_message"], path + "reply_message.")


def walk_wall(wall, path):
    for index, attachment in enumerate(wall.get("attachments", [])):
        location = f"{path}attachments[{index}]"
        yield location, attachment, wall
        kind = attachment.get("type")
        if kind in ("wall", "post"):
            yield from walk_wall(attachment.get(kind, {}), location + "." + kind + ".")
    for index, child in enumerate(wall.get("copy_history", [])):
        yield from walk_wall(child, f"{path}copy_history[{index}].")


def collect_report(dialog_dir, peer_id, progress=None):
    progress = progress or (lambda message: None)
    progress("Чтение исходных сообщений…")
    dialog_dir = Path(dialog_dir)
    messages, issues = [], []
    for file in sorted((dialog_dir / "OriginalMessages").glob("*.json")):
        try:
            item = json.loads(file.read_text(encoding="utf-8"))
            if not isinstance(item, dict) or not isinstance(item.get("conversation_message_id"), int):
                raise ValueError("Invalid message")
            messages.append(item)
            if len(messages) % 100 == 0:
                progress(f"Прочитано сообщений: {len(messages)}")
        except (ValueError, OSError):
            issues.append("Не удалось прочитать исходное сообщение: " + file.name)
    messages.sort(key=lambda m: (m.get("date") or 0, m["conversation_message_id"]))
    progress(f"Прочитано сообщений: {len(messages)}. Проверка журналов…")
    latest = {}
    started_messages = set()
    for file in sorted((dialog_dir / "Logs").glob("*/events.jsonl")):
        try:
            lines = file.read_text(encoding="utf-8").splitlines()
        except OSError:
            issues.append("Не удалось прочитать журнал: " + file.parent.name)
            continue
        for line_number, line in enumerate(lines, 1):
            try:
                record = json.loads(line)
                details = record.get("details", {})
                if record.get("peer_id") == peer_id and record.get("event") in ("message_started", "markdown_written"):
                    started_messages.add(record.get("cid"))
                if record.get("peer_id") != peer_id or record.get("event") not in ("attachment_started", "attachment_finished", "attachment_failed"):
                    continue
                key = (details["root_cid"], details["item_path"])
                started_messages.add(details["root_cid"])
                timestamp = datetime.fromisoformat(record["timestamp"]).timestamp()
                if key not in latest or timestamp >= latest[key][0]:
                    latest[key] = (timestamp, record)
            except (ValueError, TypeError, KeyError, AttributeError):
                issues.append(f"Пропущена повреждённая запись журнала {file.parent.name}:{line_number}")
    progress("Сопоставление вложений…")
    # Legacy archives may have Markdown but no structured journal. Treat them as
    # previously processed, without claiming that their attachments downloaded.
    import re
    for file in (dialog_dir / "MdFiles").rglob("*.md"):
        match = re.search(r"(?:^|\.Id)(\d+)(?:_part_\d+)?\.md$", file.name)
        if match:
            started_messages.add(int(match.group(1)))
    unprocessed_messages = sum(m["conversation_message_id"] not in started_messages for m in messages)
    unprocessed_attachments = 0
    entries, summary, reasons = [], Counter(), Counter()
    for position, root in enumerate(messages):
        if position % 100 == 0:
            progress(f"Проверено сообщений: {position} из {len(messages)}")
        if root["conversation_message_id"] not in started_messages:
            unprocessed_attachments += sum(1 for _ in walk_attachments(root))
            continue
        for path, attachment, owner in walk_attachments(root):
            cid = root["conversation_message_id"]
            kind = attachment.get("type", "unknown")
            entry = dict(peer_id=peer_id, root_cid=cid, item_path=path, attachment_type=kind,
                         message_cid=owner.get("conversation_message_id"), date=owner.get("date", root.get("date")),
                         from_id=owner.get("from_id", owner.get("owner_id")), reasons=[], downloads={})
            saved = latest.get((cid, path))
            if saved is None:
                entry["reasons"] = ["unknown"]
            else:
                record = saved[1]
                details = record["details"]
                entry["last_event"] = record["timestamp"]
                if details.get("attachment_type") != kind:
                    entry["reasons"] = ["unknown"]
                elif record["event"] == "attachment_started":
                    entry["reasons"] = ["interrupted"]
                elif record["event"] == "attachment_failed":
                    entry["reasons"] = ["error"]
                else:
                    entry["downloads"] = details.get("downloads", {})
                    entry["reasons"] = sorted({v for v in entry["downloads"].values() if v != "downloaded"})
                    status = details.get("status")
                    if status in ("unsupported", "metadata_only", "error"):
                        entry["reasons"] = sorted(set(entry["reasons"] + [status]))
                    if not entry["reasons"]:
                        entry["state"] = "downloaded" if entry["downloads"] else "processed"
            entry["state"] = entry.get("state", "needs_attention")
            summary[(kind, entry["state"])] += 1
            if entry["reasons"]:
                entry["message_text"] = owner.get("text", "")
                entry["context"] = [dict(cid=m["conversation_message_id"], date=m.get("date"), from_id=m.get("from_id"), text=m.get("text", ""), target=i == position)
                                    for i, m in enumerate(messages[max(0, position-20):position+21], max(0, position-20))]
            reasons.update(entry["reasons"])
            entries.append(entry)
    return dict(peer_id=peer_id, generated_at=datetime.now().astimezone().isoformat(),
                summary=[dict(attachment_type=k[0], state=k[1], count=v) for k, v in sorted(summary.items())],
                entries=entries, issues=issues, reason_summary=dict(reasons),
                unprocessed_messages=unprocessed_messages, unprocessed_attachments=unprocessed_attachments,
                note="Состояние основано на последних событиях журналов. Фактическое наличие файлов на диске пока не проверяется. Нет события — результат неизвестен. Порядок вложений исходного JSON должен совпадать с порядком при генерации.")


LABELS = {"downloaded": "Скачано по журналу", "processed": "Обработано без скачивания",
          "needs_attention": "Требует внимания", "unknown": "Нет достоверных данных",
          "interrupted": "Обработка прервана", "error": "Ошибка обработки или скачивания",
          "no_url": "Нет доступной ссылки", "disabled_by_settings": "Отключено настройками",
          "unsupported": "Тип не поддерживается", "metadata_only": "Сохранено только описание"}


def render_report(report):
    from html import escape
    def e(value):
        return escape(str(value if value is not None else "—"), quote=True)
    def date_label(value):
        try:
            return e(datetime.fromtimestamp(value).astimezone().isoformat(sep=" ", timespec="seconds"))
        except (TypeError, ValueError, OverflowError, OSError):
            return e(value)
    def label(value):
        return e(LABELS.get(value, value))
    rows = []
    for entry in report["summary"]:
        rows.append(f"<tr><td>{e(entry['attachment_type'])}</td><td>{label(entry['state'])}</td><td>{entry['count']}</td></tr>")
    reason_rows = "".join(f"<li>{label(reason)}: {count}</li>" for reason, count in report.get("reason_summary", {}).items())
    cards = []
    for entry in report["entries"]:
        if not entry["reasons"]:
            continue
        context = []
        for message in entry["context"]:
            css = " class='target'" if message["target"] else ""
            context.append(f"<li{css}><b>CID {message['cid']} · автор {e(message['from_id'])} · дата {date_label(message['date'])}</b><pre>{e(message['text'])}</pre></li>")
        downloads = "; ".join(e(k) + ": " + label(v) for k, v in entry["downloads"].items())
        cards.append(f"<article><h2>CID {entry['root_cid']} · {e(entry['attachment_type'])}</h2>"
                     f"<p><code>{e(entry['item_path'])}</code></p><p>{', '.join(label(r) for r in entry['reasons'])}</p>"
                     f"<p>Автор: {e(entry['from_id'])}; дата: {date_label(entry['date'])}; CID вложенного сообщения: {e(entry['message_cid'])}</p>"
                     f"<p>{downloads}</p><pre>{e(entry.get('message_text', ''))}</pre>"
                     f"<details><summary>Контекст: до 20 сообщений до и после</summary><ol>{''.join(context)}</ol></details></article>")
    warnings = "".join("<li>" + e(issue) + "</li>" for issue in report["issues"])
    return ("<!doctype html><html lang='ru'><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'\">"
            "<title>Отчёт о вложениях</title><style>body{font:16px system-ui;max-width:1050px;margin:30px auto;padding:16px;background:#f5f5f5}"
            "article{background:white;padding:20px;margin:16px 0;border-radius:10px}pre{white-space:pre-wrap;overflow-wrap:anywhere}"
            "td,th{border:1px solid #ccc;padding:8px}.target{background:#fff0b5}li{padding:8px}code{overflow-wrap:anywhere}</style>"
            f"<h1>Отчёт о вложениях · диалог {report['peer_id']}</h1><p>{e(report['generated_at'])}</p>"
            f"<p>Ещё не обрабатывались: сообщений {report.get('unprocessed_messages', 0)}, вложений {report.get('unprocessed_attachments', 0)}. Они не включены в карточки проблем.</p>"
            f"<p>{e(report['note'])}</p><p>Отчёт содержит личную переписку. Не публикуйте его вместе с кодом.</p>"
            f"<table><tr><th>Тип</th><th>Состояние</th><th>Количество</th></tr>{''.join(rows)}</table>"
            f"<h2>Причины</h2><ul>{reason_rows}</ul><ul>{warnings}</ul>" + ("".join(cards) if cards else "<p>По доступным данным проблемных вложений не найдено.</p>") + "</html>")


def save_report(dialog_dir, peer_id, progress=None):
    from uuid import uuid4
    progress = progress or (lambda message: None)
    report = collect_report(dialog_dir, peer_id, progress)
    directory = Path(dialog_dir) / "Reports" / (datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + "_" + uuid4().hex[:8])
    directory.mkdir(parents=True, exist_ok=False)
    progress("Сохранение JSON…")
    with (directory / "missing-media.json").open("w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False)
    problems = [entry for entry in report["entries"] if entry["reasons"]]
    page_count = max(1, (len(problems) + 49) // 50)
    for page in range(page_count):
        progress(f"Сохранение HTML: страница {page + 1} из {page_count}")
        name = "missing-media.html" if page == 0 else f"page-{page + 1}.html"
        view = dict(report, entries=problems[page*50:(page+1)*50])
        links = []
        for target, label in ((page - 1, "Предыдущая"), (page + 1, "Следующая")):
            if 0 <= target < page_count:
                href = "missing-media.html" if target == 0 else f"page-{target + 1}.html"
                links.append(f'<a href="{href}">{label}</a>')
        navigation = f"<nav>Страница {page+1} из {page_count} · " + " · ".join(links) + "</nav>"
        output = render_report(view).replace("</html>", navigation + "</html>")
        (directory / name).write_text(output, encoding="utf-8")
    return directory / "missing-media.html"
