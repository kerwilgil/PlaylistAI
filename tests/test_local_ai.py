import json
import os
import subprocess
import unittest
from unittest.mock import patch

import local_ai
import app


class LocalAIEnvironmentTests(unittest.TestCase):
    def test_subscription_environment_removes_api_credentials(self):
        with patch.dict(
            os.environ,
            {
                "ANTHROPIC_API_KEY": "secret-a",
                "OPENAI_API_KEY": "secret-b",
                "CODEX_API_KEY": "secret-c",
                "PLAYLISTAI_TEST_VALUE": "kept",
            },
            clear=True,
        ):
            environment = local_ai._subscription_env()

        self.assertNotIn("ANTHROPIC_API_KEY", environment)
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertNotIn("CODEX_API_KEY", environment)
        self.assertEqual(environment["PLAYLISTAI_TEST_VALUE"], "kept")
        self.assertEqual(environment["CI"], "1")
        self.assertIn("PATH", environment)

    @patch("local_ai._command_path", return_value="codex")
    @patch("local_ai._run_process")
    def test_codex_status_accepts_message_on_stderr(self, run_process, _command_path):
        run_process.return_value = subprocess.CompletedProcess(
            ["codex"], 0, stdout="", stderr="Logged in using ChatGPT\n"
        )

        status = local_ai.cli_status("codex")

        self.assertTrue(status["available"])
        self.assertNotIn("ChatGPT", status["detail"])

    @patch("local_ai._command_path", return_value="claude")
    @patch("local_ai._run_process")
    def test_claude_status_does_not_return_account_data(self, run_process, _command_path):
        run_process.return_value = subprocess.CompletedProcess(
            ["claude"], 0, stdout=json.dumps({"loggedIn": True, "email": "private@example.com"}), stderr=""
        )

        status = local_ai.cli_status("claude_code")

        self.assertTrue(status["available"])
        self.assertNotIn("email", status)
        self.assertNotIn("private", json.dumps(status))


class LocalAICallTests(unittest.TestCase):
    @patch("local_ai._command_path", return_value="claude")
    @patch("local_ai._run_process")
    def test_claude_runs_without_tools_or_session_persistence(self, run_process, _command_path):
        run_process.return_value = subprocess.CompletedProcess(
            ["claude"], 0, stdout=json.dumps({"result": '{"ok":true}'}), stderr=""
        )

        response = local_ai.call_local_ai("prompt", provider="claude_code", model="default")

        command = run_process.call_args.args[0]
        self.assertEqual(response, '{"ok":true}')
        self.assertIn("--no-session-persistence", command)
        self.assertIn("--safe-mode", command)
        self.assertIn("--tools", command)
        self.assertNotIn("--model", command)
        self.assertEqual(run_process.call_args.kwargs["input_text"], "prompt")

    @patch("local_ai._command_path", return_value="codex")
    @patch("local_ai._run_process")
    def test_codex_runs_ephemeral_read_only_in_temporary_directory(self, run_process, _command_path):
        captured = {}

        def complete(command, **kwargs):
            captured["command"] = command
            captured["cwd"] = kwargs["cwd"]
            output_path = command[command.index("--output-last-message") + 1]
            with open(output_path, "w", encoding="utf-8") as output_file:
                output_file.write('{"ok":true}')
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        run_process.side_effect = complete
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}

        response = local_ai.call_local_ai(
            "prompt", provider="codex", model="default", output_schema=schema
        )

        command = captured["command"]
        self.assertEqual(response, '{"ok":true}')
        self.assertIn("--ephemeral", command)
        self.assertIn("--ignore-rules", command)
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        self.assertIn("--output-schema", command)
        self.assertEqual(command[-1], "-")
        self.assertEqual(captured["cwd"], command[command.index("--cd") + 1])

    def test_rejects_unknown_subscription_provider(self):
        with self.assertRaises(local_ai.LocalAIError):
            local_ai.call_local_ai("prompt", provider="unknown")


class LocalAIProviderApiTests(unittest.TestCase):
    def test_settings_template_exposes_access_modes_and_version(self):
        client = app.app.test_client()
        with client.session_transaction() as flask_session:
            flask_session["token_info"] = {"access_token": "test"}

        response = client.get("/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Suscripción local", html)
        self.assertIn("PlaylistAI 1.1.0", html)

    @patch("app.cli_status")
    def test_provider_endpoint_groups_subscription_and_api_modes(self, cli_status):
        cli_status.return_value = {
            "installed": True,
            "authenticated": True,
            "available": True,
            "detail": "Sesión activa",
        }

        response = app.app.test_client().get("/api/providers")
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["claude_code"]["access"], "subscription")
        self.assertEqual(payload["codex"]["access"], "subscription")
        self.assertEqual(payload["anthropic"]["access"], "api")
        self.assertTrue(payload["claude_code"]["available"])
        self.assertNotIn("email", json.dumps(payload).lower())

    def test_configuration_rejects_a_model_from_another_provider(self):
        error = app.ai_config_error("claude_code", "gpt-5.6-sol")

        self.assertIn("Modelo de IA no soportado", error)


if __name__ == "__main__":
    unittest.main()
