# NUS Canvas → Telegram daily reminders

A personal, self-hosted deadline digest for NUS students. GitHub Actions runs it at **08:00 Singapore time daily**, with incomplete work due in the next **seven days**, sorted by deadline. Items due within **48 hours** are highlighted.

Each student deploys a private copy with their own credentials and Telegram bot. There is **no always-on server, database, central user registry, or paid hosting dependency**. Automatic Telegram pairing discovers your private chat without a manual Chat ID lookup.

This is an unofficial project. Canvas remains the authoritative source for deadlines and submission status.

## Quick Start

You need a GitHub account, Telegram, a computer with **Python 3.12 or newer**, and [GitHub CLI (`gh`)](https://cli.github.com/). Setup runs locally once; your computer can then be switched off.

### 1. Create your private copy

At this repository, choose **Use this template → Create a new repository**. Select **Private**, choose a name, and create it. Use a template rather than a public fork. Keep your credentials out of the public source repository.

### 2. Create your Telegram bot

Message [@BotFather](https://t.me/BotFather), send `/newbot`, and follow the prompts. Keep its **bot token** for the hidden setup prompt. Use a new dedicated bot, without a webhook or another polling application.

### 3. Generate your Canvas token

Sign in directly to [NUS Canvas settings](https://canvas.nus.edu.sg/profile/settings). Choose **Account → Settings → Approved Integrations → + New Access Token**, if available for your account. Set a purpose such as `Personal deadline reminders` and a short expiry. Copy the token when shown.

This token can access your account. Never send it to maintainers, put it in Telegram, or paste it into an issue, source file, or workflow.

### 4. Run guided setup

Install Python from [python.org](https://www.python.org/downloads/) and GitHub CLI from [cli.github.com](https://cli.github.com/) if needed. Open Terminal on macOS/Linux or PowerShell on Windows. Sign into GitHub CLI using its browser flow:

```text
gh auth login --hostname github.com --web --git-protocol https
```

Select your own GitHub account. The wizard uses this authenticated local CLI session with repository administration permission; you do not need to create a separate personal access token. A private-repository setup needs permission to manage that repository's Actions secrets. It cannot use the default workflow `GITHUB_TOKEN`.

Replace `YOUR-NAME/YOUR-REPO` below with **your private copy**, then clone it:

```text
gh repo clone YOUR-NAME/YOUR-REPO
cd YOUR-REPO
```

On **macOS and Linux**:

```bash
python3 --version # must be 3.12 or newer
python3 -m venv .venv
.venv/bin/python -m pip install --only-binary=:all: --require-hashes -r requirements.txt
.venv/bin/python tools/setup_telegram.py --repo YOUR-NAME/YOUR-REPO
```

On **Windows PowerShell**:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: --require-hashes -r requirements.txt
.\.venv\Scripts\python.exe tools/setup_telegram.py --repo YOUR-NAME/YOUR-REPO
```

The wizard verifies that the destination repository is private and you have administration permission. Paste your Canvas token and bot token into the **hidden terminal prompts**. It displays your bot link and a random, one-time command:

```text
/start <pairing-code-shown-by-the-wizard>
```

Open **your bot's private chat** in Telegram and send that exact command while setup waits. The code expires after five minutes. Keep the code private; it authorizes the reminder destination. Do not send either API token in Telegram.

The wizard accepts only the correctly paired private user, discovers the Chat ID, and saves all three Actions secrets through GitHub CLI:

| Secret | Stored value |
| --- | --- |
| `CANVAS_TOKEN` | Your Canvas API token |
| `TELEGRAM_BOT_TOKEN` | Your BotFather bot token |
| `TELEGRAM_CHAT_ID` | Automatically verified private chat destination |

Tokens are kept in process memory and passed to `gh` through stdin, not command arguments or tracked files. Setup briefly polls Telegram and then exits. It does not depend on old retained messages. If two users present the code in the received updates, setup rejects the pairing. Anyone who learns the code before pairing finishes could claim it; start again if exposed. Secret updates are not atomic: if provisioning fails partway, rerun setup to replace all three secrets consistently.

**Already configured?** Your existing three secrets remain compatible. You do not need to pair again unless changing your bot or destination. The old `tools/get_chat_id.py` helper now points to secure setup.

### 5. Test the reminder

Open **Actions → Daily NUS deadline reminder → Run workflow** in your private repository. Enable Actions if prompted. Check the run result and your Telegram chat.

A complete scan sends a digest even when nothing is due. A partial scan sends a warning and available results, and the workflow fails so you can investigate. A failed scan attempts a short warning telling you to check Canvas, and the workflow still fails. Telegram delivery failure also fails the workflow.

## Supported behavior and limitations

- Scans active student courses, Canvas assignments, and available Classic Quizzes. New Quizzes are handled through their Canvas assignment records and labelled as quizzes when Canvas identifies them. This does not detect every Canvas activity type.
- Requests assignment submission data and applies Canvas's student-specific assignment date overrides. Linked Classic Quizzes use those assignment records to avoid duplicate reminders and generic-date fallback.
- Current `unsubmitted` states and instructor reassignment (`redo_request`) remain incomplete even with previous attempts, grades, or timestamps. Submitted and pending-review states are confirmed submissions. Graded submissions require actual submission evidence; a grade or attempt count alone is insufficient. Excused work is excluded separately.
- Missing or malformed submission information is **unknown**, not confirmed incomplete. An available deadline is retained with “submission unverified.” A null due date has no deadline; missing or invalid dates are never invented.
- Standalone Classic Quizzes use the current-user quiz submission endpoint. Their supplied dates may not resolve individual overrides. Reported override information triggers a partial warning; verify these quizzes in Canvas.
- Assignments representing paper, external-tool, discussion, or other work can appear if Canvas exposes a due date. Completion may not be available. Modules, announcements, peer-review tasks, calendar-only events, and third-party activities are not comprehensively covered.
- Unpublished records are excluded. Locked but published work can still have a valid upcoming deadline.

| Scan state | Meaning within supported API scope |
| --- | --- |
| COMPLETE | Required supported information was checked successfully; an empty digest is allowed. This does not guarantee coverage of activities outside that scope. |
| PARTIAL | Some endpoints, dates, or submission states could not be verified; independently checked results can be sent, with a warning. An empty partial result is **not an all clear**. |
| FAILED | No useful information could be reliably checked, authentication failed, or unsafe API behavior was detected. No deadline digest is sent; a safe failure warning is attempted. |

Authorization errors are not treated as empty courses. Course-local failures allow independent courses to continue. Invalid credentials, redirects, unsafe pagination, loops, and excessive pagination fail closed. GET retries are limited to three attempts for network failures, 429, and 500/502/503/504, with bounded backoff and reasonable `Retry-After` support. Requests retain timeouts; the Canvas scan has a five-minute request budget and the Actions job a ten-minute timeout.

Messages are escaped for Telegram HTML and split within a conservative 4096-character bound. Sends are not automatically retried because a timeout can happen after delivery. If a multi-message digest fails midway, earlier messages may already have arrived. **Manual reruns may resend messages; GitHub Actions and Telegram do not provide exactly-once delivery here.** There is no deduplication database.

## Schedule, customization, and cost

The workflow retains:

```yaml
- cron: '0 0 * * *' # 00:00 UTC = 08:00 SGT
```

GitHub schedules may be delayed or skipped, especially near the start of an hour. A non-zero minute is an optional reliability trade-off you can choose in your copy; this project keeps 08:00. Scheduled workflows run from the default branch. Public forks need scheduling enabled, and inactive public repositories can have schedules disabled after 60 days. See [GitHub schedule documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

Optional repository **Actions variables**:

| Variable | Default | Allowed range |
| --- | --- | --- |
| `REMINDER_DAYS` | `7` | 1–30 days |
| `URGENT_HOURS` | `48` | 1–168 hours |

No paid server is required. Private repositories consume your plan's included GitHub-hosted Actions minutes; usage beyond the allowance may incur charges or stop runs depending on billing settings. CI, dependency updates, and manual reruns also consume minutes. Check [Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions) and your account's usage and budgets. This is not a promise of unlimited free execution.

## Privacy, rotation, and troubleshooting

Telegram receives course names, activity titles, deadlines, and Canvas links. **Bot chats are not end-to-end encrypted.** GitHub processes credentials on its runners; collaborators who can change your workflow could extract secrets. Use a private deployment, review changes, and set short Canvas-token expiries. Read [SECURITY.md](SECURITY.md).

| Symptom | What to do |
| --- | --- |
| Pairing expires or no updates arrive | Rerun setup and send its **new exact command** to your bot while it waits. Stop other polling tools. Old messages are not a setup mechanism. |
| Webhook or HTTP 409 conflict | Use a new dedicated bot, or remove your own webhook/other polling tool. Setup never deletes webhooks automatically. |
| GitHub setup fails | Run `gh auth login`, confirm the private repository name and your admin permission. Rerun after a partial secret update. |
| Canvas HTTP 401 | Generate a new personal token and replace `CANVAS_TOKEN`; revoke the old token in Canvas. |
| Canvas HTTP 403/404 or partial warning | Check Canvas directly. Your institution/course may restrict supported endpoints; an unavailable endpoint is not an empty result. |
| Canvas HTTP 429 or server error | Retries are bounded; try a later manual run and check Canvas directly meanwhile. |
| Telegram HTTP 401/404 | Check or rotate the bot token with BotFather and rerun setup. |
| Telegram HTTP 400/403 or no messages | Ensure you have not blocked your bot; rerun pairing if the destination changed. Check the Actions failure diagnostics. |
| Scheduled run skipped | Missing configuration is skipped only when all secrets are absent; incomplete configuration fails. Check Actions, default branch, billing, and scheduling restrictions. |

To rotate only the Canvas token, replace `CANVAS_TOKEN` in **Settings → Secrets and variables → Actions**, then revoke the old token in Canvas settings. For an exposed bot token, revoke/regenerate it with BotFather, then rerun setup. To stop, disable **Daily NUS deadline reminder** in Actions and revoke unused tokens. Never post tokens, student data, or private messages in bug reports.

## Development

```bash
.venv/bin/python -m pip install --only-binary=:all: --require-hashes -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q reminder.py tools tests
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pip-audit --require-hashes --disable-pip -r requirements-dev.txt
.venv/bin/python tools/scan_secrets.py
```

On Windows use `.\.venv\Scripts\python.exe` and the corresponding executables instead. All tests use fictional data and mock network/CLI calls. They establish local behavior, **not live NUS Canvas or Telegram compatibility**. A student must verify their own account, endpoint permissions, pairing, secret provisioning, and delivery through a manual workflow run.

Runtime packages and transitives are pinned with hashes. Dependabot proposes Python and action updates. See [CONTRIBUTING.md](CONTRIBUTING.md) for lock regeneration and [docs/PUBLISHING.md](docs/PUBLISHING.md) for release checks.

### Acknowledgements

Inspired in part by [nus-assistant-bot](https://github.com/lavanyagarg112/nus-assistant-bot). This project is an independently developed, serverless implementation focused on secure, self-hosted daily deadline reminders for NUS students.

## License

Released under the [MIT License](LICENSE). Retain its copyright and license notice when redistributing.

## Disclaimer

This project is not developed, approved, or endorsed by NUS, Instructure, Canvas, Telegram, or GitHub. Use only your own credentials and follow applicable university policies. Delivery and deadline accuracy are not guaranteed.
