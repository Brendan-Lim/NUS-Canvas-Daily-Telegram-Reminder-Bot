"""One-time local pairing; tokens go to gh over stdin, never arguments or files."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import secrets
import subprocess
import time
import warnings

import requests

PAIRING_SECONDS = 300
MAX_POLLS = 40


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
    parser.add_argument("--repo", required=True, help="Your private OWNER/REPOSITORY")
    args = parser.parse_args()
    try:
        repo = verify_repository(args.repo)
        print(f"Configuring Actions secrets for {repo}. Existing values will be replaced.")
        canvas_token = hidden("Canvas API token (hidden): ")
        bot_token = hidden("Telegram bot token (hidden): ")
        telegram = TelegramSetup(bot_token)
        username = telegram.prepare()
        code = secrets.token_urlsafe(32)
        started = int(time.time())
        print(
            f"Open https://t.me/{username} in Telegram and send the following in its private chat within five minutes:"
        )
        print(f"/start {code}")
        print("Keep this one-time code private. Do not send either API token in Telegram.")
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
