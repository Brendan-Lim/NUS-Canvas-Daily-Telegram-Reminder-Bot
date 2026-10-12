"""Offline regression tests. All names and identifiers are fictional."""

import os
import unittest
from datetime import timedelta
from unittest.mock import Mock, patch

import requests
from test_reminder import NOW, FakeCanvas, iso

import reminder as r


def response(status=200, payload=None, links=None, headers=None):
    value = Mock(status_code=status, links=links or {}, headers=headers or {})
    value.json.return_value = [] if payload is None else payload
    return value


def client_with(*responses):
    session = Mock(headers={})
    session.get.side_effect = responses
    return r.CanvasClient("fictional-token", session), session


def assignment(**fields):
    return {
        "id": 1,
        "name": "Fictional task",
        "due_at": iso(timedelta(days=1)),
        "submission": {"workflow_state": "unsubmitted"},
        **fields,
    }


class ClassificationTests(unittest.TestCase):
    def test_current_unsubmitted_overrides_attempt_grade_and_old_timestamp(self):
        for attempt in (0, 1, 3, True, "2"):
            with self.subTest(attempt=attempt):
                self.assertEqual(
                    r.submission_state(
                        {
                            "workflow_state": "unsubmitted",
                            "attempt": attempt,
                            "grade": "A",
                            "submitted_at": iso(timedelta(days=-1)),
                        }
                    ),
                    r.SubmissionState.INCOMPLETE,
                )

    def test_submission_evidence_and_resubmission(self):
        for submission in (
            {"workflow_state": "submitted"},
            {"workflow_state": "pending_review"},
            {"workflow_state": "graded", "submitted_at": iso(timedelta(days=-1))},
            {"workflow_state": "graded", "attempt": 2, "submission_type": "online_quiz"},
            {"workflow_state": "submitted", "grade_matches_current_submission": False},
        ):
            with self.subTest(submission=submission):
                self.assertEqual(r.submission_state(submission), r.SubmissionState.SUBMITTED)
        self.assertEqual(
            r.submission_state({"workflow_state": "submitted", "redo_request": True}),
            r.SubmissionState.INCOMPLETE,
        )

    def test_grade_and_attempt_alone_are_unknown(self):
        for submission in (
            None,
            [],
            {},
            {"grade": "A"},
            {"attempt": 1},
            {"workflow_state": "graded", "attempt": 1},
            {"workflow_state": "complete"},
            {"workflow_state": "new_unknown_state"},
        ):
            with self.subTest(submission=submission):
                self.assertEqual(r.submission_state(submission), r.SubmissionState.UNKNOWN)

    def test_unrecognized_state_with_old_timestamp_not_completed(self):
        self.assertEqual(
            r.submission_state(
                {"workflow_state": "unexpected", "submitted_at": iso(timedelta(days=-1))}
            ),
            r.SubmissionState.UNKNOWN,
        )
        self.assertEqual(
            r.quiz_submission_state(
                {
                    "quiz_submissions": [
                        {"workflow_state": "unexpected", "finished_at": iso(timedelta(days=-1))}
                    ]
                }
            ),
            r.SubmissionState.UNKNOWN,
        )

    def test_malformed_graded_submission_type_is_unknown(self):
        for value in ([], {}, True, 42):
            with self.subTest(value=value):
                self.assertEqual(
                    r.submission_state(
                        {"workflow_state": "graded", "attempt": 1, "submission_type": value}
                    ),
                    r.SubmissionState.UNKNOWN,
                )

    def test_excused_is_separate(self):
        self.assertEqual(r.submission_state({"excused": True}), r.SubmissionState.EXCUSED)
        self.assertFalse(r.is_submitted({"excused": True}))

    def test_malformed_timestamps_are_unknown(self):
        for value in ("", 42, False, {}, "2026-10-12", "garbage"):
            with self.subTest(value=value):
                self.assertEqual(
                    r.submission_state({"submitted_at": value}), r.SubmissionState.UNKNOWN
                )
                with self.assertRaises(r.ServiceError):
                    r.parse_dt(value)

    def test_quiz_latest_attempt_wins(self):
        self.assertFalse(
            r.quiz_is_submitted(
                {
                    "quiz_submissions": [
                        {"attempt": 1, "workflow_state": "complete"},
                        {"attempt": 2, "workflow_state": "untaken"},
                    ]
                }
            )
        )
        self.assertTrue(
            r.quiz_is_submitted(
                {
                    "quiz_submissions": [
                        {"attempt": 1, "workflow_state": "untaken"},
                        {"attempt": 2, "workflow_state": "pending_review"},
                    ]
                }
            )
        )

    def test_quiz_unknown_and_unavailable_are_not_empty(self):
        for payload in (
            None,
            {},
            [],
            {"quiz_submissions": None},
            {"quiz_submissions": [3]},
            {"quiz_submissions": [{"finished_at": "bad"}]},
            {"quiz_submissions": [{"workflow_state": "preview"}]},
            {"quiz_submissions": [{"attempt": 1}, {"attempt": 1}]},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(r.quiz_submission_state(payload), r.SubmissionState.UNKNOWN)
        self.assertEqual(
            r.quiz_submission_state({"quiz_submissions": []}), r.SubmissionState.INCOMPLETE
        )


class ScanTests(unittest.TestCase):
    def test_complete_empty_scan(self):
        result = r.gather_deadlines(FakeCanvas([], []), now=NOW)
        self.assertEqual(result.status, r.ScanStatus.SUCCESS)
        self.assertIn("No incomplete", r.make_messages(result, now=NOW)[0])

    def test_course_list_failure(self):
        c = Mock()
        c.pages.side_effect = r.ServiceError("Canvas API request failed (HTTP 403)")
        result = r.gather_deadlines(c, now=NOW)
        self.assertEqual(result.status, r.ScanStatus.FAILURE)
        self.assertIn("Check Canvas directly", r.make_messages(result, now=NOW)[0])

    def test_independent_course_continues(self):
        c = Mock()

        def pages(path, params=None):
            if path == "courses":
                return [{"id": 1}, {"id": 2}]
            if path.startswith("courses/1/"):
                raise r.ServiceError("Canvas API request failed (HTTP 403)")
            return [assignment()] if path.endswith("assignments") else []

        c.pages.side_effect = pages
        result = r.gather_deadlines(c, now=NOW)
        self.assertEqual(result.status, r.ScanStatus.FAILURE)
        self.assertEqual(len(result.deadlines), 1)

    def test_no_useful_scan_is_failed(self):
        c = Mock()
        c.pages.side_effect = [[{"id": 1}], r.ServiceError("HTTP 403"), r.ServiceError("HTTP 404")]
        self.assertEqual(r.gather_deadlines(c, now=NOW).status, r.ScanStatus.FAILURE)

    def test_optional_endpoint_failure_succeeds_with_warning_not_all_clear(self):
        c = Mock()
        c.pages.side_effect = [[{"id": 1}], [], r.ServiceError("HTTP 404"), [], []]
        result = r.gather_deadlines(c, now=NOW)
        self.assertEqual(result.status, r.ScanStatus.SUCCESS)
        msg = r.make_messages(result, now=NOW)[0]
        self.assertIn("Standalone Classic Quiz coverage", msg)
        self.assertNotIn("No incomplete", msg)

    def test_unknown_submission_retains_deadline(self):
        result = r.gather_deadlines(FakeCanvas([assignment(submission=None)], []), now=NOW)
        self.assertEqual(result.status, r.ScanStatus.SUCCESS)
        self.assertTrue(result.deadlines[0].submission_unknown)
        self.assertIn("submission unverified", r.make_messages(result, now=NOW)[0])

    def test_unavailable_quiz_submission_retains_deadline(self):
        c = FakeCanvas([], [{"id": 8, "title": "Fictional quiz", "due_at": iso(timedelta(days=1))}])
        c.one = Mock(return_value=None)
        result = r.gather_deadlines(c, now=NOW)
        self.assertEqual(result.status, r.ScanStatus.SUCCESS)
        self.assertEqual(len(result.deadlines), 1)
        c.one.assert_called_once_with("courses/101/quizzes/8/submission")

    def test_quiz_submission_http_failure_retains_known_deadline(self):
        for status in (403, 500, 502, 503, 504):
            with self.subTest(status=status):
                c = FakeCanvas([], [{"id": 8, "due_at": iso(timedelta(days=1))}])
                c.one = Mock(side_effect=r.ServiceError(f"HTTP {status}"))
                result = r.gather_deadlines(c, now=NOW)
                self.assertEqual(result.status, r.ScanStatus.SUCCESS)
                self.assertTrue(result.deadlines[0].submission_unknown)

    def test_unpublished_assignment_and_unavailable_quizzes_succeed_with_warning(self):
        c = Mock()
        c.pages.side_effect = [
            [{"id": 1}],
            [assignment(published=False)],
            r.ServiceError("HTTP 404"),
            [],
            [],
        ]
        self.assertEqual(r.gather_deadlines(c, now=NOW).status, r.ScanStatus.SUCCESS)

    def test_excused_and_unpublished_filtered(self):
        result = r.gather_deadlines(
            FakeCanvas(
                [assignment(submission={"excused": True}), assignment(id=2, published=False)], []
            ),
            now=NOW,
        )
        self.assertEqual(result.deadlines, [])

    def test_missing_deadline_not_fabricated(self):
        missing = assignment()
        del missing["due_at"]
        for item in (missing, assignment(due_at="bad"), assignment(due_at=None)):
            with self.subTest(item=item):
                self.assertEqual(r.gather_deadlines(FakeCanvas([item], []), now=NOW).deadlines, [])

    def test_exact_window_boundaries(self):
        values = [
            timedelta(0),
            timedelta(seconds=1),
            timedelta(days=7),
            timedelta(days=7, seconds=1),
        ]
        result = r.gather_deadlines(
            FakeCanvas([assignment(id=i + 1, due_at=iso(d)) for i, d in enumerate(values)], []),
            now=NOW,
        )
        self.assertEqual(len(result.deadlines), 2)
        self.assertEqual(result.deadlines[-1].due_at, NOW + timedelta(days=7))

    def test_new_quiz_and_repeated_assignments_deduplicated(self):
        item = assignment(is_quiz_assignment=True, submission_types=["external_tool"])
        result = r.gather_deadlines(
            FakeCanvas([item, item], [{"id": 9, "assignment_id": 1}]), now=NOW
        )
        self.assertEqual(len(result.deadlines), 1)
        self.assertEqual(result.deadlines[0].kind, "Quiz")

    def test_linked_quiz_uses_student_assignment_due(self):
        c = FakeCanvas([], [{"id": 9, "assignment_id": 1, "due_at": iso(timedelta(days=1))}])
        c.one = Mock(return_value=assignment(due_at=iso(timedelta(days=3))))
        result = r.gather_deadlines(c, now=NOW)
        self.assertEqual(result.deadlines[0].due_at, NOW + timedelta(days=3))
        self.assertEqual(c.one.call_args.args[1]["override_assignment_dates"], "true")

    def test_linked_quiz_no_generic_fallback_on_failure(self):
        c = FakeCanvas([], [{"id": 9, "assignment_id": 1, "due_at": iso(timedelta(days=1))}])
        c.one = Mock(side_effect=r.ServiceError("HTTP 403"))
        result = r.gather_deadlines(c, now=NOW)
        self.assertEqual(result.status, r.ScanStatus.FAILURE)
        self.assertEqual(result.deadlines, [])

    def test_fatal_failure_discards_collected_data(self):
        c = Mock()
        c.pages.side_effect = [
            [{"id": 1}],
            [assignment()],
            r.ServiceError("Unsafe URL", fatal=True),
        ]
        result = r.gather_deadlines(c, now=NOW)
        self.assertEqual(result.status, r.ScanStatus.FAILURE)
        self.assertEqual(result.deadlines, [])

    def test_malformed_records_safe(self):
        for courses in ([None], [{"id": True}], [{"id": "1"}], {}):
            with self.subTest(courses=courses):
                c = Mock()
                c.pages.return_value = courses
                self.assertEqual(r.gather_deadlines(c, now=NOW).status, r.ScanStatus.FAILURE)


class CanvasTransportTests(unittest.TestCase):
    @patch("reminder.time.sleep")
    def test_retryable_statuses(self, sleep):
        for status in (429, 500, 502, 503, 504):
            with self.subTest(status=status):
                c, session = client_with(response(status, headers={"Retry-After": "3"}), response())
                self.assertEqual(c.pages("courses"), [])
                self.assertEqual(session.get.call_count, 2)
                sleep.assert_called_with(3)

    @patch("reminder.time.sleep")
    def test_network_retry_and_exhaustion(self, sleep):
        for error in (
            requests.Timeout("fictional-secret"),
            requests.ConnectionError("fictional-secret"),
        ):
            with self.subTest(error=type(error)):
                c, session = client_with(error, error, error)
                with self.assertRaisesRegex(r.ServiceError, "bounded retries") as caught:
                    c.pages("courses")
                self.assertEqual(session.get.call_count, 3)
                self.assertNotIn("fictional-secret", str(caught.exception))

    @patch("reminder.time.sleep")
    def test_retryable_final_failure(self, sleep):
        c, session = client_with(*[response(503)] * 3)
        with self.assertRaisesRegex(r.ServiceError, "503"):
            c.pages("courses")
        self.assertEqual(session.get.call_count, 3)

    @patch("reminder.time.sleep")
    def test_nonretryable_statuses(self, sleep):
        for status in (400, 401, 403, 404, 422, 501):
            with self.subTest(status=status):
                c, session = client_with(response(status))
                with self.assertRaises(r.ServiceError):
                    c.pages("courses")
                self.assertEqual(session.get.call_count, 1)
        sleep.assert_not_called()

    @patch("reminder.time.sleep")
    def test_tls_failure_not_retried(self, sleep):
        c, session = client_with(requests.exceptions.SSLError("private transport details"))
        with self.assertRaisesRegex(r.ServiceError, "TLS") as caught:
            c.pages("courses")
        self.assertTrue(caught.exception.fatal)
        self.assertEqual(session.get.call_count, 1)
        sleep.assert_not_called()

    def test_optional_one_404_is_unavailable(self):
        c, _ = client_with(response(404))
        self.assertIsNone(c.one("courses/1/quizzes/2/submission", not_found_ok=True))

    def test_redirects_refused(self):
        for status in (301, 302, 307, 308):
            with self.subTest(status=status):
                c, session = client_with(
                    response(status, headers={"Location": "https://evil.invalid"})
                )
                with self.assertRaises(r.ServiceError) as caught:
                    c.one("courses")
                self.assertTrue(caught.exception.fatal)
                self.assertEqual(session.get.call_count, 1)
                self.assertFalse(session.get.call_args.kwargs["allow_redirects"])
                self.assertEqual(session.get.call_args.kwargs["timeout"], r.REQUEST_TIMEOUT)

    def test_unsafe_url_variants(self):
        for url in (
            "https://canvas.nus.edu.sg:invalid/api/v1/courses",
            "https://canvas.nus.edu.sg:444/api/v1/courses",
            "https://x@canvas.nus.edu.sg/api/v1/courses",
            "https://canvas.nus.edu.sg/api/v1/courses#secret",
            "https://canvas.nus.edu.sg/api/v1/\ncourses",
            "//canvas.nus.edu.sg/api/v1/courses",
            None,
        ):
            with self.subTest(url=url):
                self.assertFalse(r._valid_canvas_url(url))
                c, session = client_with()
                with self.assertRaises(r.ServiceError):
                    c._request(url)
                session.get.assert_not_called()

    def test_loop_and_limit(self):
        c, _ = client_with(response(links={"next": {"url": r.CANVAS_ORIGIN + "/api/v1/courses"}}))
        with self.assertRaisesRegex(r.ServiceError, "loop"):
            c.pages("courses")
        pages = [
            response(links={"next": {"url": r.CANVAS_ORIGIN + f"/api/v1/courses?page={i + 1}"}})
            for i in range(r.MAX_PAGES)
        ]
        c, session = client_with(*pages)
        with self.assertRaisesRegex(r.ServiceError, "safety limit"):
            c.pages("courses")
        self.assertEqual(session.get.call_count, r.MAX_PAGES)

    def test_malformed_json_and_list(self):
        c, _ = client_with(response(payload={}))
        with self.assertRaises(r.ServiceError):
            c.pages("courses")
        bad = response()
        bad.json.side_effect = ValueError("private body")
        c, _ = client_with(bad)
        with self.assertRaisesRegex(r.ServiceError, "invalid JSON"):
            c.one("courses")
        c, _ = client_with(response(payload=[None]))
        with self.assertRaises(r.ServiceError):
            c.pages("courses")

    def test_budget_expired(self):
        c, session = client_with()
        c.expires = 0
        with self.assertRaisesRegex(r.ServiceError, "runtime"):
            c.one("courses")
        session.get.assert_not_called()

    def test_retry_after_bounded_and_date(self):
        for value in ("bad", "99999", "-1", "nan", "inf"):
            with self.subTest(value=value):
                self.assertEqual(r._retry_delay(value, 1), 2)
        self.assertEqual(r._retry_delay("20", 0), 20)
        date = NOW + timedelta(seconds=5)
        with patch("reminder.datetime") as dt:
            dt.now.return_value = NOW
            self.assertEqual(r._retry_delay(date.strftime("%a, %d %b %Y %H:%M:%S GMT"), 0), 5)


class DeliveryTests(unittest.TestCase):
    ENV = {
        "CANVAS_TOKEN": "fictional-canvas",
        "TELEGRAM_BOT_TOKEN": "fictional-bot",
        "TELEGRAM_CHAT_ID": "123",
    }

    def test_telegram_failures_are_safe(self):
        for value in (
            response(302),
            response(429),
            response(500),
            response(payload=[]),
            response(payload={"ok": False}),
            requests.Timeout("private-token"),
        ):
            with self.subTest(value=type(value)):
                session = Mock()
                session.post.side_effect = [value]
                with self.assertRaises(r.ServiceError) as caught:
                    r.send_telegram(
                        "hello", bot_token="private-token", chat_id="123", session=session
                    )
                self.assertNotIn("private-token", str(caught.exception))
                self.assertEqual(session.post.call_count, 1)

    def test_telegram_confirmed_success(self):
        session = Mock()
        session.post.return_value = response(payload={"ok": True})
        r.send_telegram("hello", bot_token="fictional", chat_id="123", session=session)

    def test_unicode_messages_bound_and_sgt(self):
        items = [
            r.Deadline("Quiz", "TEST", "😀" * 90, NOW + timedelta(hours=1), r.CANVAS_ORIGIN + "/x")
            for _ in range(80)
        ]
        messages = r.make_messages(items, now=NOW)
        self.assertTrue(all(r.telegram_length(m) <= 4096 for m in messages))
        self.assertIn("9:00 AM SGT", messages[0])
        with self.assertRaises(r.ServiceError):
            r.send_telegram("😀" * 3000, bot_token="fictional", chat_id="123", session=Mock())

    def test_main_binary_exit_codes(self):
        for status in r.ScanStatus:
            with (
                self.subTest(status=status),
                patch.dict(os.environ, self.ENV, clear=True),
                patch("reminder.gather_deadlines", return_value=r.ScanResult(status, [], [])),
                patch("reminder.send_telegram") as send,
            ):
                self.assertEqual(r.main(), 0 if status == r.ScanStatus.SUCCESS else 1)
                send.assert_called_once()

    def test_unexpected_scan_failure_notifies(self):
        with (
            patch.dict(os.environ, self.ENV, clear=True),
            patch("reminder.gather_deadlines", side_effect=RuntimeError("private body")),
            patch("reminder.send_telegram") as send,
        ):
            self.assertEqual(r.main(), 1)
            self.assertIn("could not be completed", send.call_args.args[0])
            self.assertNotIn("private body", send.call_args.args[0])

    def test_partial_delivery_stops_and_fails(self):
        with (
            patch.dict(os.environ, self.ENV, clear=True),
            patch(
                "reminder.gather_deadlines",
                return_value=r.ScanResult(r.ScanStatus.SUCCESS, [], []),
            ),
            patch("reminder.make_messages", return_value=["a", "b", "c"]),
            patch("reminder.send_telegram", side_effect=[None, r.ServiceError("HTTP 500")]) as send,
            self.assertLogs(r.LOG, "ERROR") as logs,
        ):
            self.assertEqual(r.main(), 1)
            self.assertEqual(send.call_count, 2)
            self.assertIn("1 of 3", " ".join(logs.output))

    def test_missing_configuration_no_network(self):
        with patch.dict(os.environ, {}, clear=True), patch("reminder.CanvasClient") as client:
            self.assertEqual(r.main(), 1)
            client.assert_not_called()
