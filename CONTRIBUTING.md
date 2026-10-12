# Contributing

Use Python 3.12 or newer and a virtual environment. Install the hash-pinned
`requirements-dev.txt`, then run README's test, static analysis, syntax, and audit
commands. Tests must remain deterministic, use fictional data, and mock all
Canvas, Telegram, and GitHub CLI operations. Never attach real university data.

Keep API access read-only and limited to the NUS Canvas HTTPS origin. Changes to
submission classification, scan state, retries, pairing, or delivery need regression
tests. Do not copy code from sources without a compatible license.

## Dependency updates

The only direct runtime dependency is Requests. `requirements.in` lists it;
`requirements.txt` locks all runtime transitives and hashes. Development tools are
listed in `requirements-dev.in` and locked separately. With Python 3.12, regenerate:

```bash
pip-compile --generate-hashes --strip-extras requirements.in --output-file requirements.txt
pip-compile --generate-hashes --strip-extras --allow-unsafe requirements-dev.in --output-file requirements-dev.txt
```

Review licenses, advisories, version changes, and resulting hashes before merging
Dependabot updates. Runtime packages use Apache-2.0 (Requests), MPL-2.0 (certifi),
MIT (charset-normalizer, urllib3), and BSD-3-Clause (idna); retain their notices.
Development tools do not run in the daily production job. Ruff uses MIT,
pip-audit Apache-2.0, and pip-tools BSD-3-Clause. Standard package wheels and
verified upstream binaries are used; no remote setup scripts are executed.

Open a reviewed PR with passing CI. The public template should hold no personal
production secrets. By contributing, you agree your contribution is licensed
under this repository's MIT license.
