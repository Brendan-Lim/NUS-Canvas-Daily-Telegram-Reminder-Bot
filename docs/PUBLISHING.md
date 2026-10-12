# Public repository maintenance and releases

The public repository already exists at
`Brendan-Lim/NUS-Canvas-Daily-Telegram-Reminder-Bot`; its default branch is `main`.
Do not initialize a replacement repository, import private history, or access the
maintainer's separate personal bot repository.

## Before merging or releasing

1. Confirm the Git remote is the public repository above. Preserve unrelated work.
2. Run unit tests, Ruff, compile checks, dependency audit, workflow validation,
   and secret screening described in README. Inspect the entire diff. Use only
   fictional test data. History screening is heuristic; it cannot prove absence.
3. Validate action pins against official upstream release commits. Workflow
   validation uses actionlint 1.7.12 with an upstream SHA-256-verified binary;
   no downloaded setup scripts are executed.
4. Keep `0 0 * * *` (08:00 SGT), existing secret names, and Python 3.12 compatibility.
5. Push normally; never force-push or rewrite public history. When protected,
   use a feature branch and reviewed PR. Wait for the **tests** CI check.
6. Publish no tag or release until the pushed commit passes CI. For the first
   release use `v1.0.0`; describe scan states, automatic pairing, limitations,
   and the absence of live student-account verification.

The public template must contain no personal production secrets. Fully
unconfigured scheduled reminders skip without contacting Canvas or Telegram;
manual runs and partially configured deployments fail clearly. CI has no
production secrets and does not execute `pull_request_target` contributor code.

## Repository settings

- **Settings → General → Template repository:** enabled.
- Description: personal, serverless NUS Canvas deadline reminders through Telegram
  with secure automatic setup and GitHub Actions.
- Topics: `nus`, `canvas-lms`, `telegram-bot`, `github-actions`, `student-tools`, `python`.
- **Settings → Security → Advanced Security:** enable private vulnerability
  reporting, dependency graph, Dependabot alerts and security updates where available.
- **Settings → Actions → General:** read-only default workflow permissions;
  disable approval of PRs by workflows. Require full action SHAs where supported.
- **Settings → Branches → Add branch protection rule:** target `main`, require a PR
  and one approving review, dismiss stale approvals, require the `tests` status
  check and up-to-date branches, block force pushes and deletion. Keep administrator
  enforcement off so the sole owner can administer the project; normal contributions
  still use reviews and CI. Do not use an administrator bypass to merge failing code.

Configure protection after an initial verified push so it does not obstruct the
requested hardening delivery. GitHub plan or API restrictions may require the
owner to apply settings manually. Record only settings confirmed by a follow-up
API read; a failed API call is not a completed change.
