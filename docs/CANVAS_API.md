# Canvas API compatibility and live verification

The scan uses read-only NUS Canvas REST requests. Diagnostics name fixed operations,
never course/activity IDs, names, student identifiers, URLs, query parameters,
tokens, or response bodies. Tests use fictional data and mocked transport.

## Why course quiz listing can return HTTP 404

The [documented Classic Quiz listing](https://developerdocs.instructure.com/services/canvas/resources/quizzes)
is paginated: `GET /api/v1/courses/:course_id/quizzes`. In the reviewed
[upstream controller](https://github.com/instructure/canvas-lms/blob/1c9f0bb8013ed69c4f2efe11fd483025469b7e6c/app/controllers/quizzes/quizzes_api_controller.rb),
`index` requires course read access **and** `tab_enabled?(TAB_QUIZZES)`. The
[tab check](https://github.com/instructure/canvas-lms/blob/1c9f0bb8013ed69c4f2efe11fd483025469b7e6c/app/controllers/application_controller.rb)
returns JSON HTTP 404 when the Quiz tab is not available to the current user.
The single-quiz `show` action instead checks read access to the particular quiz;
it does not use that listing tab gate.

This establishes a possible explanation for the reported NUS listing 404, not its
confirmed cause. Course read permissions, enrollment access, navigation settings,
and institution/version configuration can affect availability. A course using
New Quizzes may expose those activities through assignments rather than Classic
Quiz listing; a 404 alone cannot establish the course's quiz configuration.

After a listing failure, the read-only, paginated
[Tabs API](https://developerdocs.instructure.com/services/canvas/resources/tabs)
provides safe diagnostics: the Quiz tab is listed, restricted/hidden, not returned,
or the tab lookup itself failed. These are navigation observations. The upstream
tab check includes hidden/unused tabs, so a `hidden` flag is not an exact test of
that gate. Neither a hidden nor an absent tab proves no quizzes exist. The bot
does not change course navigation or log any tab labels or URLs.

## Required coverage, optional coverage, and operational outcomes

| Safe diagnostic label | Supported operation | Failure handling |
| --- | --- | --- |
| Course discovery | List active student courses. | Required; failure fails the run. |
| Course assignment listing | Paginated assignments with submission data and student date overrides applied. | Required; failure fails the run while independent courses may still be checked. |
| Course quiz listing | Paginated Classic Quizzes. | Inaccessible listing is an optional coverage limitation only after required assignment retrieval succeeds. It is never interpreted as an empty quiz list. |
| Course navigation lookup | Paginated course Tabs API. | Optional diagnosis, not evidence of quiz absence. |
| Planner quiz supplement | Paginated, course-scoped current-user Planner items in a bounded date range. | Optional discovery after a quiz-listing failure; unavailable results leave a coverage warning. |
| Known quiz lookup | Retrieve a quiz discovered through Planner. | Once an activity is known, its record and required due date must be verifiable; failure fails the run. |
| Quiz submission lookup | Retrieve the current user's Classic Quiz submission. | Restricted/unavailable or unrecognized information leaves the known deadline visible as “submission unverified”. |
| Linked assignment lookup | Retrieve a quiz's assignment when absent from the assignment listing. | Required; no generic quiz-date fallback. |
| Quiz override date lookup | Retrieve current-user effective Classic Quiz dates. | Required dates must resolve uniquely; no generic fallback. |

The only final operational outcomes are **SUCCESS** and **FAILURE**. Successful
required processing plus confirmed Telegram delivery returns zero, even when
optional coverage/submission warnings exist. A success does not promise exhaustive
Canvas coverage. Coverage warnings are structured independently of the outcome
and appear in the digest. An empty result with warnings is explicitly **not an
all clear**.

Required retrieval failures, missing/malformed known activity dates, unresolved
student-specific dates, HTTP 401, unsafe redirects, TLS verification failures,
and unsafe/looping/excessive pagination fail closed. API safety failures remain
fatal even on optional endpoints. Failed scans attempt a short privacy-safe
Telegram warning and return nonzero even if it arrives. Telegram delivery failures
also return nonzero; partially confirmed multi-message delivery is reported
honestly. Bounded GET retries and the scan runtime budget apply to all lookups.

## Planner supplement and why To-Do is not used

The [Planner API](https://developerdocs.instructure.com/services/canvas/resources/planner)
supports pagination, `context_codes[]`, `start_date`, and `end_date`. The
[upstream implementation](https://github.com/instructure/canvas-lms/blob/1c9f0bb8013ed69c4f2efe11fd483025469b7e6c/app/controllers/planner_controller.rb)
includes an ungraded quiz collection in its default result. The bot uses the
current-user endpoint, restricted to the course whose quiz listing failed, with
a one-day margin around the reminder window to avoid boundary differences.

Only valid `plannable_type: quiz` identifiers belonging to that course are used.
The bot fetches each quiz's actual record, resolves effective student dates where
needed, then applies the exact existing seven-day window and 48-hour urgency
threshold. It deduplicates against assignment/quiz IDs already checked. Linked
quizzes continue using effective assignment dates and assignment submission data.
Planner URLs, titles, and nested activity data are not used as authoritative input.

No completion filter is requested: `planner_override.marked_complete` is a
user-controlled checkbox, not a confirmed submission. Planner summary grades,
submission booleans, and `plannable_date` are not used to classify completion or
invent a due date. Actual current-user quiz submission evidence remains required
for positive completion. An unavailable submission lookup retains the deadline.

Planner supplements **known** quizzes; it does not replace the required assignment
scan or establish that no other standalone quizzes exist. The Classic Quiz
coverage warning remains even when recovery works or Planner returns no quizzes.
This needs live verification against NUS's deployed version and permissions.

The [To-Do API](https://developerdocs.instructure.com/services/canvas/resources/users#method.users.todo_items)
can include ungraded quizzes, but its documented item limit and dismissal/ignore
preferences prevent reliable absence checks. The bot does not use To-Do as another
fallback or treat dismissed work as completed.

## Current-user Classic Quiz submissions

The documented endpoint is:

```text
GET /api/v1/courses/:course_id/quizzes/:quiz_id/submission
```

The [official response schema](https://developerdocs.instructure.com/services/canvas/resources/quiz_submissions)
contains `quiz_submissions`, an array. The
[upstream controller](https://github.com/instructure/canvas-lms/blob/1c9f0bb8013ed69c4f2efe11fd483025469b7e6c/app/controllers/quizzes/quiz_submissions_api_controller.rb)
returns the current user's record, or a successful empty array when none exists.
An in-progress record uses `untaken`; finished records can be `complete` or
`pending_review`. If more than one record is supplied, only a uniquely highest,
valid attempt is considered; ambiguous attempts remain unknown.

A 404 is **not** evidence of a never-attempted quiz. It remains unavailable
information, as do restricted or malformed responses. The scan does not fall back
to course-wide submissions, which could expose other students' submissions to
accounts with grading privileges. Grades, historical attempts, and Planner
checkboxes alone never establish completion. Existing current-attempt submission
filtering, excused handling, and resubmission behavior are unchanged.

## Student-specific Classic Quiz dates

The [quiz override API](https://developerdocs.instructure.com/services/canvas/resources/quiz_assignment_overrides)
provides effective dates for the current API user:

```text
GET /api/v1/courses/:course_id/quizzes/assignment_overrides
```

Requests select the relevant quiz using
`quiz_assignment_overrides[][quiz_ids][]`. Responses wrap a paginated array in
`quiz_assignment_overrides`. For students, a matching record's `due_dates` contains
one effective date set. Integer and documented string quiz IDs are accepted;
duplicate records, multiple date sets, missing fields, and malformed timestamps
fail required date resolution. An explicit null `due_at` means no deadline and
never falls back to another date.

The [upstream quiz serializer](https://github.com/instructure/canvas-lms/blob/1c9f0bb8013ed69c4f2efe11fd483025469b7e6c/app/serializers/quizzes/quiz_serializer.rb)
applies student-specific dates. `all_dates` can contain only the base date: a sole
`base: true` entry matching the listed date requires no extra lookup. Other
nonempty/malformed override metadata or `only_visible_to_overrides` requires the
effective-date endpoint. Unresolvable known dates fail the run rather than
substitute a potentially incorrect generic date. Linked quizzes use effective
assignment records. New Quizzes remain covered where Canvas exposes their
assignment, dates, and submission information; external activities are not
universally discoverable.

## Verify in your own private deployment

1. Update your private copy; existing secrets and local onboarding remain compatible.
2. Run a manual reminder. Compare dates and completion filtering with Canvas,
   including assignment-linked quizzes and standalone quizzes with individual dates.
3. If **Course quiz listing HTTP 404** persists, review only the safe navigation and
   Planner operation diagnostics. Confirm whether Planner recovers a known
   standalone quiz. Do not infer quiz absence from hidden tabs or empty Planner data.
4. Confirm that accurate reminders with optional warnings show SUCCESS after
   delivery, and required retrieval/date or delivery failures show FAILURE.
   A “submission unverified” label intentionally retains a known deadline.
5. Share only fixed operation labels/statuses when reporting issues. Do not share
   Canvas URLs, response bodies, tokens, coursework, or student identifiers.

Mocked tests establish local behavior, not live NUS/Telegram compatibility. Manual
reruns may resend messages. Keep `v1.0.0` unpublished pending live verification.
