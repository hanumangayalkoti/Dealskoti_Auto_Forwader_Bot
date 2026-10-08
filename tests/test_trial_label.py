"""
Admin / account screens pe trial walon ke aage '🎁 Trial'.

    python -m unittest tests/test_trial_label.py -v
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

os.environ.setdefault("BOT_TOKEN", "1:x")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bot.main import _on_trial, _plan_text  # noqa: E402

NOW = datetime.now(timezone.utc)


def user(plan="gold", claimed_days_ago=1, expiry_in_days=4, claimed=True):
    return {"plan": plan,
            "trial_claimed_at": NOW - timedelta(days=claimed_days_ago) if claimed else None,
            "plan_expiry": NOW + timedelta(days=expiry_in_days)}


class TrialLabelTest(unittest.TestCase):
    def test_trial_user_marked(self):
        self.assertTrue(_on_trial(user()))
        self.assertIn("Trial", _plan_text(user()))

    def test_old_7_day_trial_marked(self):
        self.assertTrue(_on_trial(user(claimed_days_ago=2, expiry_in_days=5)))

    def test_paid_after_trial_not_marked(self):
        # Trial ke baad 30 din kharide → expiry bahut aage → trial nahi
        self.assertFalse(_on_trial(user(claimed_days_ago=3, expiry_in_days=32)))

    def test_never_trialled_paid_user(self):
        self.assertFalse(_on_trial(user(claimed=False, expiry_in_days=20)))
        self.assertNotIn("Trial", _plan_text(user(claimed=False, expiry_in_days=20)))

    def test_other_plan_not_marked(self):
        self.assertFalse(_on_trial(user(plan="silver")))

    def test_expired_trial_not_marked(self):
        self.assertFalse(_on_trial(user(claimed_days_ago=6, expiry_in_days=-1)))

    def test_missing_fields_safe(self):
        self.assertFalse(_on_trial({"plan": "gold"}))
        self.assertEqual(_plan_text(None), "🆓 Free")


if __name__ == "__main__":
    unittest.main()
