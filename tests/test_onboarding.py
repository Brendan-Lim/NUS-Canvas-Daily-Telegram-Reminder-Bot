"""Version, dependency and repository onboarding checks with no live credentials."""

import builtins
import contextlib
import io
import json
import runpy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools import setup_telegram as s

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "setup_telegram.py"


class StartupTests(unittest.TestCase):
    def test_python_39_guard_runs_before_optional_imports(self):
        output = io.StringIO()
        original = builtins.__import__

        def imports(name, *args, **kwargs):
            if name == "requests":
                self.fail("Requests must not be imported under unsupported Python")
            return original(name, *args, **kwargs)

        with (
            patch.object(sys, "version_info", (3, 9, 6)),
            patch.object(sys, "platform", "darwin"),
            patch("builtins.__import__", side_effect=imports),
            contextlib.redirect_stdout(output),
            self.assertRaises(SystemExit) as caught,
        ):
            runpy.run_path(str(SCRIPT), run_name="startup_test")
        self.assertEqual(caught.exception.code, 1)
        for value in (
            "3.9.6",
            "3.12",
            "brew install python@3.12",
            "python3.12 -m venv --clear .venv",
            "--require-hashes",
        ):
            self.assertIn(value, output.getvalue())

    def test_missing_requests_has_actionable_instructions_without_traceback(self):
        original = builtins.__import__

        def imports(name, *args, **kwargs):
            if name == "requests":
                raise ModuleNotFoundError("fictional-private-error", name="requests")
            return original(name, *args, **kwargs)

        output = io.StringIO()
        with (
            patch("builtins.__import__", side_effect=imports),
            contextlib.redirect_stdout(output),
            self.assertRaises(SystemExit) as caught,
        ):
            runpy.run_path(str(SCRIPT), run_name="startup_test")
        self.assertEqual(caught.exception.code, 1)
        self.assertIn("missing or incomplete", output.getvalue())
        self.assertIn("--only-binary=:all: --require-hashes -r requirements.txt", output.getvalue())
        self.assertNotIn("fictional-private-error", output.getvalue())

    def test_windows_recovery_uses_the_python_launcher(self):
        with patch.object(sys, "platform", "win32"):
            instructions = s.setup_environment_help(recreate=True)
        self.assertIn("py -3.12 -m venv --clear .venv", instructions)
        self.assertIn(r".\.venv\Scripts\python.exe", instructions)
        self.assertNotIn("brew install", instructions)


class RepositoryDetectionTests(unittest.TestCase):
    def test_safe_https_and_ssh_remotes(self):
        for url in (
            "https://github.com/fictional/private.git",
            "https://github.com/fictional/private",
            "git@github.com:fictional/private.git",
            "ssh://git@github.com/fictional/private.git",
            "https://github.com:443/fictional/private.git",
        ):
            with self.subTest(url=url):
                self.assertEqual(s.repository_from_remote(url), "fictional/private")

    def test_unsafe_remote_rejected_without_echoing(self):
        for url in (
            "http://github.com/fictional/private",
            "https://token@github.com/fictional/private",
            "https://github.com.evil.invalid/fictional/private",
            "https://github.com:444/fictional/private",
            "ssh://someone@github.com/fictional/private",
            "https://github.com/fictional/private?token=fictional",
            "https://github.com/fictional/private#bad",
            "https://github.com/../private",
            "https://github.com/fictional/private/extra",
            "https://github.com:bad/fictional/private",
            "git@evil.invalid:fictional/private.git",
            "file:///fictional/private",
            "https://github.com/fictional/\nprivate",
        ):
            with self.subTest(url=url):
                self.assertIsNone(s.repository_from_remote(url))

    def resolve(self, urls, explicit=None):
        # Every Git operation and every authenticated CLI call is mocked.
        def git(args):
            if args == ["rev-parse", "--is-inside-work-tree"]:
                return "true"
            if args == ["remote"]:
                return "\n".join(urls)
            return "\n".join(urls[args[-1]])

        with (
            patch("tools.setup_telegram.git_run", side_effect=git),
            patch(
                "tools.setup_telegram.verify_repository", side_effect=lambda repo: repo
            ) as verify,
        ):
            result = s.resolve_repository(explicit)
            verify.assert_called_once()
            return result

    def test_automatic_repository_detected_from_checkout(self):
        self.assertEqual(
            self.resolve({"origin": ["https://github.com/fictional/private.git"]}),
            "fictional/private",
        )
        self.assertEqual(
            self.resolve(
                {
                    "origin": ["https://github.com/fictional/private"],
                    "mirror": ["git@github.com:fictional/private.git"],
                }
            ),
            "fictional/private",
        )

    def test_ambiguous_selection_requires_explicit_checkout_remote(self):
        urls = {
            "origin": ["https://github.com/fictional/private.git"],
            "upstream": ["https://github.com/fictional/template.git"],
        }
        with self.assertRaisesRegex(s.SetupError, "ambiguous"):
            self.resolve(urls)
        self.assertEqual(self.resolve(urls, "fictional/private"), "fictional/private")
        with self.assertRaisesRegex(s.SetupError, "must match"):
            self.resolve(urls, "unrelated/private")

    def test_missing_and_unsafe_remotes_are_not_guessed(self):
        for urls in (
            {},
            {"origin": ["https://fictional.invalid/private"]},
            {"origin": ["https://token@github.com/fictional/private"]},
        ):
            with self.subTest(urls=urls), self.assertRaises(s.SetupError) as caught:
                self.resolve(urls)
            self.assertNotIn("https://token@", str(caught.exception))

    def test_split_fetch_push_destinations_are_ambiguous(self):
        with (
            patch(
                "tools.setup_telegram.git_run",
                side_effect=[
                    "true",
                    "origin",
                    "https://github.com/fictional/private",
                    "https://github.com/other/private",
                ],
            ),
            self.assertRaisesRegex(s.SetupError, "ambiguous"),
        ):
            s.resolve_repository()

    def test_public_template_cannot_receive_secrets(self):
        with (
            patch("tools.setup_telegram.gh_run") as gh,
            self.assertRaisesRegex(s.SetupError, "public template"),
        ):
            s.verify_repository(s.PUBLIC_TEMPLATE)
        gh.assert_not_called()

    def test_private_canonical_destination_and_permissions_verified(self):
        for info in (
            {"nameWithOwner": "Fictional/Private", "isPrivate": True, "viewerPermission": "ADMIN"},
            {"nameWithOwner": "fictional/private", "isPrivate": False, "viewerPermission": "ADMIN"},
            {"nameWithOwner": "fictional/private", "isPrivate": True, "viewerPermission": "WRITE"},
            {"nameWithOwner": "wrong/private", "isPrivate": True, "viewerPermission": "ADMIN"},
        ):
            with (
                self.subTest(info=info),
                patch("tools.setup_telegram.gh_run", side_effect=["", json.dumps(info)]),
            ):
                if info["nameWithOwner"] == "Fictional/Private":
                    self.assertEqual(s.verify_repository("fictional/private"), "Fictional/Private")
                else:
                    with self.assertRaises(s.SetupError):
                        s.verify_repository("fictional/private")

    def test_missing_git_unauthenticated_and_noncheckout(self):
        with (
            patch("tools.setup_telegram.subprocess.run", side_effect=FileNotFoundError()),
            self.assertRaisesRegex(s.SetupError, "Git is unavailable"),
        ):
            s.git_run(["remote"])
        with (
            patch(
                "tools.setup_telegram.gh_run",
                side_effect=s.SetupError("GitHub CLI operation failed"),
            ),
            self.assertRaises(s.SetupError),
        ):
            s.verify_repository("fictional/private")
        with (
            patch("tools.setup_telegram.git_run", return_value="false"),
            self.assertRaises(s.SetupError),
        ):
            s.resolve_repository()

    def test_git_failure_output_is_suppressed(self):
        with (
            patch(
                "tools.setup_telegram.subprocess.run",
                return_value=SimpleNamespace(returncode=1, stderr="private-url-token", stdout=""),
            ),
            self.assertRaises(s.SetupError) as caught,
        ):
            s.git_run(["remote"])
        self.assertNotIn("private-url-token", str(caught.exception))


class WizardInteractionTests(unittest.TestCase):
    def test_destination_confirmation_defaults_to_cancel(self):
        for answer in ("", "no", "y", "YES OTHER REPO"):
            with (
                self.subTest(answer=answer),
                patch("builtins.input", return_value=answer),
                patch("builtins.print"),
            ):
                self.assertFalse(s.confirm_repository("fictional/private"))
        with patch("builtins.input", return_value=" yes "), patch("builtins.print"):
            self.assertTrue(s.confirm_repository("fictional/private"))

    def test_cancellation_happens_before_tokens_or_provisioning(self):
        with (
            patch("sys.argv", ["setup"]),
            patch(
                "tools.setup_telegram.resolve_repository", return_value="fictional/private"
            ) as resolve,
            patch("builtins.input", return_value="no"),
            patch("tools.setup_telegram.hidden") as hidden,
            patch("tools.setup_telegram.provision") as provision,
            patch("builtins.print"),
        ):
            self.assertEqual(s.main(), 1)
        resolve.assert_called_once_with(None)
        hidden.assert_not_called()
        provision.assert_not_called()

    def test_confirmation_before_tokens_and_clear_telegram_instructions(self):
        events = []
        telegram = Mock()
        telegram.prepare.return_value = "fictional_bot"

        def confirm(prompt):
            events.append("confirmed")
            return "yes"

        def hidden(prompt):
            self.assertEqual(events[0], "confirmed")
            events.append("token")
            return "123:fictional"

        output = io.StringIO()
        with (
            patch("sys.argv", ["setup"]),
            patch("tools.setup_telegram.resolve_repository", return_value="fictional/private"),
            patch("builtins.input", side_effect=confirm),
            patch("tools.setup_telegram.hidden", side_effect=hidden),
            patch("tools.setup_telegram.TelegramSetup", return_value=telegram),
            patch("tools.setup_telegram.secrets.token_urlsafe", return_value="fictional-code"),
            patch("tools.setup_telegram.pair", return_value="123"),
            patch("tools.setup_telegram.provision") as provision,
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(s.main(), 0)
        provision.assert_called_once_with(
            "fictional/private", "123:fictional", "123:fictional", "123"
        )
        for text in (
            "Destination: fictional/private",
            "https://t.me/fictional_bot",
            "INSIDE TELEGRAM, not in Terminal or PowerShell",
            "/start fictional-code",
            "terminal running",
            "five minutes",
            "Never paste either API token",
        ):
            self.assertIn(text, output.getvalue())
        self.assertNotIn("123:fictional", output.getvalue())
