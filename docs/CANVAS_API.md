# Canvas API compatibility and live verification

The scan uses read-only NUS Canvas REST requests. Diagnostics name fixed operations,
never course/activity IDs, names, student identifiers, URLs, query parameters,
tokens, or response bodies. New operation labels identify where a failure occurs;
old generic logs cannot retrospectively identify an individual request.

## Operations and failure behavior

| Safe diagnostic label | Supported operation | Failure handling |
| --- | --- | --- |
| Course discovery | List active student courses. | Failure prevents reliable discovery and fails the scan. |
| Course assignment listing | List assignments with submission data and student date overrides applied. | Nonfatal course failures allow independent courses to continue. |
| Course quiz listing | List Classic Quizzes. | A 404 or permission error means unavailable coverage, not an empty list. |
| Quiz submission lookup | Retrieve the current user's Classic Quiz submission. | A 404, restricted endpoint, or unrecognized response leaves submission status unknown. A known deadline remains visible with a warning. |
| Linked assignment lookup | Retrieve a quiz's assignment when absent from the assignment listing. | No generic quiz-date fallback on failure. |
| Quiz override date lookup | Retrieve current-user effective Classic Quiz dates. | Required dates must resolve uniquely; no generic fallback on failure or ambiguity. |
| Other Canvas operation | Any unrecognized API operation. | Same safe request and error controls apply. |

HTTP 401, unsafe redirects, TLS verification failures, and unsafe/looping/excessive
pagination fail closed. Other endpoint failures preserve independent checked
results where safe. COMPLETE/PARTIAL/FAILED semantics and nonzero partial/failure
exits remain intact, even when Telegram delivery succeeds.

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
`pending_review`. These existing classifications are preserved. If more than one
record is supplied, only a uniquely highest, valid attempt is considered;
ambiguous attempts remain unknown.

A 404 is **not** documented evidence of a never-attempted quiz. It remains
unavailable information, as do restricted or malformed responses. The scan does
not fall back to a course-wide submission listing, which could expose other
students' submissions to accounts with grading privileges.

The earlier generic HTTP 404 warning could arise from assignment listing, quiz
listing, or linked-assignment retrieval. Quiz-submission 404s were previously
converted silently to `None`, followed by an unknown-submission warning. The
updated code records the quiz lookup's safe operation and HTTP status explicitly.
Neither an accurate digest nor a manual confirmation of one unfinished quiz
establishes that every unknown response is incomplete.

## Student-specific Classic Quiz dates

The [quiz override API](https://developerdocs.instructure.com/services/canvas/resources/quiz_assignment_overrides)
provides effective dates for the current API user:

```text
GET /api/v1/courses/:course_id/quizzes/assignment_overrides
```

Requests select the relevant quiz with the documented
`quiz_assignment_overrides[][quiz_ids][]` parameter. Responses wrap a paginated
array in `quiz_assignment_overrides`. For students, a matching record's
`due_dates` contains one effective date set. Both integer and documented string
quiz IDs are accepted; duplicate records, multiple date sets, missing fields,
and malformed timestamps cause uncertainty. An explicit null `due_at` means
there is no deadline, and never falls back to another date.

The [upstream quiz serializer](https://github.com/instructure/canvas-lms/blob/1c9f0bb8013ed69c4f2efe11fd483025469b7e6c/app/serializers/quizzes/quiz_serializer.rb)
already applies student-specific dates to the quiz listing. The existence of
`all_dates` alone does not establish an unresolved override: it can contain only
the base date. A sole `base: true` entry matching the listed date applies to all
students and needs no extra request. Other nonempty or malformed override metadata,
or `only_visible_to_overrides`, requires the effective-date lookup. Failures retain
a partial warning and omit that uncertain deadline rather than substitute a
potentially incorrect generic date. Linked quizzes continue using student-specific
assignment records, with existing deduplication and seven-day filtering.

These contracts are supported by official documentation and public upstream code,
not by access to NUS accounts. NUS deployment versions, endpoint availability,
and account permissions still need live verification.

## Verify in your own private deployment

1. Use the updated code in your private copy; existing secrets remain compatible.
2. Run a manual daily reminder and compare deadline dates and completed/incomplete
   items with Canvas, including standalone quizzes with individual date overrides.
3. If PARTIAL remains, check the fixed operation labels and HTTP statuses. A quiz
   marked “submission unverified” is intentionally retained when its deadline is
   known but its submission cannot be confirmed.
4. Confirm that a complete scan succeeds, a genuinely partial scan still exits
   nonzero, and Telegram delivery remains useful. Reruns may resend messages.
5. Share only safe operation labels/statuses when reporting failures. Do not share
   private Canvas URLs, response bodies, tokens, coursework, or student identifiers.

Do not publish `v1.0.0` until the maintainer completes this live verification.
