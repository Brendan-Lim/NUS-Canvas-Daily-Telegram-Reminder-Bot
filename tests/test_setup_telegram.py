"""Pairing and provisioning are fully mocked; no live student account is used."""

import json
import os
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from tools import setup_telegram as s

CODE = "fictional-onetime-code"
START = 1234567000


def update(cid=123, **fields):
    message = {
        "text": f"/start {CODE}",
        "date": START + 1,
        "chat": {"id": cid, "type": "private"},
        "from": {"id": cid, "is_bot": False},
        **fields,
    }
    return {"update_id": cid, "message": message}


def api(status=200, result=None, ok=True):
    value = Mock(status_code=status)
    value.json.return_value = {"ok": ok, "result": result}
    return value


class PairingTests(unittest.TestCase):
    def verify(self, updates):
        return s.paired_chat(updates, CODE, started=START, expires=START + 300)

    def test_exact_private_pairing(self):
        self.assertEqual(self.verify([update()]), ("123", 123))

    def test_ignore_unpaired_users(self):
        for text in (
            "/start",
            "/start wrong",
            f"/start {CODE} extra",
            f"/start {CODE} ",
            f"/start@bot {CODE}",
        ):
            with self.subTest(text=text):
                self.assertIsNone(self.verify([update(text=text)])[0])
        self.assertEqual(self.verify([update(99, text="/start wrong"), update()])[0], "123")

    def test_no_group_channel_bot_or_foreign_sender(self):
        for changes in (
            {"chat": {"id": 123, "type": "group"}},
            {"chat": {"id": 123, "type": "channel"}},
            {"from": {"id": 123, "is_bot": True}},
            {"from": {"id": 999, "is_bot": False}},
            {"chat": {"id": True, "type": "private"}},
            {"chat": {"id": "123", "type": "private"}},
            {"forward_origin": {}},
            {"from": None},
        ):
            with self.subTest(changes=changes):
                self.assertIsNone(self.verify([update(**changes)])[0])

    def test_edited_messages_not_accepted(self):
        self.assertIsNone(
            self.verify([{"update_id": 123, "edited_message": update()["message"]}])[0]
        )

    def test_stale_future_and_malformed_dates(self):
        for date in (START - 1, START + 301, None, "123", True):
            with self.subTest(date=date):
                self.assertIsNone(self.verify([update(date=date)])[0])

    def test_ambiguous_pairing_rejected(self):
        with self.assertRaisesRegex(s.SetupError, "Ambiguous"):
            self.verify([update(123), update(456)])
        self.assertEqual(self.verify([update(), update()])[0], "123")

    def test_unavailable_or_malformed_updates(self):
        for value in (None, {}, [None], [{"update_id": True}], [{"update_id": "1"}]):
            with self.subTest(value=value), self.assertRaises(s.SetupError):
                self.verify(value)

    def test_pair_offset_and_queued_ambiguity(self):
        telegram = Mock()
        telegram.call.side_effect = [[update(99, text="/start other")], [update()], []]
        self.assertEqual(
            s.pair(telegram, CODE, started=START, clock=lambda: 0, wall=lambda: START + 2), "123"
        )
        self.assertEqual(telegram.call.call_args_list[1].args[1]["offset"], 100)
        self.assertEqual(telegram.call.call_args_list[2].args[1]["offset"], 124)
        telegram.call.side_effect = [[update()], [update(456)]]
        with self.assertRaisesRegex(s.SetupError, "Ambiguous"):
            s.pair(telegram, CODE, started=START, clock=lambda: 0, wall=lambda: START + 2)

    def test_timeout_and_expired_after_poll(self):
        telegram = Mock()
        telegram.call.return_value = []
        with self.assertRaisesRegex(s.SetupError, "timed out"):
            s.pair(telegram, CODE, started=START, clock=lambda: 0, wall=lambda: START + 2)
        self.assertEqual(telegram.call.call_count, s.MAX_POLLS)
        telegram.reset_mock()
        with self.assertRaises(s.SetupError):
            s.pair(telegram, CODE, started=START, clock=lambda: 0, wall=lambda: START + 301)
        telegram.call.assert_not_called()
        telegram.call.return_value = [update()]
        with self.assertRaises(s.SetupError):
            s.pair(
                telegram,
                CODE,
                started=START,
                clock=Mock(side_effect=[0, 0, 301]),
                wall=lambda: START + 2,
            )


class SetupTransportTests(unittest.TestCase):
    def test_prepare_checks_bot_and_webhook(self):
        session = Mock()
        session.post.side_effect = [
            api(result={"is_bot": True, "username": "fictional_bot"}),
            api(result={"url": ""}),
        ]
        self.assertEqual(s.TelegramSetup("123:fictional", session).prepare(), "fictional_bot")
        self.assertFalse(session.post.call_args.kwargs["allow_redirects"])

    def test_webhook_conflict_does_not_delete_webhook(self):
        session = Mock()
        session.post.side_effect = [
            api(result={"is_bot": True, "username": "fictional_bot"}),
            api(result={"url": "https://example.invalid"}),
        ]
        with self.assertRaisesRegex(s.SetupError, "webhook"):
            s.TelegramSetup("123:fictional", session).prepare()
        self.assertEqual(session.post.call_count, 2)

    def test_errors_and_redirects_safe(self):
        for status in (301, 302, 400, 401, 404, 409, 429, 500):
            with self.subTest(status=status):
                session = Mock()
                session.post.return_value = api(status)
                with self.assertRaises(s.SetupError) as caught:
                    s.TelegramSetup("123:fictional", session).call("getUpdates")
                self.assertNotIn("123:fictional", str(caught.exception))
                self.assertEqual(session.post.call_count, 1)

    def test_network_invalid_json_and_payload(self):
        for value in (
            requests.Timeout("private token"),
            requests.ConnectionError("private token"),
            api(ok=False),
            api(result=[]),
        ):
            with self.subTest(value=type(value)):
                session = Mock()
                session.post.side_effect = [value]
                tool = s.TelegramSetup("123:fictional", session)
                if isinstance(value, requests.RequestException):
                    with self.assertRaises(s.SetupError):
                        tool.call("getUpdates")
                elif value.json.return_value["ok"] is False:
                    with self.assertRaises(s.SetupError):
                        tool.call("getUpdates")
        session = Mock()
        session.post.return_value = api()
        session.post.return_value.json.side_effect = ValueError("private body")
        with self.assertRaises(s.SetupError):
            s.TelegramSetup("123:fictional", session).call("getMe")

    def test_bad_token_and_unknown_method_never_network(self):
        for token in ("", "x", "123:abc/evil", "123:abc\n", "https://evil.invalid"):
            session = Mock()
            with self.subTest(token=token), self.assertRaises(s.SetupError):
                s.TelegramSetup(token, session)
            session.post.assert_not_called()
        with self.assertRaises(s.SetupError):
            s.TelegramSetup("123:fictional", Mock()).call("deleteWebhook")


class ProvisioningTests(unittest.TestCase):
    @patch("tools.setup_telegram.subprocess.run")
    def test_secret_stdin_only_and_no_environment_token(self, run):
        run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
        with patch.dict(
            os.environ,
            {"GH_TOKEN": "fictional", "GITHUB_TOKEN": "fictional", "GH_DEBUG": "api"},
            clear=True,
        ):
            s.provision("fictional/private", "fictional-canvas", "123:fictional-bot", "123")
        self.assertEqual(run.call_count, 3)
        for call, secret in zip(
            run.call_args_list, ("fictional-canvas", "123:fictional-bot", "123")
        ):
            self.assertEqual(call.kwargs["input"], secret)
            self.assertNotIn(secret, call.args[0])
            self.assertNotIn("shell", call.kwargs)
            self.assertEqual(call.kwargs["env"], {})
            self.assertEqual(call.args[0][-4:], ["--repo", "fictional/private", "--app", "actions"])

    @patch("tools.setup_telegram.gh_run")
    def test_private_admin_repo_required(self, gh):
        for private, permission in ((True, "ADMIN"), (False, "ADMIN"), (True, "WRITE")):
            with self.subTest(private=private, permission=permission):
                gh.side_effect = [
                    "",
                    json.dumps(
                        {
                            "isPrivate": private,
                            "viewerPermission": permission,
                            "nameWithOwner": "fictional/private",
                        }
                    ),
                ]
                if private and permission == "ADMIN":
                    self.assertEqual(s.verify_repository("fictional/private"), "fictional/private")
                else:
                    with self.assertRaises(s.SetupError):
                        s.verify_repository("fictional/private")

    @patch("tools.setup_telegram.gh_run")
    def test_bad_repo_and_canonical_mismatch(self, gh):
        with self.assertRaises(s.SetupError):
            s.verify_repository("--bad")
        gh.assert_not_called()
        gh.side_effect = [
            "",
            json.dumps(
                {"isPrivate": True, "viewerPermission": "ADMIN", "nameWithOwner": "other/private"}
            ),
        ]
        with self.assertRaises(s.SetupError):
            s.verify_repository("fictional/private")

    @patch("tools.setup_telegram.subprocess.run")
    def test_cli_failures_suppress_output(self, run):
        for failure in (
            SimpleNamespace(returncode=1, stdout="secret-value", stderr="secret-value"),
            FileNotFoundError(),
            subprocess.TimeoutExpired("gh", 60),
        ):
            with self.subTest(failure=type(failure)):
                run.side_effect = [failure]
                with self.assertRaises(s.SetupError) as caught:
                    s.gh_run(["secret", "set", "CANVAS_TOKEN"], secret="secret-value")
                self.assertNotIn("secret-value", str(caught.exception))

    @patch("tools.setup_telegram.subprocess.run")
    def test_actions_default_token_not_used(self, run):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), self.assertRaises(s.SetupError):
            s.gh_run(["auth", "status"])
        run.assert_not_called()

    @patch("tools.setup_telegram.gh_run")
    def test_failed_provisioning_stops(self, gh):
        gh.side_effect = ["", s.SetupError("failure")]
        with self.assertRaises(s.SetupError):
            s.provision("fictional/private", "fictional", "fictional", "123")
        self.assertEqual(gh.call_count, 2)

    @patch("tools.setup_telegram.getpass.getpass")
    def test_hidden_prompt_refuses_echo_fallback(self, getpass):
        getpass.side_effect = s.getpass.GetPassWarning("echo fallback")
        with self.assertRaises(s.SetupError):
            s.hidden("Token: ")

    def test_full_wizard_no_secret_output(self):
        telegram = Mock()
        telegram.prepare.return_value = "fictional_bot"
        with (
            patch("sys.argv", ["setup", "--repo", "fictional/private"]),
            patch("tools.setup_telegram.verify_repository", return_value="fictional/private"),
            patch(
                "tools.setup_telegram.hidden",
                side_effect=["canvas-private-value", "123:bot-private-value"],
            ),
            patch("tools.setup_telegram.TelegramSetup", return_value=telegram),
            patch("tools.setup_telegram.secrets.token_urlsafe", return_value=CODE) as random,
            patch("tools.setup_telegram.pair", return_value="123"),
            patch("tools.setup_telegram.provision") as provision,
            patch("builtins.print") as output,
        ):
            self.assertEqual(s.main(), 0)
            random.assert_called_once_with(32)
            provision.assert_called_once_with(
                "fictional/private", "canvas-private-value", "123:bot-private-value", "123"
            )
            printed = str(output.call_args_list)
            self.assertNotIn("canvas-private-value", printed)
            self.assertNotIn("bot-private-value", printed)
            self.assertIn(CODE, printed)
