"""One-shot Librus poller for cron and Docker Compose."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import signal
import sqlite3
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


LOG = logging.getLogger("librus_bot")
STATE_PATH = Path(os.environ.get("STATE_PATH", "/data/state.sqlite3"))


class SafeError(Exception):
    """An error whose message contains no credentials or session data."""


@dataclass(frozen=True)
class Item:
    kind: str
    key: str
    text: str


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SafeError(f"Missing environment variable: {name}")
    return value


def fingerprint(*values: object) -> str:
    data = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def item_key(href: str, *fallback: object) -> str:
    return f"href:{href}" if href else f"sha256:{fingerprint(*fallback)}"


def grade_items(numeric: object, descriptive: object) -> list[Item]:
    result: list[Item] = []
    fallback_occurrences: dict[str, int] = {}
    for groups, is_descriptive in ((numeric, False), (descriptive, True)):
        for subject_grades in groups:
            for subject, grades in subject_grades.items():
                for grade in grades:
                    value = str(grade.grade).strip()
                    if not value:
                        continue
                    category = (
                        str(getattr(grade, "desc", "")).splitlines()[0]
                        if is_descriptive
                        else str(getattr(grade, "category", ""))
                    )
                    teacher = str(getattr(grade, "teacher", ""))
                    date = str(grade.date)
                    semester = int(grade.semester)
                    href = str(getattr(grade, "href", "") or "")
                    key = item_key(href, subject, semester, date, value, category, teacher)
                    if not href:
                        occurrence = fallback_occurrences.get(key, 0) + 1
                        fallback_occurrences[key] = occurrence
                        key = f"{key}:{occurrence}"
                    lines = [f"🎓 New grade: {value}", f"Subject: {subject}"]
                    if category:
                        lines.append(f"Category: {category}")
                    if teacher:
                        lines.append(f"Teacher: {teacher}")
                    if date:
                        lines.append(f"Date: {date}")
                    result.append(Item("grade", key, "\n".join(lines)))
    return result


def announcement_items(announcements: object) -> list[Item]:
    result: list[Item] = []
    occurrences: dict[str, int] = {}
    for notice in announcements:
        title = str(notice.title).strip()
        author = str(notice.author).strip()
        date = str(notice.date).strip()
        description = str(notice.description).strip()
        base_key = f"sha256:{fingerprint(title, author, date, description)}"
        occurrence = occurrences.get(base_key, 0) + 1
        occurrences[base_key] = occurrence
        key = f"{base_key}:{occurrence}"
        lines = [f"📢 New announcement: {title or 'Untitled'}"]
        if author:
            lines.append(f"Author: {author}")
        if date:
            lines.append(f"Date: {date}")
        if description:
            lines.append("\n" + description)
        result.append(Item("announcement", key, "\n".join(lines)))
    return result


def message_item(message: object) -> Item:
    title = str(message.title).strip()
    author = str(message.author).strip()
    date = str(message.date).strip()
    href = str(message.href or "")
    key = item_key(href, title, author, date)
    lines = [f"✉️ New message: {title or 'No subject'}"]
    if author:
        lines.append(f"From: {author}")
    if date:
        lines.append(f"Date: {date}")
    if message.has_attachment:
        lines.append("Has attachment")
    return Item("message", key, "\n".join(lines))


def fetch_librus(
    username: str, password: str, known_message_keys: set[str]
) -> list[Item]:
    # Imports live here so database/formatting tests need no Librus credentials.
    from librus_apix.announcements import get_announcements
    from librus_apix.client import new_client
    from librus_apix.exceptions import TokenError
    from librus_apix.grades import get_grades
    from librus_apix.messages import get_max_page_number, get_received

    for attempt in range(2):
        try:
            client = new_client()
            client.get_token(username, password)
            numeric, _averages, descriptive = get_grades(client, "all")
            announcements = get_announcements(client)

            items = grade_items(numeric, descriptive)
            items.extend(announcement_items(announcements))

            # On the first run, visit every inbox page so older messages become
            # baseline. Later runs stop at the first page containing a known one.
            last_page = get_max_page_number(client)
            if last_page < 0:
                raise SafeError("Librus returned an invalid inbox page count")
            for page in range(last_page + 1):
                messages = get_received(client, page)
                page_items = [message_item(message) for message in messages]
                items.extend(page_items)
                if any(item.key in known_message_keys for item in page_items):
                    break

            # A source can expose the same record twice (for example in two
            # grade sections); the database key is the notification identity.
            return list({(item.kind, item.key): item for item in items}.values())
        except TokenError:
            if attempt:
                raise
            LOG.warning("Librus token expired; authenticating again")
    raise SafeError("Librus authentication failed")


def open_state(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.execute("PRAGMA busy_timeout = 30000")
    db.execute(
        """CREATE TABLE IF NOT EXISTS items (
            kind TEXT NOT NULL,
            item_key TEXT NOT NULL,
            text TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('baseline', 'pending', 'sent')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            delivered_at TEXT,
            PRIMARY KEY (kind, item_key)
        )"""
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    db.commit()
    return db


def known_message_keys(db: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in db.execute("SELECT item_key FROM items WHERE kind = 'message'")
    }


def record_items(db: sqlite3.Connection, items: Iterable[Item]) -> bool:
    """Persist a complete snapshot. Return True if it was the initial baseline."""
    db.execute("BEGIN IMMEDIATE")
    try:
        initial = (
            db.execute("SELECT value FROM meta WHERE key = 'initialized'").fetchone()
            is None
        )
        status = "baseline" if initial else "pending"
        db.executemany(
            "INSERT OR IGNORE INTO items (kind, item_key, text, status) VALUES (?, ?, ?, ?)",
            ((item.kind, item.key, item.text, status) for item in items),
        )
        if initial:
            db.execute("INSERT INTO meta (key, value) VALUES ('initialized', '1')")
        db.commit()
        return initial
    except Exception:
        db.rollback()
        raise


def send_pending(db: sqlite3.Connection, send: Callable[[str], None]) -> int:
    sent = 0
    pending = db.execute(
        "SELECT kind, item_key, text FROM items WHERE status = 'pending' "
        "ORDER BY created_at, rowid"
    ).fetchall()
    for kind, key, message in pending:
        send(message)
        db.execute(
            "UPDATE items SET status = 'sent', delivered_at = CURRENT_TIMESTAMP "
            "WHERE kind = ? AND item_key = ?",
            (kind, key),
        )
        db.commit()
        sent += 1
    return sent


def send_telegram(token: str, chat_id: str, message: str) -> None:
    # Telegram sendMessage allows 4096 characters after entity parsing. Plain
    # text avoids markup injection and requires no HTML/Markdown escaping.
    used_units = 0
    safe_text = []
    for char in message:
        units = 2 if ord(char) > 0xFFFF else 1
        if used_units + units > 4095:
            safe_text.append("…")
            break
        safe_text.append(char)
        used_units += units
    message = "".join(safe_text)
    payload = json.dumps({"chat_id": chat_id, "text": message}).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            answer = json.load(response)
    except urllib.error.HTTPError as error:
        raise SafeError(f"Telegram returned HTTP {error.code}") from None
    except urllib.error.URLError:
        raise SafeError("Telegram connection failed") from None
    if not answer.get("ok"):
        raise SafeError("Telegram rejected the message")


def run_cycle(
    db: sqlite3.Connection,
    fetch: Callable[[set[str]], list[Item]],
    send: Callable[[str], None],
) -> tuple[bool, int]:
    items = fetch(known_message_keys(db))
    initial = record_items(db, items)
    if initial:
        return True, 0
    return False, send_pending(db, send)


def _deadline(_signum: int, _frame: object) -> None:
    raise SafeError("Poll exceeded 180 seconds")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        username = required_env("LIBRUS_USERNAME")
        password = required_env("LIBRUS_PASSWORD")
        token = required_env("TELEGRAM_BOT_TOKEN")
        chat_id = required_env("TELEGRAM_CHAT_ID")
        with open_state(STATE_PATH) as db:
            signal.signal(signal.SIGALRM, _deadline)
            signal.alarm(180)
            try:
                initial, sent = run_cycle(
                    db,
                    lambda known: fetch_librus(username, password, known),
                    lambda message: send_telegram(token, chat_id, message),
                )
            finally:
                signal.alarm(0)
        if initial:
            LOG.info("Initial snapshot saved; no notifications sent")
        else:
            LOG.info("Poll complete; notifications sent: %d", sent)
        return 0
    except Exception as error:
        # Third-party exceptions can contain account/session data; log only the
        # type, except for our deliberately safe messages.
        if isinstance(error, SafeError):
            LOG.error("Poll failed: %s", error)
        else:
            LOG.error("Poll failed: %s", type(error).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
