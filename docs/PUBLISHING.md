# Maintainer publishing checklist

This document is for the original publisher. **Do not publish the old personal repository or its full history.** Build a separate, clean public template.

## 1. Review this release

- Confirm this directory contains **no personal tokens, chats, grades, or private student information**.
- Review `LICENSE` and confirm you hold the rights to publish your implementation under MIT. The original `lavanyagarg112/nus-assistant-bot` has no license in the version reviewed; this release should contain only independently authored code.
- Keep your existing working, private scheduled bot separate.
- Run `python -m unittest discover -s tests -v`.
- Check `.env` is not tracked, **including in Git history**. This release should start with a new Git history.

## 2. Create a *new* public repository

Suggested GitHub name: `nus-canvas-telegram-reminder`.

From the **extracted public release folder**, use:

```bash
git init
git branch -M main
git add .
git commit -m "Release open-source NUS Canvas Telegram reminder template"
```

Then choose either:

**With GitHub CLI** (after `gh auth login`):

```bash
gh repo create Brendan-Lim/nus-canvas-telegram-reminder \
  --public --source=. --remote=origin --push \
  --description "Unofficial NUS Canvas deadline digest delivered to your own Telegram bot with GitHub Actions"
```

**With GitHub website:** create a new, **empty public** repo at https://github.com/new and then:

```bash
git remote add origin https://github.com/Brendan-Lim/nus-canvas-telegram-reminder.git
git push -u origin main
```

Do **not** clone or copy the `.git` folder from the personal repository.

## 3. Configure the public project

1. Open **Settings → General** and turn on **Template repository**. Future students should use **Use this template** to generate their own **private** copies.
2. Set repository description and topics: `nus`, `canvas-lms`, `telegram-bot`, `github-actions`, `student-tools`, `python`.
3. Open **Settings → Security** and enable **Private vulnerability reporting** if available.
4. Optionally enable Discussions. Keep Issues enabled for bugs.
5. Review workflows under **Actions**. The official public template should have **no personal production secrets**; scheduled jobs without secrets simply skip. CI tests run without credentials.
6. For a public launch, consider creating a release tagged `v1.0.0` with a concise changelog.
7. Open the README on GitHub and check the Mermaid diagram, badges, and links render.

## 4. Acceptance checks

- Public source repository contains an MIT license, README, tests, and workflow files.
- `Python tests` workflow passes without GitHub Actions secrets.
- Public template's scheduled reminder prints a skip notice and **does not access Canvas** without credentials.
- Each student is directed to create a new private repository with only their own secrets.
- No personal GitHub Actions tokens/Canvas tokens appear anywhere in public Git history.

## Limitations

This environment's GitHub connection could read earlier public code but not the latest private repository and was unable to create a new repository. Publishing requires an authenticated GitHub CLI or using the GitHub UI.
