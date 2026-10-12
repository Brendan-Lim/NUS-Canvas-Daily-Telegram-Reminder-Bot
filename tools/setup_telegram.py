"""One-time local pairing; tokens go to gh over stdin, never arguments or files."""

from __future__ import annotations

import sys


def setup_environment_help(*, recreate: bool = False) -> str:
    """Stdlib-only help, safe even before Requests or other imports are available."""
    windows = sys.platform == "win32"
    launcher = "py -3.12" if windows else "python3.12"
    python = r".\.venv\Scripts\python.exe" if windows else ".venv/bin/python"
    lines = []
    if sys.platform == "darwin":
        lines.append("On macOS with Homebrew: brew install python@3.12")
    lines.append("From your project folder, use Python 3.12 to create the project environment:")
    lines.append(f"{launcher} -m venv {'--clear ' if recreate else ''}.venv")
    if recreate:
        lines.append(
            "--clear replaces only the project .venv contents. Do not use a shared or symlinked environment."
        )
    lines.append(
        f"{python} -m pip install --only-binary=:all: --require-hashes -r requirements.txt"
    )
    lines.append(f"{python} tools/setup_telegram.py")
    return "\n".join(lines)


# Run before optional imports and any setup work, including argument parsing.
if sys.version_info[:2] < (3, 12):
    print(
        "Setup requires Python 3.12 or newer; detected Python "
        + ".".join(map(str, sys.version_info[:3]))
        + "."
    )
    print(setup_environment_help(recreate=True))
    raise SystemExit(1)

import argparse
import getpass
import json
import os
import re
import secrets
import subprocess
import time
import warnings
from urllib.parse import urlparse

try:
    import requests
except ImportError:
    print(
        "Setup dependencies are missing or incomplete. Install the hash-pinned project requirements, then retry."
    )
    print(setup_environment_help())
    raise SystemExit(1) from None

PAIRING_SECONDS = 300
MAX_POLLS = 40
PUBLIC_TEMPLATE = "Brendan-Lim/NUS-Canvas-Daily-Telegram-Reminder-Bot"


class SetupError(Exception):
    """Only fixed safe diagnostics may cross this boundary."""


def gh_run(args: list[str], *, secret: str | None = None) -> str:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        raise SetupError("Run setup locally using gh auth login, not in GitHub Actions.")
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "GH_TOKEN",
            "GITHUB_TOKEN",
            "GH_HOST",
            "GH_REPO",
            "GH_DEBUG",
            "GH_LOG_FILE",
        }
    }
    try:
        result = subprocess.run(
            ["gh", *args],
            input=secret,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        raise SetupError(
            "GitHub CLI unavailable or timed out. Install gh and run gh auth login."
        ) from None
    if result.returncode:
        raise SetupError(
            "GitHub CLI operation failed. Check login and repository administration permissions."
        )
    return result.stdout


def verify_repository(repo: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise SetupError("Use the repository name in OWNER/REPOSITORY format.")
    if repo.lower() == PUBLIC_TEMPLATE.lower():
        raise SetupError(
            "The public template cannot receive personal secrets. Clone your own private copy."
        )
    gh_run(["auth", "status", "--hostname", "github.com"])
    try:
        info = json.loads(
            gh_run(
                [
                    "repo",
                    "view",
                    repo,
                    "--json",
                    "nameWithOwner,isPrivate,viewerPermission",
                ]
            )
        )
        valid = (
            isinstance(info, dict)
            and info.get("isPrivate") is True
            and info.get("viewerPermission") == "ADMIN"
            and str(info.get("nameWithOwner", "")).lower() == repo.lower()
        )
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise SetupError(
            "Setup requires your own private repository and administration permission."
        )
    return info["nameWithOwner"]


def git_run(args: list[str]) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    try:
        result = subprocess.run(
            ["git", *args], text=True, capture_output=True, timeout=30, check=False, env=env
        )
    except (OSError, subprocess.SubprocessError):
        raise SetupError(
            "Git is unavailable or timed out. Install Git and run setup from your cloned project folder."
        ) from None
    if result.returncode:
        raise SetupError(
            "Could not inspect this Git checkout. Run setup from your cloned project folder."
        )
    return result.stdout.strip()


def repository_from_remote(url: str) -> str | None:
    """Accept GitHub HTTPS/SSH only; never echo a remote URL or its credentials."""
    if not isinstance(url, str) or any(c.isspace() for c in url):
        return None
    if url.startswith("git@github.com:"):
        path = url[len("git@github.com:") :]
    else:
        try:
            parsed = urlparse(url)
            if (
                parsed.hostname != "github.com"
                or parsed.query
                or parsed.fragment
                or parsed.password
            ):
                return None
            if parsed.scheme == "https" and parsed.username is None and parsed.port in (None, 443):
                path = parsed.path.removeprefix("/")
            elif parsed.scheme == "ssh" and parsed.username == "git" and parsed.port in (None, 22):
                path = parsed.path.removeprefix("/")
            else:
                return None
        except ValueError:
            return None
    path = path.removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", path):
        return None
    if any(part in (".", "..") for part in path.split("/")):
        return None
    return path


def resolve_repository(explicit: str | None = None) -> str:
    if git_run(["rev-parse", "--is-inside-work-tree"]) != "true":
        raise SetupError("Run setup inside your cloned project repository.")
    remotes = git_run(["remote"]).splitlines()
    candidates = {}
    unsafe = False
    for remote in remotes:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", remote):
            raise SetupError("A Git remote name could not be safely inspected.")
        for mode in ([], ["--push"]):
            urls = git_run(["remote", "get-url", "--all", *mode, remote]).splitlines()
            if not urls:
                unsafe = True
            for url in urls:
                repo = repository_from_remote(url)
                if repo is None:
                    unsafe = True
                else:
                    candidates[repo.lower()] = repo
    if explicit is not None:
        if explicit.lower() not in candidates:
            raise SetupError("--repo must match a safe GitHub remote in this checkout.")
        selected = candidates[explicit.lower()]
    elif unsafe or len(candidates) != 1:
        raise SetupError(
            "Repository detection is missing, unsafe or ambiguous. Check remotes, or select a matching private remote with --repo OWNER/REPOSITORY."
        )
    else:
        selected = next(iter(candidates.values()))
    return verify_repository(selected)


def confirm_repository(repo: str) -> bool:
    print(f"Destination: {repo} (verified private repository).")
    answer = input(
        "Replace this repository's three reminder Actions secrets? Type yes to continue: "
    )
    return answer.strip().lower() == "yes"


class TelegramSetup:
    def __init__(self, token: str, session=None):
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
            raise SetupError("Invalid Telegram bot token format.")
        self.token = token
        self.session = session or requests.Session()

    def call(self, method: str, data: dict | None = None):
        if method not in {"getMe", "getWebhookInfo", "getUpdates"}:
            raise SetupError("Unsupported setup operation.")
        try:
            response = self.session.post(
                f"https://api.telegram.org/bot{self.token}/{method}",
                json=data or {},
                timeout=(10, 25),
                allow_redirects=False,
            )
            if response.status_code == 409:
                raise SetupError(
                    "Telegram polling conflict. Stop other polling tools or use a new bot; no webhook was removed."
                )
            if response.status_code in (401, 404):
                raise SetupError("Telegram rejected the bot token. Check BotFather.")
            if response.status_code != 200:
                raise SetupError("Telegram setup request failed. Retry setup later.")
            payload = response.json()
        except (requests.RequestException, ValueError):
            raise SetupError(
                "Telegram unavailable or returned an invalid response. Retry setup later."
            ) from None
        if (
            not isinstance(payload, dict)
            or payload.get("ok") is not True
            or "result" not in payload
        ):
            raise SetupError("Telegram did not confirm the setup request.")
        return payload["result"]

    def prepare(self) -> str:
        bot = self.call("getMe")
        webhook = self.call("getWebhookInfo")
        if (
            not isinstance(bot, dict)
            or bot.get("is_bot") is not True
            or not re.fullmatch(r"[A-Za-z0-9_]+", str(bot.get("username", "")))
        ):
            raise SetupError("Telegram returned invalid bot information.")
        if not isinstance(webhook, dict) or not isinstance(webhook.get("url"), str):
            raise SetupError("Telegram webhook information unavailable.")
        if webhook["url"]:
            raise SetupError(
                "This bot has a webhook. Use a new dedicated bot or remove your own webhook first."
            )
        return bot["username"]


def paired_chat(
    updates: object, code: str, *, started: int, expires: int
) -> tuple[str | None, int | None]:
    if not isinstance(updates, list):
        raise SetupError("Telegram updates unavailable.")
    matches: set[str] = set()
    highest = None
    for update in updates:
        if (
            not isinstance(update, dict)
            or not isinstance(update.get("update_id"), int)
            or isinstance(update["update_id"], bool)
        ):
            raise SetupError("Telegram returned malformed updates.")
        highest = max(highest if highest is not None else update["update_id"], update["update_id"])
        message = update.get("message")
        if not isinstance(message, dict) or "forward_origin" in message:
            continue
        if message.get("text") != f"/start {code}":
            continue
        date = message.get("date")
        chat, sender = message.get("chat"), message.get("from")
        if not isinstance(date, int) or isinstance(date, bool) or not started <= date <= expires:
            continue
        if not isinstance(chat, dict) or not isinstance(sender, dict):
            continue
        cid = chat.get("id")
        if (
            chat.get("type") != "private"
            or not isinstance(cid, int)
            or isinstance(cid, bool)
            or cid <= 0
            or sender.get("id") != cid
            or sender.get("is_bot") is not False
        ):
            continue
        matches.add(str(cid))
    if len(matches) > 1:
        raise SetupError(
            "Ambiguous pairing: multiple private users supplied the code. Run setup again with a new code."
        )
    return next(iter(matches), None), highest


def pair(
    telegram: TelegramSetup, code: str, *, started: int, clock=time.monotonic, wall=time.time
) -> str:
    deadline = clock() + PAIRING_SECONDS
    offset = None
    for _ in range(MAX_POLLS):
        if clock() >= deadline or wall() > started + PAIRING_SECONDS:
            break
        data = {"timeout": 10, "limit": 100, "allowed_updates": ["message"]}
        if offset is not None:
            data["offset"] = offset
        updates = telegram.call("getUpdates", data)
        if clock() >= deadline or wall() > started + PAIRING_SECONDS:
            break
        cid, highest = paired_chat(
            updates, code, started=started, expires=started + PAIRING_SECONDS
        )
        if highest is not None:
            offset = highest + 1
        if cid:
            more = telegram.call("getUpdates", {**data, "offset": offset, "timeout": 0})
            other, _ = paired_chat(more, code, started=started, expires=started + PAIRING_SECONDS)
            if other and other != cid:
                raise SetupError("Ambiguous pairing. Run setup again with a new code.")
            if clock() >= deadline or wall() > started + PAIRING_SECONDS:
                break
            return cid
    raise SetupError(
        "Pairing expired or timed out. Run setup again and send the new code while it waits."
    )


def provision(repo: str, canvas_token: str, bot_token: str, chat_id: str):
    for name, value in (
        ("CANVAS_TOKEN", canvas_token),
        ("TELEGRAM_BOT_TOKEN", bot_token),
        ("TELEGRAM_CHAT_ID", chat_id),
    ):
        gh_run(["secret", "set", name, "--repo", repo, "--app", "actions"], secret=value)


def hidden(prompt: str) -> str:
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            value = getpass.getpass(prompt).strip()
        except getpass.GetPassWarning:
            raise SetupError(
                "Use an interactive terminal that supports hidden password input."
            ) from None
    if not value:
        raise SetupError("A token is required.")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo", help="Optional private OWNER/REPOSITORY matching a checkout remote"
    )
    args = parser.parse_args()
    try:
        repo = resolve_repository(args.repo)
        if not confirm_repository(repo):
            print("Setup cancelled; no tokens requested or secrets replaced.")
            return 1
        canvas_token = hidden("Canvas API token (hidden): ")
        bot_token = hidden("Telegram bot token (hidden): ")
        telegram = TelegramSetup(bot_token)
        username = telegram.prepare()
        code = secrets.token_urlsafe(32)
        started = int(time.time())
        print(f"1. Open your bot's PRIVATE CHAT inside Telegram: https://t.me/{username}")
        print("2. Send the exact message below INSIDE TELEGRAM, not in Terminal or PowerShell:")
        print(f"/start {code}")
        print(
            "3. Keep this terminal running while it verifies your chat. The code expires in five minutes."
        )
        print("Keep this one-time code private. Never paste either API token into Telegram.")
        cid = pair(telegram, code, started=started)
        provision(repo, canvas_token, bot_token, cid)
        print(
            "Private chat verified and all three Actions secrets saved. Run the daily reminder from GitHub Actions."
        )
        return 0
    except SetupError as error:
        print(f"Setup stopped: {error}")
        print(
            "Secret updates may be incomplete if provisioning failed; rerun setup to replace all three."
        )
        return 1
    except (KeyboardInterrupt, EOFError):
        print("Setup cancelled.")
        return 1
    except Exception:
        print("Setup failed unexpectedly; details suppressed to protect credentials.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
