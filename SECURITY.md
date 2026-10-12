# Security policy

This template uses each student's own Canvas token, private GitHub repository,
and dedicated Telegram bot. A Canvas API token is a powerful bearer credential.

## Report privately

Use **Security → Report a vulnerability** on the public repository. If private
reporting is unavailable, contact the maintainer privately through their GitHub
profile before publishing exploit details. Never attach real tokens, chat IDs,
coursework, grade data, or private screenshots to public reports. Use fictional data.

## Trust boundaries and controls

- There is no shared server, registry, or database. The maintainer does not collect
  credentials. GitHub processes secrets while workflows execute.
- Anyone who can alter your workflows may be able to extract Actions secrets.
  Keep your deployment private, review collaborators and updates, and use expiring tokens.
- Canvas bearer requests are GET-only, pinned to HTTPS `canvas.nus.edu.sg` on
  port 443 under `/api/v1/`. Redirects and unsafe pagination are refused before
  forwarding credentials. Retries, pagination, timeouts, and total scan runtime are bounded.
- Diagnostics never intentionally include tokens, response bodies, course names,
  assignment titles, or user identifiers. Operational Telegram warnings contain
  no coursework. Regular digests intentionally contain your deadline information.
- Telegram receives titles, course names, dates, and links. Bot chats are **not
  end-to-end encrypted**. Consider university data policies before enabling delivery.
- Local setup uses a cryptographically random, five-minute pairing code and accepts
  only an exact `/start CODE` message from the matching private-chat user. Edited,
  forwarded, group, channel, stale, and unrelated messages are rejected. Ambiguous
  claimants in received/queued updates cause failure. Protect the code: anyone who
  learns it before pairing finishes may claim the destination. Pairing verifies
  possession of the code and Telegram chat, not a legal or university identity.
- Use a fresh bot. Setup briefly consumes/acknowledges updates, never deletes a
  webhook, and cannot detect every competing client. It does not depend on old
  retained updates, and stops after success, timeout, or error.
- API tokens use hidden terminal input and remain in memory. GitHub CLI receives
  secrets on stdin, with no shell, credential arguments, or secret files. Setup
  requires a private repository and admin access. It rejects execution in Actions
  and removes environment token overrides so the workflow `GITHUB_TOKEN` is not used.
- Setup checks Python 3.12+ before optional imports, reports missing dependencies,
  and detects the destination from safe GitHub checkout remotes. Explicit `--repo`
  selection must match a checkout remote. Canonical name, private visibility,
  administration permission, and an interactive `yes` confirmation are required
  before requesting API tokens or replacing secrets. The public template is refused.
- Three GitHub secret writes are not atomic. Rerun setup after provisioning failure.
  Existing `TELEGRAM_CHAT_ID` secrets remain valid; legacy manual configurations
  are not retrospectively verified by the wizard.
- Dependencies and transitives are hash-pinned. CI audits known advisories, runs
  static security rules, and screens tracked files and available Git blobs.
  Screening cannot prove absence of every credential or student-data format.
- CI runs without production secrets; action references are immutable upstream
  SHAs. Do not add untrusted PR execution through `pull_request_target`.

## Operational limitations

Operational outcomes are SUCCESS or FAILURE. Success means required processing
and Telegram delivery completed, not exhaustive Canvas coverage. Inaccessible
standalone Classic Quiz listings are optional only after required assignment
retrieval succeeds. Coverage warnings remain visible even if Planner recovers
known quizzes. Unknown submissions retain their known deadlines; an empty digest
with coverage warnings cannot establish an all clear. Required retrieval or date
failures, authentication failures, and unsafe responses fail closed and return
nonzero even if their failure notification arrives. Delivery timeouts can leave
an uncertain or partial delivery; no exactly-once guarantee or automatic send
retry is provided. Check Canvas directly when warnings appear or reminders stop.

Mocked tests cannot establish live service compatibility, institution permissions,
or successful secret provisioning for your account. Test your own private copy
with a manual Actions run after setup and token rotation.

## If a token is exposed or you want to stop

1. Revoke the Canvas token in **Canvas → Account → Settings → Approved Integrations**.
2. Create a new expiring token and replace `CANVAS_TOKEN` in your private Actions secrets.
3. Revoke/regenerate an exposed Telegram bot token with **@BotFather** and rerun setup.
4. Review Actions runs, collaborators, and workflow edits. Removing a GitHub secret
   alone does not revoke a token at its provider.
5. Disable the daily workflow and revoke unused tokens when stopping reminders.

Security fixes target the latest `main` and any published supported release.
