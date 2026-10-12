"""One-shot NUS Canvas -> Telegram deadline digest; no server or database."""

from __future__ import annotations

import html
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests

CANVAS_ORIGIN = "https://canvas.nus.edu.sg"
SGT = timezone(timedelta(hours=8), "SGT")
LOG = logging.getLogger(__name__)
REQUEST_TIMEOUT = (10, 30)
MAX_PAGES = 20
MAX_TELEGRAM_CHARS = 4096
DEFAULT_DAYS = 7
DEFAULT_URGENT_HOURS = 48


class ServiceError(Exception):
    """Safe, credential-free operational failure message."""


@dataclass(frozen=True)
class Deadline:
    kind: str
    course: str
    title: str
    due_at: datetime
    url: str


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timezone missing")
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        raise ServiceError("Canvas returned an invalid timestamp") from None


def is_submitted(submission: object) -> bool:
    """Do not mistake an instructor's grade with no student attempt for submission."""
    if not isinstance(submission, dict):
        return False
    if submission.get("submitted_at"):
        return True
    if submission.get("workflow_state") in ("submitted", "pending_review", "complete"):
        return True
    # A positive student submission attempt counts, even if grading is pending.
    attempt = submission.get("attempt")
    return isinstance(attempt, int) and not isinstance(attempt, bool) and attempt > 0


def quiz_is_submitted(payload: object) -> bool:
    if isinstance(payload, dict):
        records = payload.get("quiz_submissions", payload.get("quiz_submission", []))
    else:
        records = payload
    if isinstance(records, dict):
        records = [records]
    if not isinstance(records, list):
        return False
    return any(
        isinstance(item, dict)
        and (item.get("finished_at") or item.get("workflow_state") in ("complete", "pending_review"))
        for item in records
    )


def _in_window(due: datetime | None, now: datetime, horizon_days: int) -> bool:
    return due is not None and now < due <= now + timedelta(days=horizon_days)


def _valid_canvas_url(url: str) -> bool:
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


class CanvasClient:
    def __init__(self, token: str, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})

    def _request(self, url: str, params: dict | None = None, *, not_found_ok: bool = False):
        if not _valid_canvas_url(url):
            raise ServiceError("Canvas supplied an unsafe pagination URL")
        try:
            # Never automatically follow redirects with an access token.
            response = self.session.get(
                url, params=params, timeout=REQUEST_TIMEOUT, allow_redirects=False
            )
        except requests.RequestException:
            raise ServiceError("Unable to reach NUS Canvas") from None
        if response.status_code == 404 and not_found_ok:
            return None
        if response.status_code == 401:
            raise ServiceError("Canvas API token is invalid or expired")
        if not 200 <= response.status_code < 300:
            raise ServiceError(f"Canvas API request failed (HTTP {response.status_code}) at {urlparse(url).path}")
        return response

    def one(self, path: str, *, not_found_ok: bool = False):
        result = self._request(f"{CANVAS_ORIGIN}/api/v1/{path.lstrip('/')}", not_found_ok=not_found_ok)
        if result is None:
            return None
        try:
            return result.json()
        except ValueError:
            raise ServiceError("Canvas returned invalid JSON") from None

    def pages(self, path: str, params: dict | None = None, *, not_found_ok: bool = False) -> list[dict]:
        url = f"{CANVAS_ORIGIN}/api/v1/{path.lstrip('/')}"
        initial_params = {"per_page": 100, **(params or {})}
        seen: set[str] = set()
        collected: list[dict] = []
        for _ in range(MAX_PAGES):
            if url in seen:
                raise ServiceError("Canvas pagination contains a loop")
            seen.add(url)
            response = self._request(url, params=initial_params, not_found_ok=not_found_ok and len(seen) == 1)
            if response is None:
                LOG.warning("Optional Canvas endpoint unavailable (HTTP 404): %s", urlparse(url).path)
                return []
            try:
                page = response.json()
            except ValueError:
                raise ServiceError("Canvas returned invalid JSON") from None
            if not isinstance(page, list):
                raise ServiceError("Unexpected Canvas list response")
            collected.extend(page)
            next_url = response.links.get("next", {}).get("url")
            if not next_url:
                return collected
            if not _valid_canvas_url(next_url):
                raise ServiceError("Canvas supplied an unsafe pagination URL")
            url, initial_params = next_url, None
        raise ServiceError("Canvas exceeded the pagination safety limit")


def gather_deadlines(client: CanvasClient, *, now: datetime, days: int = 7) -> list[Deadline]:
    if now.tzinfo is None:
        raise ValueError("now must be timezone aware")
    output: list[Deadline] = []
    courses = client.pages("courses", {"enrollment_state": "active", "enrollment_type": "student"})
    for course in courses:
        course_id = course.get("id")
        if not isinstance(course_id, int) or isinstance(course_id, bool):
            continue
        course_name = str(course.get("course_code") or course.get("name") or f"Course {course_id}")
        assignments = client.pages(
            f"courses/{course_id}/assignments",
            {"include[]": "submission", "override_assignment_dates": "true"},
        )
        seen_quiz_ids: set[int] = set()
        linked_assignment_ids: set[int] = set()
        for a in assignments:
            aid = a.get("id")
            if not isinstance(aid, int) or isinstance(aid, bool):
                continue
            linked_assignment_ids.add(aid)
            due = parse_dt(a.get("due_at"))
            qid = a.get("quiz_id")
            if isinstance(qid, int) and not isinstance(qid, bool):
                seen_quiz_ids.add(qid)
            if not _in_window(due, now, days) or is_submitted(a.get("submission")):
                continue
            is_quiz = bool(qid) or "online_quiz" in (a.get("submission_types") or [])
            output.append(Deadline(
                "Quiz" if is_quiz else "Assignment", course_name,
                str(a.get("name") or "Untitled"), due,
                f"{CANVAS_ORIGIN}/courses/{course_id}/assignments/{aid}",
            ))

        # Classic standalone/ungraded quizzes can be absent from /assignments.
        for q in client.pages(f"courses/{course_id}/quizzes", not_found_ok=True):
            qid = q.get("id")
            if not isinstance(qid, int) or isinstance(qid, bool):
                continue
            if qid in seen_quiz_ids or q.get("assignment_id") in linked_assignment_ids:
                continue
            if q.get("published") is False:
                continue
            due = parse_dt(q.get("due_at"))
            if not _in_window(due, now, days):
                continue
            submissions = client.one(f"courses/{course_id}/quizzes/{qid}/submissions", not_found_ok=True)
            if submissions is not None and quiz_is_submitted(submissions):
                continue
            output.append(Deadline(
                "Quiz", course_name, str(q.get("title") or "Untitled quiz"), due,
                f"{CANVAS_ORIGIN}/courses/{course_id}/quizzes/{qid}",
            ))
    output.sort(key=lambda d: (d.due_at, d.course.lower(), d.title.lower()))
    return output


def _short(value: str, limit: int = 90) -> str:
    # Removes control characters and prevents huge/unfriendly Telegram lines.
    clean = " ".join(value.split())
    return clean if len(clean) <= limit else clean[:limit - 1] + "…"


def _html_line(item: Deadline) -> str:
    label = html.escape(f"{_short(item.course, 45)} — {_short(item.title)}")
    url = html.escape(item.url, quote=True)
    due = item.due_at.astimezone(SGT).strftime("%a %d %b, %I:%M %p").replace(" 0", " ")
    return f'• [{item.kind}] <a href="{url}">{label}</a>\n  Due: {due} SGT'


def make_messages(
    items: list[Deadline], *, now: datetime, days: int = DEFAULT_DAYS,
    urgent_hours: int = DEFAULT_URGENT_HOURS
) -> list[str]:
    date_str = now.astimezone(SGT).strftime("%a, %d %b %Y")
    header = f"📅 <b>NUS Canvas daily digest</b> · {date_str} (SGT)"
    urgent = [d for d in items if d.due_at <= now + timedelta(hours=urgent_hours)]
    later = [d for d in items if d.due_at > now + timedelta(hours=urgent_hours)]
    lines = []
    if not items:
        return [f"{header}\n\n✅ No incomplete Canvas assignments or quizzes due in the next {days} days."]
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
        if len(candidate) > MAX_TELEGRAM_CHARS:
            messages.append(current)
            current = header + " (continued)\n" + line
            if len(current) > MAX_TELEGRAM_CHARS:
                raise ServiceError("A formatted deadline item is too long")
        else:
            current = candidate
    messages.append(current)
    return messages


def send_telegram(message: str, *, bot_token: str, chat_id: str, session: requests.Session | None = None):
    transport = session or requests.Session()
    try:
        response = transport.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={
                "chat_id": chat_id, "text": message, "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=REQUEST_TIMEOUT, allow_redirects=False,
        )
    except requests.RequestException:
        raise ServiceError("Unable to reach the Telegram API") from None
    if response.status_code != 200:
        raise ServiceError(f"Telegram delivery failed (HTTP {response.status_code})")
    try:
        payload = response.json()
    except ValueError:
        raise ServiceError("Telegram returned invalid JSON") from None
    if payload.get("ok") is not True:
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
        urgent_hours = read_int_setting("URGENT_HOURS", DEFAULT_URGENT_HOURS, minimum=1, maximum=168)
        now = datetime.now(timezone.utc)
        items = gather_deadlines(CanvasClient(token), now=now, days=days)
        parts = make_messages(items, now=now, days=days, urgent_hours=urgent_hours)
        for part in parts:
            send_telegram(part, bot_token=bot_token, chat_id=chat_id)
        LOG.info("Daily digest sent (%d upcoming items, %d Telegram messages)", len(items), len(parts))
        return 0
    except ServiceError as e:
        LOG.error("Reminder failed: %s", e)
        return 1
    except Exception:
        LOG.error("Reminder failed unexpectedly (details suppressed to protect secrets)")
        return 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    raise SystemExit(main())
