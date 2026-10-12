# Security policy

This project helps students use their **own** NUS Canvas tokens in **their own** GitHub Actions environments. A Canvas API token is a powerful bearer credential.

## Report a vulnerability

Please use GitHub's **Report a vulnerability** / private vulnerability reporting feature on this repository if the maintainer has enabled it. Otherwise, contact the maintainer privately through their GitHub profile before publishing exploit details.

**Never submit actual Canvas tokens, Telegram bot tokens, chat IDs, grade data, or screenshots of those secrets in public issues, pull requests, discussions, or CI logs.** Replace them with fictional strings.

## Threat model

- Each student's personal repository is the credential boundary; this project intentionally has no shared server or shared user database.
- GitHub Actions processes secrets while a run executes. Anyone with sufficient write/admin access to a student's repository can potentially change its workflow to extract them; review collaborators and workflow changes.
- Telegram receives the course names, assignment titles, dates, and links in the reminders; those messages are not end-to-end encrypted bot chats.
- The Canvas API client only sends bearer credentials to the pinned HTTPS `canvas.nus.edu.sg` API origin; it rejects cross-origin pagination and redirects.
- Public source code should never include real `.env` files or credentials, including in commit history.

## If a token is exposed

1. Revoke the Canvas token in **NUS Canvas → Account → Settings → Approved Integrations** (not just the GitHub secret).
2. Replace `CANVAS_TOKEN` in GitHub Actions secrets after creating a new expiring token.
3. If the Telegram token was exposed, regenerate/revoke it via [@BotFather](https://t.me/BotFather) and replace `TELEGRAM_BOT_TOKEN`.
4. Review recent Actions runs and repository access for unauthorized changes.

## Supported versions

Security issues should be reproduced on the latest `main` branch. See the repository's release tags for versioned snapshots if published.
