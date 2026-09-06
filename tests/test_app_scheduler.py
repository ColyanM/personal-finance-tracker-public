import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import app_scheduler


class AppSchedulerTests(unittest.TestCase):
    def test_runner_uses_an_exact_interpreter_path_without_credentials_or_system(self):
        python_path = Path("C:/Python Current/python.exe")

        runner_text = app_scheduler.build_runner_text(python_path=python_path)

        self.assertIn(f'"{python_path}"', runner_text)
        self.assertIn(f'"{app_scheduler.APP_SCRIPT}"', runner_text)
        self.assertIn(f'"{app_scheduler.LOG_FILE}"', runner_text)
        self.assertNotIn(" python ", runner_text.casefold())

        sensitive_words = (
            "system",
            "password",
            "secret",
            "token",
            "plaid",
            "akahu",
        )
        for sensitive_word in sensitive_words:
            with self.subTest(sensitive_word=sensitive_word):
                self.assertNotIn(sensitive_word, runner_text.casefold())

    def test_python_path_falls_back_to_the_current_interpreter(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            project_root = Path(temporary_folder)
            current_python = Path("C:/Current Python/python.exe")

            with (
                patch("scripts.app_scheduler.PROJECT_ROOT", project_root),
                patch("scripts.app_scheduler.sys.executable", str(current_python)),
            ):
                selected_python = app_scheduler.get_python_path()

        self.assertEqual(selected_python, current_python)

    def test_task_runs_on_logon_with_limited_current_user_privileges(self):
        runner_path = Path("C:/Finance Hub/finance_hub_web_app.cmd")

        command = app_scheduler.build_task_command(runner_path)

        self.assertEqual(command[0], "schtasks")
        self.assertEqual(command[1:3], ["/Create", "/TN"])
        self.assertEqual(command[command.index("/SC") + 1], "ONLOGON")
        self.assertEqual(command[command.index("/RL") + 1], "LIMITED")
        self.assertIn(str(runner_path), command[command.index("/TR") + 1])

        command_text = " ".join(command).casefold()
        self.assertNotIn("system", command_text)
        self.assertNotIn("highest", command_text)
        self.assertNotIn("/ru", [argument.casefold() for argument in command])
        self.assertNotIn("/rp", [argument.casefold() for argument in command])

    def test_task_settings_remove_time_limit_and_restart_after_failures(self):
        command = app_scheduler.build_task_settings_command()
        command_text = " ".join(command)

        self.assertEqual(Path(command[0]), app_scheduler.POWERSHELL_PATH)
        self.assertIn("New-ScheduledTaskSettingsSet", command_text)
        self.assertIn("[TimeSpan]::Zero", command_text)
        self.assertIn("-MultipleInstances IgnoreNew", command_text)
        self.assertIn("-RestartCount 3", command_text)
        self.assertIn("Set-ScheduledTask", command_text)
        self.assertNotIn("SYSTEM", command_text)

    def test_install_and_remove_require_exact_confirmation_phrases(self):
        with (
            patch("scripts.app_scheduler.save_runner") as save_runner,
            patch("scripts.app_scheduler.subprocess.run") as subprocess_run,
        ):
            with self.assertRaisesRegex(ValueError, app_scheduler.CREATE_CONFIRMATION):
                app_scheduler.install_task(
                    SimpleNamespace(confirm="INSTALL", start_now=False)
                )
            with self.assertRaisesRegex(ValueError, app_scheduler.REMOVE_CONFIRMATION):
                app_scheduler.remove_task(SimpleNamespace(confirm="DELETE"))

        save_runner.assert_not_called()
        subprocess_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()  #Allows this test file to run directly
