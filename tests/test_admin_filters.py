"""
Admin user list ke filter (Sab / Paid / Trial / Free / Block).

    python -m unittest tests/test_admin_filters.py -v
"""
import os
import sys
import unittest

os.environ.setdefault("BOT_TOKEN", "1:x")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bot.main import _split_action, USER_SEGMENT_NAMES, BROADCAST_AUDIENCES  # noqa: E402
from bot.db import Database  # noqa: E402


class FilterTest(unittest.TestCase):
    def test_split_action(self):
        self.assertEqual(_split_action("uinfo"), ("uinfo", "all"))        # purane buttons
        self.assertEqual(_split_action("grant~trial"), ("grant", "trial"))
        self.assertEqual(_split_action("block~bogus"), ("block", "all"))

    def test_every_segment_has_sql(self):
        for seg, _ in USER_SEGMENT_NAMES:
            self.assertIn(seg, Database.USER_SEGMENTS)

    def test_callback_data_fits(self):
        # Telegram callback_data 64 byte tak
        self.assertLessEqual(len("apick:payout~blocked:999:9999999999999"), 64)

    def test_broadcast_audiences(self):
        keys = [k for k, _ in BROADCAST_AUDIENCES]
        for k in ("all", "running", "paid", "trial", "expired", "english", "hinglish"):
            self.assertIn(k, keys)


if __name__ == "__main__":
    unittest.main()
