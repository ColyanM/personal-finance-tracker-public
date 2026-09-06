import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts import daily_automation, task_scheduler
from scripts.settings import clean_setting


class DailyAutomationTests(unittest.TestCase):
    def test_task_runner_uses_current_daily_options(self):
        arguments = SimpleNamespace(
            currency="NZD",
            hours=24,
            max_rows=20,
            bnz_days=None,
            plaid_days=30,
            skip_bnz=False,
            skip_plaid=False,
            skip_email=False,
            send_if_empty=True,
        )

        runner_text = task_scheduler.build_runner_text(
            arguments,
            python_path=Path("C:/Python/python.exe"),
        )

        self.assertIn("--confirm RUN_DAILY", runner_text)
        self.assertIn("--plaid-days 30", runner_text)
        self.assertIn("--send-if-empty", runner_text)
        self.assertNotIn("FINANCE_HUB_PLAID_ENVIRONMENT", runner_text)
        self.assertNotIn("FINANCE_HUB_ALLOW_PLAID_PRODUCTION", runner_text)
        self.assertNotIn("daily-alert-email-password", runner_text)

    def test_failure_email_body_stays_simple(self):
        body = daily_automation.build_failure_email_body(["Example Bank failed: timeout"])

        self.assertIn("Finance Hub daily refresh had a problem", body)
        self.assertIn("Example Bank failed: timeout", body)
        self.assertNotIn("access_token", body)

    def test_partial_plaid_refresh_is_reported_as_needing_attention(self):
        result = (
            None,
            "2025-01-15",
            12,
            (3, 0, 4, 1, 0),
            2,
            5,
            "",
            ["Plaid connection 3 (Example Credit Union) needs bank sign-in"],
        )

        ok, detail = daily_automation.run_refresh_step(
            "US",
            lambda: result,
            daily_automation.summarize_plaid_result,
            lambda refresh_result: not refresh_result[7],
        )

        self.assertFalse(ok)
        self.assertIn("US partial", detail)
        self.assertIn("Example Credit Union", detail)

    def test_status_email_body_summarizes_refresh_and_transactions(self):
        preview = {
            "since": "2025-01-15 00:00:00",
            "transaction_count": 3,
            "needs_review_count": 1,
            "spending_minor": 1250,
            "income_minor": 5000,
            "currency": "NZD",
            "rows": [
                {
                    "posted_date": "2025-01-15",
                    "merchant": "Example Coffee Shop",
                    "display_amount": "-$7.50 NZD",
                    "original_amount": "-$7.50 NZD",
                    "category": "Dining",
                }
            ],
            "shown_count": 1,
            "has_more": True,
        }
        monthly_status = {
            "currency": "NZD",
            "start_date": "2025-01-01",
            "end_date": "2025-01-31",
            "income_minor": 500000,
            "spending_minor": 125000,
            "expense_budget_minor": 200000,
            "remaining_budget_minor": 75000,
        }
        net_worth_status = {
            "currency": "NZD",
            "date": "2025-01-15",
            "total_minor": 1250000,
            "previous_date": "2025-01-14",
            "previous_total_minor": 1200000,
            "change_minor": 50000,
        }

        body = daily_automation.build_status_email_body(
            ["Example Bank ok: 1 posted added"],
            preview,
            monthly_status,
            net_worth_status,
        )

        self.assertIn("Finance Hub daily status", body)
        self.assertIn("Example Bank ok: 1 posted added", body)
        self.assertIn("New posted transactions: 3", body)
        self.assertIn("Needs review: 1", body)
        self.assertIn("$12.50 NZD", body)
        self.assertIn("Example Coffee Shop", body)
        self.assertIn("2 more not shown", body)
        self.assertIn("Monthly budget (2025-01-01 to 2025-01-31)", body)
        self.assertIn("Budget remaining: $750.00 NZD", body)
        self.assertIn("Current: $12,500.00 NZD", body)
        self.assertIn("Change from 2025-01-14: up $500.00 NZD", body)
        self.assertNotIn("Checking", body)

    def test_net_worth_change_text_hides_delta_when_prior_history_is_missing(self):
        status = {
            "currency": "NZD",
            "change_minor": 50000,
            "missing_history_count": 1,
        }

        text = daily_automation.net_worth_change_text(status)

        self.assertEqual(
            text,
            "Change from day before: unavailable (1 account missing prior balance history)",
        )

    def test_alert_settings_are_checked_before_saving(self):
        self.assertEqual(clean_setting("daily_alert_time", "7:05"), "07:05")
        self.assertEqual(clean_setting("daily_alert_hours", "48"), "48")
        self.assertEqual(clean_setting("daily_alert_currency", "usd"), "USD")

        with self.assertRaises(ValueError):
            clean_setting("daily_alert_max_rows", "200")


if __name__ == "__main__":
    unittest.main()  #Allows this test file to run directly
