"""One-shot NUS Canvas -> Telegram deadline digest; no server or database."""

from __future__ import annotations

import html
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
from urllib.parse import urlparse

import requests

CANVAS_ORIGIN = "https://canvas.nus.edu.sg"
SGT = timezone(timedelta(hours=8), "SGT")
LOG = logging.getLogger(__name__)
REQUEST_TIMEOUT = (10, 30)
MAX_PAGES = 20
MAX_TELEGRAM_CHARS = 4096
MAX_ATTEMPTS = 3
MAX_BACKOFF = 20
SCAN_BUDGET_SECONDS = 300
DEFAULT_DAYS = 7
DEFAULT_URGENT_HOURS = 48


class ServiceError(Exception):
    """Safe, credential-free operational failure message."""

    def __init__(self, message: str, *, fatal: bool = False):
        super().__init__(message)
        self.fatal = fatal


class SubmissionState(str, Enum):
    SUBMITTED = "submitted"
    INCOMPLETE = "incomplete"
    EXCUSED = "excused"
    UNKNOWN = "unknown"


class ScanStatus(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


@dataclass
class ScanResult:
    status: ScanStatus
    deadlines: list[Deadline]
    diagnostics: list[str]


@dataclass(frozen=True)
class Deadline:
    kind: str
    course: str
    title: str
    due_at: datetime
    url: str
    submission_unknown: bool = False


def parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        if not isinstance(value, str) or not value:
            raise ValueError("invalid timestamp")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timezone missing")
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        raise ServiceError("Canvas returned an invalid timestamp") from None


def submission_state(submission: object) -> SubmissionState:
    """Use current submission evidence, never a grade or historical attempt alone."""
    if not isinstance(submission, dict):
        return SubmissionState.UNKNOWN
    if submission.get("excused") is True:
        return SubmissionState.EXCUSED
    if submission.get("redo_request") is True or submission.get("workflow_state") == "unsubmitted":
        return SubmissionState.INCOMPLETE
    if submission.get("workflow_state") not in (None, "graded", "submitted", "pending_review"):
        return SubmissionState.UNKNOWN
    try:
        timestamp = parse_dt(submission.get("submitted_at"))
    except ServiceError:
        return SubmissionState.UNKNOWN
    if submission.get("workflow_state") in ("submitted", "pending_review") or timestamp:
        return SubmissionState.SUBMITTED
    attempt = submission.get("attempt")
    if (
        submission.get("workflow_state") == "graded"
        and _id(attempt)
        and isinstance(submission.get("submission_type"), str)
        and submission.get("submission_type")
        in {
            "online_text_entry",
            "online_url",
            "online_upload",
            "online_quiz",
            "media_recording",
            "basic_lti_launch",
            "student_annotation",
        }
    ):
        return SubmissionState.SUBMITTED
    return SubmissionState.UNKNOWN


def is_submitted(submission: object) -> bool:
    return submission_state(submission) == SubmissionState.SUBMITTED


def quiz_submission_state(payload: object) -> SubmissionState:
    # Use the current-user /submission endpoint, not a course-wide grade listing.
    records = payload.get("quiz_submissions") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        return SubmissionState.UNKNOWN
    if not records:
        return SubmissionState.INCOMPLETE
    if not all(isinstance(item, dict) for item in records):
        return SubmissionState.UNKNOWN
    if len(records) > 1:
        if not all(_id(item.get("attempt")) for item in records):
            return SubmissionState.UNKNOWN
        highest = max(item["attempt"] for item in records)
        records = [item for item in records if item["attempt"] == highest]
        if len(records) != 1:
            return SubmissionState.UNKNOWN
    item = records[0]
    if (
        item.get("workflow_state") in ("untaken", "settings_only")
        or item.get("overdue_and_needs_submission") is True
    ):
        return SubmissionState.INCOMPLETE
    if item.get("workflow_state") == "preview":
        return SubmissionState.UNKNOWN
    if item.get("workflow_state") not in (None, "complete", "pending_review"):
        return SubmissionState.UNKNOWN
    try:
        finished = parse_dt(item.get("finished_at"))
    except ServiceError:
        return SubmissionState.UNKNOWN
    if finished or item.get("workflow_state") in ("complete", "pending_review"):
        return SubmissionState.SUBMITTED
    return SubmissionState.UNKNOWN


def quiz_is_submitted(payload: object) -> bool:
    return quiz_submission_state(payload) == SubmissionState.SUBMITTED


def _id(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _in_window(due: datetime | None, now: datetime, horizon_days: int) -> bool:
    return due is not None and now < due <= now + timedelta(days=horizon_days)


def _valid_canvas_url(url: str) -> bool:
    try:
        if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
            return False
        parsed = urlparse(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname == "canvas.nus.edu.sg"
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
            and parsed.path.startswith("/api/v1/")
            and parsed.fragment == ""
        )
    except ValueError:
        return False


def _retry_delay(value: str | None, attempt: int) -> float:
    delay = min(2**attempt, MAX_BACKOFF)
    if value:
        try:
            seconds = float(value)
        except (ValueError, TypeError):
            try:
                seconds = (
                    parsedate_to_datetime(value) - datetime.now(timezone.utc)
                ).total_seconds()
            except (ValueError, TypeError, OverflowError):
                return delay
        if 0 <= seconds <= MAX_BACKOFF:
            delay = max(delay, seconds)
    return delay


def canvas_operation(path: str) -> str:
    """Fixed labels only: never return identifiers, URLs or query parameters."""
    path = path.removeprefix("/api/v1/")
    patterns = (
        (r"courses", "Course discovery"),
        (r"courses/[0-9]+/assignments", "Course assignment listing"),
        (r"courses/[0-9]+/quizzes", "Course quiz listing"),
        (r"courses/[0-9]+/quizzes/[0-9]+/submission", "Quiz submission lookup"),
        (r"courses/[0-9]+/assignments/[0-9]+", "Linked assignment lookup"),
        (r"courses/[0-9]+/quizzes/assignment_overrides", "Quiz override date lookup"),
    )
    return next(
        (label for pattern, label in patterns if re.fullmatch(pattern, path)),
        "Other Canvas operation",
    )


def quiz_override_due_at(records: list[dict], quiz_id: int) -> datetime | None:
    """Use the documented single set of effective dates for the current student."""
    matches = []
    for record in records:
        qid = record.get("quiz_id")
        if isinstance(qid, str) and re.fullmatch(r"[1-9][0-9]*", qid):
            qid = int(qid)
        if not _id(qid):
            raise ServiceError("Quiz override date lookup returned invalid identifiers")
        if qid == quiz_id:
            matches.append(record)
    if len(matches) != 1:
        raise ServiceError("Quiz override date lookup did not return one matching quiz")
    dates = matches[0].get("due_dates")
    if not isinstance(dates, list) or len(dates) != 1 or not isinstance(dates[0], dict):
        raise ServiceError("Quiz override date lookup returned missing or ambiguous student dates")
    if "due_at" not in dates[0]:
        raise ServiceError("Quiz override date lookup did not supply a deadline")
    try:
        return parse_dt(dates[0]["due_at"])
    except ServiceError:
        raise ServiceError("Quiz override date lookup returned an invalid timestamp") from None


def quiz_needs_override_lookup(quiz: dict) -> bool:
    """A matching, sole documented base date applies to all students already."""
    if quiz.get("only_visible_to_overrides") is True:
        return True
    dates = quiz.get("all_dates")
    if dates is None or dates == []:
        return False
    if (
        isinstance(dates, list)
        and len(dates) == 1
        and isinstance(dates[0], dict)
        and dates[0].get("base") is True
        and "due_at" in dates[0]
        and "due_at" in quiz
    ):
        try:
            return parse_dt(dates[0]["due_at"]) != parse_dt(quiz["due_at"])
        except ServiceError:
            pass
    return True


class CanvasClient:
    def __init__(self, token: str, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers.update(
            {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        )
        self.expires = time.monotonic() + SCAN_BUDGET_SECONDS

    def _request(self, url: str, params: dict | None = None, *, not_found_ok: bool = False):
        if not _valid_canvas_url(url):
            raise ServiceError("Canvas supplied an unsafe pagination URL", fatal=True)
        operation = canvas_operation(urlparse(url).path)
        for attempt in range(MAX_ATTEMPTS):
            remaining = self.expires - time.monotonic()
            if remaining < sum(REQUEST_TIMEOUT):
                raise ServiceError(f"{operation} failed: Canvas scan runtime limit reached")
            try:
                response = self.session.get(
                    url, params=params, timeout=REQUEST_TIMEOUT, allow_redirects=False
                )
            except requests.exceptions.SSLError:
                raise ServiceError(
                    f"{operation} failed: Canvas TLS verification failed", fatal=True
                ) from None
            except (requests.ConnectionError, requests.Timeout):
                if attempt == MAX_ATTEMPTS - 1:
                    raise ServiceError(
                        f"{operation} failed: unable to reach Canvas after bounded retries"
                    ) from None
                delay = _retry_delay(None, attempt)
            except requests.RequestException:
                raise ServiceError(f"{operation} failed safely") from None
            else:
                if response.status_code not in (429, 500, 502, 503, 504):
                    break
                if attempt == MAX_ATTEMPTS - 1:
                    raise ServiceError(
                        f"{operation} failed after bounded retries (HTTP {response.status_code})"
                    )
                delay = _retry_delay(response.headers.get("Retry-After"), attempt)
            if time.monotonic() + delay + sum(REQUEST_TIMEOUT) > self.expires:
                raise ServiceError(f"{operation} failed: Canvas scan runtime limit reached")
            time.sleep(delay)
        if response.status_code == 404 and not_found_ok:
            return None
        if response.status_code == 401:
            raise ServiceError(
                f"{operation} failed: Canvas API token is invalid or expired (HTTP 401)", fatal=True
            )
        if 300 <= response.status_code < 400:
            raise ServiceError(f"{operation} failed: Canvas redirect refused", fatal=True)
        if not 200 <= response.status_code < 300:
            raise ServiceError(f"{operation} failed (HTTP {response.status_code})")
        return response

    def one(self, path: str, params: dict | None = None, *, not_found_ok: bool = False):
        result = self._request(
            f"{CANVAS_ORIGIN}/api/v1/{path.lstrip('/')}", params, not_found_ok=not_found_ok
        )
        if result is None:
            return None
        try:
            return result.json()
        except ValueError:
            raise ServiceError(
                f"{canvas_operation(path)} failed: Canvas returned invalid JSON"
            ) from None

    def pages(
        self,
        path: str,
        params: dict | None = None,
        *,
        not_found_ok: bool = False,
        list_key: str | None = None,
    ) -> list[dict]:
        url = f"{CANVAS_ORIGIN}/api/v1/{path.lstrip('/')}"
        initial_params = {"per_page": 100, **(params or {})}
        seen: set[str] = set()
        collected: list[dict] = []
        for _ in range(MAX_PAGES):
            if url in seen:
                raise ServiceError("Canvas pagination contains a loop", fatal=True)
            seen.add(url)
            response = self._request(
                url, params=initial_params, not_found_ok=not_found_ok and len(seen) == 1
            )
            if response is None:
                raise ServiceError(f"{canvas_operation(path)} unavailable (HTTP 404)")
            try:
                page = response.json()
            except ValueError:
                raise ServiceError(
                    f"{canvas_operation(path)} failed: Canvas returned invalid JSON"
                ) from None
            if list_key is not None:
                page = page.get(list_key) if isinstance(page, dict) else None
            if not isinstance(page, list) or not all(isinstance(item, dict) for item in page):
                raise ServiceError(
                    f"{canvas_operation(path)} failed: unexpected Canvas list response"
                )
            collected.extend(page)
            next_url = response.links.get("next", {}).get("url")
            if not next_url:
                return collected
            if not _valid_canvas_url(next_url):
                raise ServiceError("Canvas supplied an unsafe pagination URL", fatal=True)
            url, initial_params = next_url, None
        raise ServiceError("Canvas exceeded the pagination safety limit", fatal=True)


def gather_deadlines(client: CanvasClient, *, now: datetime, days: int = 7) -> ScanResult:
    if now.tzinfo is None:
        raise ValueError("now must be timezone aware")
    output: list[Deadline] = []
    diagnostics: list[str] = []
    checked = 0
    seen_courses: set[int] = set()

    def note(message: str):
        # Only fixed local messages or ServiceError messages from our client.
        if message not in diagnostics:
            diagnostics.append(message)

    def add(item: dict, course_id: int, course_name: str, *, quiz: bool = False):
        nonlocal checked
        aid = item.get("id")
        if not _id(aid):
            raise ServiceError("Canvas returned an invalid activity identifier")
        if item.get("published") is False:
            checked += 1
            return
        if "due_at" not in item:
            raise ServiceError("An activity deadline was unavailable")
        due = parse_dt(item["due_at"])
        if not _in_window(due, now, days):
            checked += 1
            return
        if quiz:
            try:
                # A documented 200 with an empty list confirms never attempted;
                # a 404 is unavailable information, not an empty submission.
                payload = client.one(f"courses/{course_id}/quizzes/{aid}/submission")
            except ServiceError as error:
                if error.fatal:
                    raise
                note(str(error))
                payload = None
            state = quiz_submission_state(payload)
            if state == SubmissionState.UNKNOWN:
                note("Quiz submission lookup could not verify the current attempt")
        else:
            state = submission_state(item.get("submission"))
        if state == SubmissionState.UNKNOWN:
            note("Some submission information could not be verified")
        else:
            checked += 1
        if state in (SubmissionState.SUBMITTED, SubmissionState.EXCUSED):
            return
        types = item.get("submission_types")
        if types is not None and (
            not isinstance(types, list) or not all(isinstance(t, str) for t in types)
        ):
            raise ServiceError("Canvas returned invalid submission types")
        is_quiz = (
            quiz
            or _id(item.get("quiz_id"))
            or item.get("is_quiz_assignment") is True
            or "online_quiz" in (types or [])
        )
        output.append(
            Deadline(
                "Quiz" if is_quiz else "Assignment",
                course_name,
                str(item.get("title" if quiz else "name") or "Untitled"),
                due,
                f"{CANVAS_ORIGIN}/courses/{course_id}/{'quizzes' if quiz else 'assignments'}/{aid}",
                state == SubmissionState.UNKNOWN,
            )
        )

    try:
        courses = client.pages(
            "courses", {"enrollment_state": "active", "enrollment_type": "student"}
        )
        if not isinstance(courses, list):
            raise ServiceError("Canvas returned invalid courses", fatal=True)
        for course in courses:
            if not isinstance(course, dict) or not _id(course.get("id")):
                note("A course could not be verified")
                continue
            course_id = course["id"]
            if course_id in seen_courses:
                continue
            seen_courses.add(course_id)
            course_name = str(course.get("course_code") or course.get("name") or "Course")
            linked_assignments: set[int] = set()
            seen_quizzes: set[int] = set()
            try:
                assignments = client.pages(
                    f"courses/{course_id}/assignments",
                    {"include[]": "submission", "override_assignment_dates": "true"},
                )
                if not assignments:
                    checked += 1
                for item in assignments:
                    try:
                        if not isinstance(item, dict) or not _id(item.get("id")):
                            raise ServiceError("Canvas returned an invalid assignment")
                        if item["id"] in linked_assignments:
                            continue
                        linked_assignments.add(item["id"])
                        if _id(item.get("quiz_id")):
                            seen_quizzes.add(item["quiz_id"])
                        add(item, course_id, course_name)
                    except ServiceError as error:
                        if error.fatal:
                            raise
                        note(str(error))
            except ServiceError as error:
                if error.fatal:
                    raise
                note(str(error))
            try:
                quizzes = client.pages(f"courses/{course_id}/quizzes")
                if not quizzes:
                    checked += 1
                for item in quizzes:
                    try:
                        if not isinstance(item, dict) or not _id(item.get("id")):
                            raise ServiceError("Canvas returned an invalid quiz")
                        assignment_id = item.get("assignment_id")
                        if assignment_id is not None and not _id(assignment_id):
                            raise ServiceError(
                                "Canvas returned an invalid linked assignment identifier"
                            )
                        if item["id"] in seen_quizzes or assignment_id in linked_assignments:
                            continue
                        seen_quizzes.add(item["id"])
                        if item.get("published") is False:
                            checked += 1
                            continue
                        # A linked quiz uses the assignment's student-specific dates;
                        # never fall back to its generic quiz date when that lookup fails.
                        if _id(item.get("assignment_id")):
                            assignment = client.one(
                                f"courses/{course_id}/assignments/{item['assignment_id']}",
                                {"include[]": "submission", "override_assignment_dates": "true"},
                            )
                            if (
                                not isinstance(assignment, dict)
                                or assignment.get("id") != item["assignment_id"]
                            ):
                                raise ServiceError("Linked quiz assignment could not be verified")
                            if assignment["id"] in linked_assignments:
                                continue
                            linked_assignments.add(assignment["id"])
                            assignment = {**assignment, "is_quiz_assignment": True}
                            add(assignment, course_id, course_name)
                        else:
                            # all_dates may contain only the base date; its existence
                            # alone does not establish uncertainty or multiple overrides.
                            if quiz_needs_override_lookup(item):
                                records = client.pages(
                                    f"courses/{course_id}/quizzes/assignment_overrides",
                                    {"quiz_assignment_overrides[][quiz_ids][]": item["id"]},
                                    list_key="quiz_assignment_overrides",
                                )
                                due = quiz_override_due_at(records, item["id"])
                                item = {**item, "due_at": due.isoformat() if due else None}
                            add(item, course_id, course_name, quiz=True)
                    except ServiceError as error:
                        if error.fatal:
                            raise
                        note(str(error))
            except ServiceError as error:
                if error.fatal:
                    raise
                note(str(error))
    except ServiceError as error:
        return ScanResult(ScanStatus.FAILED, [], [str(error)])
    output.sort(key=lambda d: (d.due_at, d.course.lower(), d.title.lower()))
    status = ScanStatus.PARTIAL if diagnostics else ScanStatus.COMPLETE
    if diagnostics and not (checked or output):
        status = ScanStatus.FAILED
    return ScanResult(status, output, diagnostics)


def _short(value: str, limit: int = 90) -> str:
    # Removes control characters and prevents huge/unfriendly Telegram lines.
    clean = " ".join(value.split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def _html_line(item: Deadline) -> str:
    label = html.escape(f"{_short(item.course, 45)} — {_short(item.title)}")
    url = html.escape(item.url, quote=True)
    due = item.due_at.astimezone(SGT).strftime("%a %d %b, %I:%M %p").replace(" 0", " ")
    unknown = " · submission unverified" if item.submission_unknown else ""
    return f'• [{item.kind}] <a href="{url}">{label}</a>\n  Due: {due} SGT{unknown}'


def telegram_length(value: str) -> int:
    # UTF-16 units also bound astral Unicode safely. Counting markup is conservative.
    return len(value.encode("utf-16-le")) // 2


def make_messages(
    items: list[Deadline] | ScanResult,
    *,
    now: datetime,
    days: int = DEFAULT_DAYS,
    urgent_hours: int = DEFAULT_URGENT_HOURS,
) -> list[str]:
    date_str = now.astimezone(SGT).strftime("%a, %d %b %Y")
    header = f"📅 <b>NUS Canvas daily digest</b> · {date_str} (SGT)"
    status = items.status if isinstance(items, ScanResult) else ScanStatus.COMPLETE
    if status == ScanStatus.FAILED:
        return [
            "⚠️ The daily Canvas scan could not be completed. Check Canvas directly for deadlines."
        ]
    if isinstance(items, ScanResult):
        items = items.deadlines
    warning = "⚠️ Partial scan: some courses, deadlines or submissions could not be verified. Check Canvas directly."
    urgent = [d for d in items if d.due_at <= now + timedelta(hours=urgent_hours)]
    later = [d for d in items if d.due_at > now + timedelta(hours=urgent_hours)]
    lines = []
    if status == ScanStatus.PARTIAL:
        lines.append(warning)
    if not items:
        if status == ScanStatus.PARTIAL:
            return [
                f"{header}\n\n{warning}\nNo upcoming items found in the information checked; this is not an all clear."
            ]
        return [
            f"{header}\n\n✅ No incomplete Canvas assignments or quizzes due in the next {days} days."
        ]
    if urgent:
        lines.append(f"🔴 <b>Due within {urgent_hours} hours</b>")
        lines.extend(_html_line(item) for item in urgent)
    if later:
        if lines:
            lines.append("")
        lines.append(f"🟠 <b>Later within {days} days</b>")
        lines.extend(_html_line(item) for item in later)
    lines.append("\n<i>Updates daily; check Canvas for the authoritative deadlines.</i>")

    messages = []
    current = header
    for line in lines:
        candidate = current + "\n" + line
        if telegram_length(candidate) > MAX_TELEGRAM_CHARS:
            messages.append(current)
            current = header + " (continued)\n" + line
            if telegram_length(current) > MAX_TELEGRAM_CHARS:
                raise ServiceError("A formatted deadline item is too long")
        else:
            current = candidate
    messages.append(current)
    return messages


def send_telegram(
    message: str, *, bot_token: str, chat_id: str, session: requests.Session | None = None
):
    if telegram_length(message) > MAX_TELEGRAM_CHARS:
        raise ServiceError("Telegram message exceeds the length limit")
    transport = session or requests.Session()
    try:
        response = transport.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=REQUEST_TIMEOUT,
            allow_redirects=False,
        )
    except requests.RequestException:
        raise ServiceError("Unable to reach the Telegram API") from None
    if response.status_code != 200:
        raise ServiceError(f"Telegram delivery failed (HTTP {response.status_code})")
    try:
        payload = response.json()
    except ValueError:
        raise ServiceError("Telegram returned invalid JSON") from None
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise ServiceError("Telegram did not confirm message delivery")


def read_int_setting(name: str, default: int, *, minimum: int, maximum: int) -> int:
    """Parse a safe, bounded public workflow variable (never a credential)."""
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        raise ServiceError(f"{name} must be a whole number") from None
    if not minimum <= value <= maximum:
        raise ServiceError(f"{name} must be between {minimum} and {maximum}")
    return value


def main() -> int:
    # Do not print token values or Canvas response bodies (course data is private).
    token = os.environ.get("CANVAS_TOKEN")
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not all((token, bot_token, chat_id)):
        LOG.error("Missing CANVAS_TOKEN, TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID secret")
        return 1
    try:
        days = read_int_setting("REMINDER_DAYS", DEFAULT_DAYS, minimum=1, maximum=30)
        urgent_hours = read_int_setting(
            "URGENT_HOURS", DEFAULT_URGENT_HOURS, minimum=1, maximum=168
        )
        now = datetime.now(timezone.utc)
        try:
            result = gather_deadlines(CanvasClient(token), now=now, days=days)
        except Exception:
            result = ScanResult(
                ScanStatus.FAILED, [], ["Unexpected scan failure; details suppressed"]
            )
        for diagnostic in result.diagnostics:
            LOG.warning("Canvas scan: %s", diagnostic)
        parts = make_messages(result, now=now, days=days, urgent_hours=urgent_hours)
        delivered = 0
        for part in parts:
            try:
                send_telegram(part, bot_token=bot_token, chat_id=chat_id)
            except ServiceError:
                LOG.error(
                    "Telegram confirmed %d of %d messages; remaining delivery stopped",
                    delivered,
                    len(parts),
                )
                raise
            delivered += 1
        LOG.info("Scan %s; Telegram confirmed %d messages", result.status.value, delivered)
        return 0 if result.status == ScanStatus.COMPLETE else 1
    except ServiceError as e:
        LOG.error("Reminder failed: %s", e)
        return 1
    except Exception:
        LOG.error("Reminder failed unexpectedly (details suppressed to protect secrets)")
        return 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    raise SystemExit(main())
