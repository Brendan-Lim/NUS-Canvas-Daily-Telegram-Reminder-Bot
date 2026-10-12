# Contributing

Thanks for helping make student tools more reliable.

## Local development

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Open an issue for behavior changes. Submit small pull requests with tests for changes to deadline processing or API integration. All PR tests must pass **without GitHub Actions secrets**.

Do not attach real university data or tokens. Use fictional course codes, deadlines, bot tokens, and chat IDs in fixtures and bug reports.

The project's API client is intentionally limited to the NUS Canvas HTTPS origin and read-only requests. Please discuss any changes to authentication architecture before implementing them.

**License:** by contributing, you agree that your contributions are licensed under the repository's MIT license.
