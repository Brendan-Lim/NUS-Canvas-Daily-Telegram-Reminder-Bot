import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import reminder as r

NOW = datetime(2026, 10, 12, 0, 0, tzinfo=timezone.utc)


def iso(delta):
    return (NOW + delta).isoformat()


class FakeCanvas:
    def __init__(self, assignments, quizzes, quiz_submissions=None):
        self.assignments, self.quizzes = assignments, quizzes
        self.quiz_submissions = quiz_submissions or {}

    def pages(self, path, params=None, *, not_found_ok=False):
        if path == "courses":
            return [{"id": 101, "course_code": "CS2100"}]
        if path.endswith("/assignments"):
            return self.assignments
        if path.endswith("/quizzes"):
            return self.quizzes
        raise AssertionError(path)

    def one(self, path, not_found_ok=False):
        return self.quiz_submissions.get(path, {"quiz_submissions": []})


class ReminderTests(unittest.TestCase):
    def test_submission_status_uses_student_attempt_not_grade_alone(self):
        self.assertFalse(r.is_submitted({"workflow_state": "graded", "attempt": 0}))
        self.assertFalse(r.is_submitted({"workflow_state": "unsubmitted"}))
        self.assertTrue(
            r.is_submitted(
                {"workflow_state": "graded", "attempt": 1, "submission_type": "online_upload"}
            )
        )
        self.assertTrue(r.is_submitted({"workflow_state": "submitted"}))
        self.assertTrue(r.is_submitted({"submitted_at": iso(timedelta(days=-1))}))
        self.assertTrue(
            r.quiz_is_submitted({"quiz_submissions": [{"finished_at": iso(timedelta(days=-1))}]})
        )
        self.assertFalse(r.quiz_is_submitted({"quiz_submissions": [{"workflow_state": "untaken"}]}))

    def test_seven_day_window_duplicates_and_completed(self):
        assignments = [
            {
                "id": 1,
                "name": "Assignment",
                "due_at": iso(timedelta(hours=20)),
                "submission": {"workflow_state": "unsubmitted"},
            },
            {
                "id": 2,
                "quiz_id": 55,
                "name": "Quiz A",
                "due_at": iso(timedelta(days=4)),
                "submission": {"attempt": 0},
            },
            {
                "id": 3,
                "name": "Submitted",
                "due_at": iso(timedelta(days=1)),
                "submission": {"submitted_at": iso(timedelta(days=-1))},
            },
            {"id": 4, "name": "Too late", "due_at": iso(timedelta(days=8))},
            {"id": 5, "name": "Already passed", "due_at": iso(timedelta(minutes=-1))},
            {"id": 6, "name": "At seven days", "due_at": iso(timedelta(days=7))},
        ]
        quizzes = [
            {"id": 55, "assignment_id": 2, "title": "Quiz A", "due_at": iso(timedelta(days=4))},
            {"id": 56, "title": "Quiz B", "published": True, "due_at": iso(timedelta(days=6))},
            {"id": 57, "title": "Done Quiz", "published": True, "due_at": iso(timedelta(days=1))},
            {
                "id": 58,
                "title": "Unpublished",
                "published": False,
                "due_at": iso(timedelta(days=1)),
            },
        ]
        quiz_submissions = {
            "courses/101/quizzes/57/submission": {
                "quiz_submissions": [{"workflow_state": "complete"}]
            }
        }
        items = r.gather_deadlines(
            FakeCanvas(assignments, quizzes, quiz_submissions), now=NOW
        ).deadlines
        self.assertEqual(
            [d.title for d in items], ["Assignment", "Quiz A", "Quiz B", "At seven days"]
        )
        self.assertEqual(items[1].kind, "Quiz")
        self.assertEqual(len([d for d in items if d.title == "Quiz A"]), 1)

    def test_cutoff_and_html_escaping(self):
        items = [
            r.Deadline(
                "Assignment",
                "CS<&",
                "Urgent <now>",
                NOW + timedelta(hours=48),
                "https://canvas.nus.edu.sg/a",
            ),
            r.Deadline(
                "Quiz",
                "CS2100",
                "Later",
                NOW + timedelta(hours=48, seconds=1),
                "https://canvas.nus.edu.sg/b",
            ),
        ]
        msg = r.make_messages(items, now=NOW)[0]
        self.assertIn("Due within 48 hours", msg)
        self.assertIn("Later within 7 days", msg)
        self.assertIn("CS&lt;&amp;", msg)
        self.assertIn("&lt;now&gt;", msg)
        self.assertEqual(msg.count("• [Quiz]"), 1)
        self.assertEqual(msg.count("• [Assignment]"), 1)

    def test_empty_digest_and_timezones(self):
        msg = r.make_messages([], now=NOW)[0]
        self.assertIn("No incomplete Canvas", msg)
        self.assertIsNone(r.parse_dt(None))
        self.assertEqual(r.parse_dt("2026-10-12T08:00:00+08:00"), NOW)

    def test_never_send_token_to_untrusted_pagination_origin(self):
        self.assertTrue(r._valid_canvas_url("https://canvas.nus.edu.sg/api/v1/courses?page=2"))
        self.assertFalse(r._valid_canvas_url("https://evil.invalid/api/v1/courses?page=2"))
        self.assertFalse(r._valid_canvas_url("http://canvas.nus.edu.sg/api/v1/courses"))
        self.assertFalse(
            r._valid_canvas_url("https://canvas.nus.edu.sg.evil.invalid/api/v1/courses")
        )
        self.assertFalse(
            r._valid_canvas_url("https://canvas.nus.edu.sg@evil.invalid/api/v1/courses")
        )
        response = Mock(status_code=200)
        response.json.return_value = []
        response.links = {"next": {"url": "https://evil.invalid/api/v1/courses"}}
        fake_session = Mock()
        fake_session.headers = {}
        fake_session.get.return_value = response
        client = r.CanvasClient("secret-test", fake_session)
        with self.assertRaises(r.ServiceError):
            client.pages("courses")
        self.assertEqual(fake_session.get.call_count, 1)
        self.assertFalse(fake_session.get.call_args.kwargs["allow_redirects"])

    def test_required_404_diagnostics_and_optional_quiz_404(self):
        fake_session = Mock()
        fake_session.headers = {}
        fake_session.get.return_value = SimpleNamespace(status_code=404)
        client = r.CanvasClient("dummy-test-token", fake_session)
        with self.assertRaisesRegex(r.ServiceError, "HTTP 404") as ctx:
            client.pages("courses/101/assignments")
        self.assertNotIn("dummy-test-token", str(ctx.exception))
        with self.assertRaisesRegex(r.ServiceError, "HTTP 404"):
            client.pages("courses/101/quizzes", not_found_ok=True)

    def test_customizable_digest_window_and_urgency(self):
        items = [
            r.Deadline(
                "Assignment",
                "CS1010",
                "Before urgent cutoff",
                NOW + timedelta(hours=23),
                "https://canvas.nus.edu.sg/a",
            ),
            r.Deadline(
                "Quiz",
                "CS1231",
                "After urgent cutoff",
                NOW + timedelta(hours=25),
                "https://canvas.nus.edu.sg/b",
            ),
        ]
        msg = r.make_messages(items, now=NOW, days=3, urgent_hours=24)[0]
        self.assertIn("Due within 24 hours", msg)
        self.assertIn("Later within 3 days", msg)
        self.assertLess(msg.index("Before urgent cutoff"), msg.index("After urgent cutoff"))
        self.assertIn("3 days", r.make_messages([], now=NOW, days=3)[0])

    def test_setting_validation(self):
        from unittest.mock import patch

        with patch.dict("os.environ", {"REMINDER_DAYS": "4"}):
            self.assertEqual(r.read_int_setting("REMINDER_DAYS", 7, minimum=1, maximum=30), 4)
        with patch.dict("os.environ", {"REMINDER_DAYS": "999"}):
            with self.assertRaisesRegex(r.ServiceError, "between"):
                r.read_int_setting("REMINDER_DAYS", 7, minimum=1, maximum=30)
        with patch.dict("os.environ", {"URGENT_HOURS": "oops"}):
            with self.assertRaisesRegex(r.ServiceError, "whole number"):
                r.read_int_setting("URGENT_HOURS", 48, minimum=1, maximum=168)

    def test_telegram_send_rejects_failure_without_leaking_token(self):
        fake_session = Mock()
        fake_session.post.return_value = SimpleNamespace(status_code=401)
        with self.assertRaisesRegex(r.ServiceError, "401") as ctx:
            r.send_telegram(
                "hello", bot_token="sensitive-token", chat_id="123", session=fake_session
            )
        self.assertNotIn("sensitive-token", str(ctx.exception))
        self.assertFalse(fake_session.post.call_args.kwargs["allow_redirects"])

    def test_split_large_digest_keeps_each_message_below_limit(self):
        items = [
            r.Deadline(
                "Assignment",
                "CS2100",
                f"Task {i}" + "X" * 45,
                NOW + timedelta(hours=5),
                "https://canvas.nus.edu.sg/courses/1/assignments/1",
            )
            for i in range(120)
        ]
        msgs = r.make_messages(items, now=NOW)
        self.assertGreater(len(msgs), 1)
        self.assertTrue(all(len(m) <= r.MAX_TELEGRAM_CHARS for m in msgs))
        self.assertEqual(sum(m.count("• [Assignment]") for m in msgs), 120)


if __name__ == "__main__":
    unittest.main()
