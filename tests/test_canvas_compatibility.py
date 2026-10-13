"""Public API contracts using fictional fixtures; all transport calls are mocked."""

import unittest
from datetime import timedelta
from unittest.mock import Mock

from test_hardening import client_with, response
from test_reminder import NOW, iso

import reminder as r


class EndpointDiagnosticsTests(unittest.TestCase):
    def test_failure_labels_never_echo_identifiers_or_data(self):
        paths = {
            "courses": "Course discovery",
            "courses/845391/assignments": "Course assignment listing",
            "courses/845391/quizzes": "Course quiz listing",
            "courses/845391/tabs": "Course navigation lookup",
            "planner/items": "Planner quiz supplement",
            "courses/845391/quizzes/772345": "Known quiz lookup",
            "courses/845391/quizzes/772345/submission": "Quiz submission lookup",
            "courses/845391/assignments/991234": "Linked assignment lookup",
            "courses/845391/quizzes/assignment_overrides": "Quiz override date lookup",
            "unsupported/845391": "Other Canvas operation",
        }
        for path, label in paths.items():
            for status in (401, 403, 404):
                with self.subTest(path=path, status=status):
                    c, session = client_with(response(status, payload={"title": "Private fixture"}))
                    with self.assertRaises(r.ServiceError) as caught:
                        c.one(path, {"student_secret": "fictional-query-value"})
                    diagnostic = str(caught.exception)
                    self.assertIn(label, diagnostic)
                    self.assertIn(str(status), diagnostic)
                    for value in (
                        "845391",
                        "772345",
                        "991234",
                        "fictional-query-value",
                        "Private fixture",
                        "fictional-token",
                        "https://",
                        "/api/v1/",
                    ):
                        self.assertNotIn(value, diagnostic)
                    self.assertEqual(session.get.call_count, 1)

    def test_optional_one_404_is_unknown_not_incomplete(self):
        c, _ = client_with(response(404))
        value = c.one("courses/1/quizzes/2/submission", not_found_ok=True)
        self.assertIsNone(value)
        self.assertEqual(r.quiz_submission_state(value), r.SubmissionState.UNKNOWN)

    def test_required_listing_404_does_not_become_empty(self):
        for path, label in (
            ("courses/1/assignments", "Course assignment listing"),
            ("courses/1/quizzes", "Course quiz listing"),
        ):
            with self.subTest(path=path):
                c, _ = client_with(response(404))
                with self.assertRaisesRegex(r.ServiceError, label):
                    c.pages(path)

    def test_wrapped_override_pagination(self):
        c, session = client_with(
            response(
                payload={"quiz_assignment_overrides": [{"quiz_id": "1"}]},
                links={
                    "next": {
                        "url": r.CANVAS_ORIGIN
                        + "/api/v1/courses/1/quizzes/assignment_overrides?page=2"
                    }
                },
            ),
            response(payload={"quiz_assignment_overrides": [{"quiz_id": "2"}]}),
        )
        records = c.pages(
            "courses/1/quizzes/assignment_overrides", list_key="quiz_assignment_overrides"
        )
        self.assertEqual([row["quiz_id"] for row in records], ["1", "2"])
        self.assertEqual(session.get.call_count, 2)

    def test_wrapped_pagination_preserves_safety(self):
        for next_url in (
            "https://evil.invalid/api/v1/x",
            r.CANVAS_ORIGIN + "/api/v1/courses/1/quizzes/assignment_overrides",
        ):
            with self.subTest(next_url=next_url):
                c, session = client_with(
                    response(
                        payload={"quiz_assignment_overrides": []}, links={"next": {"url": next_url}}
                    )
                )
                with self.assertRaises(r.ServiceError) as caught:
                    c.pages(
                        "courses/1/quizzes/assignment_overrides",
                        list_key="quiz_assignment_overrides",
                    )
                self.assertTrue(caught.exception.fatal)
                self.assertEqual(session.get.call_count, 1)

    def test_missing_wrapper_is_not_empty(self):
        for payload in (
            {},
            {"quiz_assignment_overrides": None},
            {"quiz_assignment_overrides": [{}, None]},
        ):
            with self.subTest(payload=payload):
                c, _ = client_with(response(payload=payload))
                with self.assertRaisesRegex(r.ServiceError, "Quiz override date lookup"):
                    c.pages(
                        "courses/1/quizzes/assignment_overrides",
                        list_key="quiz_assignment_overrides",
                    )


class OverrideTests(unittest.TestCase):
    def scan(self, *, dates=None, submissions=None, quiz=None, records=None, override_error=None):
        item = {
            "id": 9,
            "title": "Fictional quiz",
            "due_at": iso(timedelta(days=1)),
            "all_dates": [
                {"base": True, "due_at": iso(timedelta(days=1))},
                {"id": 41, "due_at": iso(timedelta(days=4))},
            ],
        }
        if quiz:
            item.update(quiz)
        if records is None:
            records = [
                {
                    "quiz_id": "9",
                    "due_dates": [
                        {"due_at": dates if dates is not None else iso(timedelta(days=1))}
                    ],
                }
            ]

        def pages(path, params=None, **kwargs):
            if path == "courses":
                return [{"id": 1}]
            if path.endswith("/assignments"):
                return []
            if path.endswith("/quizzes"):
                return [item, item]  # duplicates stay deduplicated
            if path.endswith("/assignment_overrides"):
                self.assertEqual(params, {"quiz_assignment_overrides[][quiz_ids][]": 9})
                self.assertEqual(kwargs, {"list_key": "quiz_assignment_overrides"})
                if override_error:
                    raise override_error
                return records
            raise AssertionError(path)

        client = Mock()
        client.pages.side_effect = pages
        client.one.return_value = (
            submissions if submissions is not None else {"quiz_submissions": []}
        )
        return r.gather_deadlines(client, now=NOW), client

    def test_resolved_effective_dates_succeed_without_warnings(self):
        result, client = self.scan()
        self.assertEqual(result.status, r.ScanStatus.SUCCESS)
        self.assertEqual(len(result.deadlines), 1)
        self.assertFalse(result.deadlines[0].submission_unknown)
        self.assertEqual(client.one.call_count, 1)

    def test_sole_matching_base_date_needs_no_extra_endpoint(self):
        result, client = self.scan(
            quiz={"all_dates": [{"base": True, "due_at": iso(timedelta(days=1))}]},
            override_error=r.ServiceError("HTTP 404"),
        )
        self.assertEqual(result.status, r.ScanStatus.SUCCESS)
        self.assertEqual(len(result.deadlines), 1)
        self.assertFalse(
            any(
                call.args[0].endswith("assignment_overrides")
                for call in client.pages.call_args_list
            )
        )

    def test_mismatched_base_dates_require_resolution(self):
        result, client = self.scan(
            quiz={"all_dates": [{"base": True, "due_at": iso(timedelta(days=3))}]}
        )
        self.assertEqual(result.status, r.ScanStatus.SUCCESS)
        self.assertTrue(
            any(
                call.args[0].endswith("assignment_overrides")
                for call in client.pages.call_args_list
            )
        )

    def test_effective_date_preferred_over_generic_and_all_dates(self):
        result, _ = self.scan(dates=iso(timedelta(days=4)))
        self.assertEqual(result.status, r.ScanStatus.SUCCESS)
        self.assertEqual(result.deadlines[0].due_at, NOW + timedelta(days=4))

    def test_effective_date_can_move_into_and_out_of_window(self):
        result, _ = self.scan(
            dates=iso(timedelta(days=2)), quiz={"due_at": iso(timedelta(days=10))}
        )
        self.assertEqual(len(result.deadlines), 1)
        result, _ = self.scan(dates=iso(timedelta(days=10)))
        self.assertEqual(result.status, r.ScanStatus.SUCCESS)
        self.assertEqual(result.deadlines, [])

    def test_null_effective_due_date_never_falls_back(self):
        result, _ = self.scan(records=[{"quiz_id": 9, "due_dates": [{"due_at": None}]}])
        self.assertEqual(result.status, r.ScanStatus.SUCCESS)
        self.assertEqual(result.deadlines, [])

    def test_missing_restricted_and_ambiguous_effective_dates_fail(self):
        values = (
            [],
            [None],
            [{"quiz_id": 10, "due_dates": [{"due_at": iso(timedelta(days=1))}]}],
            [{"quiz_id": 9, "due_dates": []}],
            [{"quiz_id": 9, "due_dates": [{}, {}]}],
            [{"quiz_id": 9, "due_dates": [{}]}],
            [{"quiz_id": 9, "due_dates": [{"due_at": "bad"}]}],
            [{"quiz_id": 9, "due_dates": None}],
            [{"quiz_id": True}],
            [{"quiz_id": 9}, {"quiz_id": 9}],
        )
        for records in values:
            with self.subTest(records=records):
                result, _ = self.scan(records=records)
                self.assertEqual(result.status, r.ScanStatus.FAILURE)
                self.assertEqual(result.deadlines, [])
                self.assertTrue(any("Quiz override date lookup" in d for d in result.diagnostics))
        for status in (403, 404):
            with self.subTest(status=status):
                result, _ = self.scan(
                    override_error=r.ServiceError(
                        f"Quiz override date lookup failed (HTTP {status})"
                    )
                )
                self.assertEqual(result.status, r.ScanStatus.FAILURE)
                self.assertEqual(result.deadlines, [])

    def test_fatal_override_error_is_failed(self):
        result, _ = self.scan(override_error=r.ServiceError("Unsafe pagination", fatal=True))
        self.assertEqual(result.status, r.ScanStatus.FAILURE)
        self.assertEqual(result.deadlines, [])

    def test_only_visible_to_overrides_resolves_even_without_all_dates(self):
        result, _ = self.scan(quiz={"all_dates": None, "only_visible_to_overrides": True})
        self.assertEqual(result.status, r.ScanStatus.SUCCESS)

    def test_completed_and_pending_review_still_filtered(self):
        for state in ("complete", "pending_review"):
            with self.subTest(state=state):
                result, _ = self.scan(submissions={"quiz_submissions": [{"workflow_state": state}]})
                self.assertEqual(result.status, r.ScanStatus.SUCCESS)
                self.assertEqual(result.deadlines, [])


class CurrentUserSubmissionTests(unittest.TestCase):
    def scan(self, payload, status=200):
        c, _ = client_with(
            response(payload=[{"id": 1}]),
            response(payload=[]),
            response(payload=[{"id": 2, "due_at": iso(timedelta(days=1))}]),
            response(status, payload=payload),
        )
        return r.gather_deadlines(c, now=NOW)

    def test_successful_empty_list_confirms_never_attempted(self):
        result = self.scan({"quiz_submissions": []})
        self.assertEqual(result.status, r.ScanStatus.SUCCESS)
        self.assertFalse(result.deadlines[0].submission_unknown)

    def test_unavailable_and_restricted_submissions_remain_unknown(self):
        for status in (403, 404):
            with self.subTest(status=status):
                result = self.scan({}, status)
                self.assertEqual(result.status, r.ScanStatus.SUCCESS)
                self.assertTrue(result.deadlines[0].submission_unknown)
                self.assertIn(f"Quiz submission lookup failed (HTTP {status})", result.diagnostics)

    def test_missing_fields_remain_unknown(self):
        for payload in ({}, {"quiz_submissions": None}, {"quiz_submissions": [{}]}):
            with self.subTest(payload=payload):
                result = self.scan(payload)
                self.assertEqual(result.status, r.ScanStatus.SUCCESS)
                self.assertTrue(result.deadlines[0].submission_unknown)

    def test_in_progress_is_incomplete_and_latest_attempt_wins(self):
        result = self.scan(
            {
                "quiz_submissions": [
                    {"workflow_state": "untaken", "started_at": iso(timedelta(hours=-1))}
                ]
            }
        )
        self.assertEqual(result.status, r.ScanStatus.SUCCESS)
        self.assertFalse(result.deadlines[0].submission_unknown)
        result = self.scan(
            {
                "quiz_submissions": [
                    {"attempt": 1, "workflow_state": "complete"},
                    {"attempt": 2, "workflow_state": "untaken"},
                ]
            }
        )
        self.assertEqual(result.status, r.ScanStatus.SUCCESS)
        self.assertEqual(len(result.deadlines), 1)

    def test_bad_credentials_fail_closed(self):
        self.assertEqual(self.scan({}, 401).status, r.ScanStatus.FAILURE)
