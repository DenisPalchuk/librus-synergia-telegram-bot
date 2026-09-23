import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bot import (
    Item,
    announcement_items,
    fetch_librus,
    grade_items,
    message_item,
    open_state,
    run_cycle,
)


class ItemTests(unittest.TestCase):
    def test_grade_href_is_stable_and_identical_grades_without_href_are_distinct(self):
        grade = lambda href: SimpleNamespace(
            grade="5", date="23.09.2026", semester=1, category="Test",
            teacher="Teacher", href=href,
        )
        items = grade_items([{"Math": [grade("/grade/1"), grade(""), grade("")]}], [])
        self.assertEqual(len({item.key for item in items}), 3)
        self.assertEqual(items[0].key, "href:/grade/1")
        self.assertIn("New grade: 5", items[0].text)
        self.assertIn("Subject: Math", items[0].text)

    def test_announcements_with_same_data_are_distinct(self):
        announcement = SimpleNamespace(
            title="News", author="School", date="23.09.2026", description="Text"
        )
        items = announcement_items([announcement, announcement])
        self.assertNotEqual(items[0].key, items[1].key)
        self.assertIn("New announcement: News", items[0].text)

    def test_message_text_is_english(self):
        message = SimpleNamespace(
            title="Notice", author="Teacher", date="23.09.2026",
            href="/message/1", has_attachment=True,
        )
        self.assertIn("New message: Notice", message_item(message).text)
        self.assertIn("Has attachment", message_item(message).text)


class LibrusFetchTests(unittest.TestCase):
    def test_fetches_all_sources_and_stops_at_known_message(self):
        grade = SimpleNamespace(
            grade="5", date="23.09.2026", semester=1, category="Test",
            teacher="Teacher", href="/grade/1",
        )
        announcement = SimpleNamespace(
            title="News", author="School", date="23.09.2026",
            description="Meeting",
        )
        message = lambda href: SimpleNamespace(
            title="Notice", author="Teacher", date="23.09.2026",
            href=href, has_attachment=False,
        )
        pages = {0: [message("/message/new")], 1: [message("/message/old")]}
        with (
            patch("librus_apix.client.new_client") as new_client,
            patch("librus_apix.grades.get_grades", return_value=([{"Math": [grade]}], {}, [])),
            patch("librus_apix.announcements.get_announcements", return_value=[announcement]),
            patch("librus_apix.messages.get_max_page_number", return_value=3),
            patch("librus_apix.messages.get_received", side_effect=lambda _, page: pages[page]) as received,
        ):
            items = fetch_librus("user", "password", {"href:/message/old"})
        new_client.return_value.get_token.assert_called_once_with("user", "password")
        self.assertEqual(received.call_count, 2)
        self.assertEqual({item.kind for item in items}, {"grade", "announcement", "message"})
        self.assertEqual(len(items), 4)


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state.sqlite3"

    def tearDown(self):
        self.tmp.cleanup()

    def test_baseline_restart_new_items_and_no_duplicates(self):
        old = Item("grade", "href:/old", "Old")
        new = Item("grade", "href:/new", "New")
        sent = []
        with open_state(self.path) as db:
            self.assertEqual(run_cycle(db, lambda _: [old], sent.append), (True, 0))
        with open_state(self.path) as db:
            self.assertEqual(
                run_cycle(db, lambda _: [old, new], sent.append), (False, 1)
            )
            self.assertEqual(
                run_cycle(db, lambda _: [old, new], sent.append), (False, 0)
            )
        self.assertEqual(sent, ["New"])

    def test_failed_fetch_does_not_initialize_state(self):
        with open_state(self.path) as db:
            def fail(_):
                raise ConnectionError("offline")

            with self.assertRaises(ConnectionError):
                run_cycle(db, fail, lambda _: None)
            self.assertIsNone(
                db.execute("SELECT value FROM meta WHERE key = 'initialized'").fetchone()
            )

    def test_failed_send_stays_pending_and_is_retried(self):
        old = Item("message", "href:/old", "Old")
        new = Item("message", "href:/new", "New")
        with open_state(self.path) as db:
            run_cycle(db, lambda _: [old], lambda _: None)

            def fail(_):
                raise ConnectionError("telegram offline")

            with self.assertRaises(ConnectionError):
                run_cycle(db, lambda _: [old, new], fail)
        sent = []
        with open_state(self.path) as db:
            self.assertEqual(
                run_cycle(db, lambda _: [old, new], sent.append), (False, 1)
            )
        self.assertEqual(sent, ["New"])


if __name__ == "__main__":
    unittest.main()
