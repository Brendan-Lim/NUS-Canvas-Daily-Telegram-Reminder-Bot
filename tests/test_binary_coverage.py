"""Binary operational outcomes and optional coverage; fictional offline fixtures only."""

import os
import unittest
from datetime import timedelta
from unittest.mock import patch

from test_hardening import assignment, client_with, response
from test_reminder import NOW, FakeCanvas, iso

import reminder as r


def planner_item(qid=9, **changes):
    return {
        "plannable_type": "quiz",
        "plannable_id": str(qid),
        "course_id": 1,
        "plannable_date": iso(timedelta(days=2)),
        "planner_override": {"marked_complete": True, "dismissed": True},
        "submissions": {"graded": True},
        **changes,
    }


def quiz(**changes):
    return {
        "id": 9,
        "title": "Fictional practice quiz",
        "due_at": iso(timedelta(days=1)),
        **changes,
    }


def fallback_client(*extra, assignments=None, listing_status=404, tabs=None, planner=None):
    return client_with(
        response(payload=[{"id": 1, "course_code": "TEST101"}]),
        response(payload=[] if assignments is None else assignments),
        response(listing_status),
        response(payload=[] if tabs is None else tabs),
        response(payload=[] if planner is None else planner),
        *extra,
    )


class OptionalCoverageTests(unittest.TestCase):
    def test_quiz_listing_404_is_success_with_coverage_warning(self):
        c, session = fallback_client(assignments=[assignment()])
        result = r.gather_deadlines(c, now=NOW)
        self.assertEqual(result.status, r.ScanStatus.SUCCESS)
        self.assertEqual(len(result.deadlines), 1)
        self.assertEqual(result.warnings, [r.CoverageWarning.CLASSIC_QUIZZES])
        self.assertIn("Course quiz listing failed (HTTP 404)", result.diagnostics)
        self.assertIn("Standalone Classic Quiz coverage", r.make_messages(result, now=NOW)[0])
        self.assertEqual(session.get.call_count, 5)

    def test_quiz_listing_403_is_optional_after_required_assignment_success(self):
        c, _ = fallback_client(listing_status=403)
        result = r.gather_deadlines(c, now=NOW)
        self.assertEqual(result.status, r.ScanStatus.SUCCESS)
        self.assertIn(r.CoverageWarning.CLASSIC_QUIZZES, result.warnings)

    @patch("reminder.time.sleep")
    def test_exhausted_transient_quiz_listing_is_disclosed_optional_coverage(self, sleep):
        for status in (429, 500, 502, 503, 504):
            with self.subTest(status=status):
                c, session = client_with(
                    response(payload=[{"id": 1}]),
                    response(payload=[]),
                    response(status),
                    response(status),
                    response(status),
                    response(payload=[]),
                    response(payload=[]),
                )
                result = r.gather_deadlines(c, now=NOW)
                self.assertEqual(result.status, r.ScanStatus.SUCCESS)
                self.assertIn(r.CoverageWarning.CLASSIC_QUIZZES, result.warnings)
                self.assertEqual(session.get.call_count, 7)
                self.assertIn(f"HTTP {status}", " ".join(result.diagnostics))

    def test_hidden_missing_or_visible_quiz_tab_does_not_prove_quiz_absence(self):
        for tabs, diagnostic in (
            ([{"id": "quizzes", "hidden": True}], "restricted or hidden"),
            ([{"id": "quizzes", "visibility": "none"}], "restricted or hidden"),
            ([], "not available"),
            ([{"id": "quizzes"}], "failure remains unexplained"),
        ):
            with self.subTest(tabs=tabs):
                c, _ = fallback_client(tabs=tabs)
                result = r.gather_deadlines(c, now=NOW)
                self.assertEqual(result.status, r.ScanStatus.SUCCESS)
                self.assertTrue(any(diagnostic in d for d in result.diagnostics))
                message = r.make_messages(result, now=NOW)[0]
                self.assertIn("not an all clear", message)
                self.assertNotIn("No incomplete Canvas", message)

    def test_unavailable_tabs_do_not_prevent_planner_discovery(self):
        c, _ = client_with(
            response(payload=[{"id": 1}]),
            response(payload=[]),
            response(404),
            response(403),
            response(payload=[planner_item()]),
            response(payload=quiz()),
            response(payload={"quiz_submissions": []}),
        )
        result = r.gather_deadlines(c, now=NOW)
        self.assertEqual(result.status, r.ScanStatus.SUCCESS)
        self.assertEqual(len(result.deadlines), 1)
        self.assertIn("Course navigation lookup failed (HTTP 403)", result.diagnostics)

    def test_unavailable_planner_warns_without_failing_required_coverage(self):
        c, _ = client_with(
            response(payload=[{"id": 1}]),
            response(payload=[]),
            response(404),
            response(payload=[]),
            response(404),
        )
        result = r.gather_deadlines(c, now=NOW)
        self.assertEqual(result.status, r.ScanStatus.SUCCESS)
        self.assertIn(r.CoverageWarning.PLANNER, result.warnings)
        self.assertIn("optional Planner", r.make_messages(result, now=NOW)[0])

    def test_planner_recovers_quiz_ignoring_toggles_grades_and_planner_date(self):
        c, session = fallback_client(
            response(payload=quiz()),
            response(payload={"quiz_submissions": []}),
            planner=[planner_item()],
        )
        result = r.gather_deadlines(c, now=NOW)
        self.assertEqual(result.status, r.ScanStatus.SUCCESS)
        self.assertEqual(result.deadlines[0].due_at, NOW + timedelta(days=1))
        self.assertFalse(result.deadlines[0].submission_unknown)
        params = session.get.call_args_list[4].kwargs["params"]
        self.assertEqual(params["context_codes[]"], "course_1")
        self.assertNotIn("filter", params)
        self.assertIn("start_date", params)
        self.assertIn("end_date", params)
        self.assertEqual(
            session.get.call_args_list[5].args[0], r.CANVAS_ORIGIN + "/api/v1/courses/1/quizzes/9"
        )
        self.assertIn(r.CoverageWarning.CLASSIC_QUIZZES, result.warnings)

    def test_planner_and_linked_assignments_deduplicate_even_when_completed(self):
        for state in ("unsubmitted", "submitted"):
            with self.subTest(state=state):
                c, session = fallback_client(
                    assignments=[assignment(quiz_id=9, submission={"workflow_state": state})],
                    planner=[planner_item(), planner_item()],
                )
                result = r.gather_deadlines(c, now=NOW)
                self.assertEqual(result.status, r.ScanStatus.SUCCESS)
                self.assertEqual(len(result.deadlines), 1 if state == "unsubmitted" else 0)
                self.assertEqual(
                    session.get.call_count, 5
                )  # no duplicate details/submission lookups

    def test_planner_linked_assignment_uses_required_effective_date(self):
        c, _ = fallback_client(
            response(payload=quiz(assignment_id=3)),
            response(payload=assignment(id=3, due_at=iso(timedelta(days=4)))),
            planner=[planner_item()],
        )
        result = r.gather_deadlines(c, now=NOW)
        self.assertEqual(result.status, r.ScanStatus.SUCCESS)
        self.assertEqual(result.deadlines[0].due_at, NOW + timedelta(days=4))
        self.assertEqual(result.deadlines[0].kind, "Quiz")

    def test_planner_duplicate_standalone_quizzes_only_remind_once(self):
        c, session = fallback_client(
            response(payload=quiz()),
            response(payload={"quiz_submissions": []}),
            planner=[planner_item(), planner_item()],
        )
        result = r.gather_deadlines(c, now=NOW)
        self.assertEqual(len(result.deadlines), 1)
        self.assertEqual(session.get.call_count, 7)

    def test_planner_completed_quiz_uses_actual_submission_evidence(self):
        c, _ = fallback_client(
            response(payload=quiz()),
            response(payload={"quiz_submissions": [{"workflow_state": "complete"}]}),
            planner=[planner_item(planner_override={"marked_complete": False})],
        )
        result = r.gather_deadlines(c, now=NOW)
        self.assertEqual(result.status, r.ScanStatus.SUCCESS)
        self.assertEqual(result.deadlines, [])

    def test_planner_pagination_includes_later_page_without_using_other_types(self):
        c, session = client_with(
            response(payload=[{"id": 1}]),
            response(payload=[]),
            response(404),
            response(payload=[]),
            response(
                payload=[planner_item(plannable_type="planner_note")],
                links={"next": {"url": r.CANVAS_ORIGIN + "/api/v1/planner/items?page=2"}},
            ),
            response(payload=[planner_item()]),
            response(payload=quiz()),
            response(payload={"quiz_submissions": []}),
        )
        result = r.gather_deadlines(c, now=NOW)
        self.assertEqual(result.status, r.ScanStatus.SUCCESS)
        self.assertEqual(len(result.deadlines), 1)
        self.assertIsNone(session.get.call_args_list[5].kwargs["params"])

    def test_malformed_cross_course_and_nonquiz_planner_items_are_not_trusted(self):
        for item in (
            planner_item(course_id=2),
            planner_item(course_id=True),
            planner_item(plannable_id=True),
            planner_item(plannable_id="../../other"),
            planner_item(plannable_type="assignment"),
        ):
            with self.subTest(item=item):
                c, session = fallback_client(planner=[item])
                result = r.gather_deadlines(c, now=NOW)
                self.assertEqual(result.status, r.ScanStatus.SUCCESS)
                self.assertEqual(result.deadlines, [])
                self.assertEqual(session.get.call_count, 5)

    def test_unknown_submission_does_not_fail_known_quiz_deadline(self):
        c, _ = fallback_client(response(payload=quiz()), response(404), planner=[planner_item()])
        result = r.gather_deadlines(c, now=NOW)
        self.assertEqual(result.status, r.ScanStatus.SUCCESS)
        self.assertTrue(result.deadlines[0].submission_unknown)
        self.assertIn(r.CoverageWarning.SUBMISSION, result.warnings)
        self.assertIn("submission unverified", r.make_messages(result, now=NOW)[0])


class RequiredFailureTests(unittest.TestCase):
    def test_required_assignments_http_errors_never_become_optional(self):
        for status in (401, 403, 404):
            with self.subTest(status=status):
                c, session = client_with(response(payload=[{"id": 1}]), response(status))
                result = r.gather_deadlines(c, now=NOW)
                self.assertEqual(result.status, r.ScanStatus.FAILURE)
                self.assertEqual(result.warnings, [])
                self.assertEqual(session.get.call_count, 2)

    @patch("reminder.time.sleep")
    def test_required_assignment_retry_exhaustion_fails(self, sleep):
        for status in (429, 500, 502, 503, 504):
            with self.subTest(status=status):
                c, session = client_with(
                    response(payload=[{"id": 1}]),
                    response(status),
                    response(status),
                    response(status),
                )
                result = r.gather_deadlines(c, now=NOW)
                self.assertEqual(result.status, r.ScanStatus.FAILURE)
                self.assertEqual(result.warnings, [])
                self.assertEqual(session.get.call_count, 4)

    def test_missing_required_assignment_data_fails(self):
        for data in ({}, {"id": 1}, assignment(due_at="invalid")):
            with self.subTest(data=data):
                result = r.gather_deadlines(FakeCanvas([data], []), now=NOW)
                self.assertEqual(result.status, r.ScanStatus.FAILURE)
                self.assertEqual(result.deadlines, [])

    def test_known_planner_quiz_dates_cannot_be_fabricated(self):
        for payload in ({"id": 9}, quiz(due_at="invalid"), {"id": True}):
            with self.subTest(payload=payload):
                c, _ = fallback_client(response(payload=payload), planner=[planner_item()])
                result = r.gather_deadlines(c, now=NOW)
                self.assertEqual(result.status, r.ScanStatus.FAILURE)
                self.assertEqual(result.deadlines, [])

    def test_linked_assignment_boolean_id_is_not_a_matching_integer(self):
        c, _ = client_with(
            response(payload=[{"id": 1}]),
            response(payload=[]),
            response(payload=[quiz(assignment_id=1)]),
            response(payload=assignment(id=True)),
        )
        result = r.gather_deadlines(c, now=NOW)
        self.assertEqual(result.status, r.ScanStatus.FAILURE)
        self.assertEqual(result.deadlines, [])

    def test_unresolved_student_specific_quiz_dates_fail(self):
        c, _ = fallback_client(
            response(payload=quiz(only_visible_to_overrides=True)),
            response(payload={"quiz_assignment_overrides": [{"quiz_id": 9, "due_dates": []}]}),
            planner=[planner_item()],
        )
        result = r.gather_deadlines(c, now=NOW)
        self.assertEqual(result.status, r.ScanStatus.FAILURE)
        self.assertEqual(result.deadlines, [])

    def test_invalid_credentials_and_redirects_fail_on_optional_endpoints(self):
        for stage in ("quizzes", "tabs", "planner"):
            for status in (401, 302):
                with self.subTest(stage=stage, status=status):
                    replies = [response(payload=[{"id": 1}]), response(payload=[])]
                    if stage != "quizzes":
                        replies.append(response(404))
                    if stage == "planner":
                        replies.append(response(payload=[]))
                    c, session = client_with(*replies, response(status))
                    result = r.gather_deadlines(c, now=NOW)
                    self.assertEqual(result.status, r.ScanStatus.FAILURE)
                    self.assertEqual(result.deadlines, [])
                    self.assertEqual(session.get.call_count, len(replies) + 1)

    def test_optional_pagination_still_fails_closed(self):
        for path, links in (
            ("tabs", {"next": {"url": "https://evil.invalid/api/v1/tabs"}}),
            ("planner", {"next": {"url": r.CANVAS_ORIGIN + "/api/v1/planner/items"}}),
        ):
            with self.subTest(path=path):
                replies = [response(payload=[{"id": 1}]), response(payload=[]), response(404)]
                if path == "planner":
                    replies.append(response(payload=[]))
                c, _ = client_with(*replies, response(payload=[], links=links))
                self.assertEqual(r.gather_deadlines(c, now=NOW).status, r.ScanStatus.FAILURE)


class BinaryWorkflowTests(unittest.TestCase):
    ENV = {
        "CANVAS_TOKEN": "fictional",
        "TELEGRAM_BOT_TOKEN": "fictional",
        "TELEGRAM_CHAT_ID": "123",
    }

    def test_only_two_status_values_exist(self):
        self.assertEqual({status.value for status in r.ScanStatus}, {"SUCCESS", "FAILURE"})

    def test_successful_delivery_with_optional_warnings_exits_zero(self):
        c, _ = fallback_client(response(payload=quiz()), response(404), planner=[planner_item()])
        result = r.gather_deadlines(c, now=NOW)
        with (
            patch.dict(os.environ, self.ENV, clear=True),
            patch("reminder.gather_deadlines", return_value=result),
            patch("reminder.send_telegram") as send,
            self.assertLogs(r.LOG, "INFO") as logs,
        ):
            self.assertEqual(r.main(), 0)
            self.assertIn("Standalone Classic Quiz coverage", send.call_args.args[0])
            self.assertIn("Reminder SUCCESS", " ".join(logs.output))

    def test_telegram_failure_turns_successful_processing_into_failure(self):
        result = r.ScanResult(r.ScanStatus.SUCCESS, [], [], [r.CoverageWarning.CLASSIC_QUIZZES])
        with (
            patch.dict(os.environ, self.ENV, clear=True),
            patch("reminder.gather_deadlines", return_value=result),
            patch(
                "reminder.send_telegram",
                side_effect=r.ServiceError("Telegram delivery failed (HTTP 500)"),
            ) as send,
            self.assertLogs(r.LOG, "ERROR") as logs,
        ):
            self.assertEqual(r.main(), 1)
            send.assert_called_once()
            self.assertIn("Reminder FAILURE", " ".join(logs.output))
            self.assertNotIn("Reminder SUCCESS", " ".join(logs.output))

    def test_failed_required_processing_exits_nonzero_even_when_alert_delivered(self):
        result = r.ScanResult(
            r.ScanStatus.FAILURE, [], ["Course assignment listing failed (HTTP 404)"]
        )
        with (
            patch.dict(os.environ, self.ENV, clear=True),
            patch("reminder.gather_deadlines", return_value=result),
            patch("reminder.send_telegram") as send,
        ):
            self.assertEqual(r.main(), 1)
            self.assertIn("could not be completed", send.call_args.args[0])
            self.assertNotIn("No incomplete", send.call_args.args[0])
