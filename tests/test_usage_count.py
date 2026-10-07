"""
Daily count ke test: ek source post = 1 count, chahe kitne tasks mein jaye;
Telegram ka duplicate delivery dobara forward/count nahi hota.

    python -m unittest tests/test_usage_count.py -v
"""
import asyncio
import os
import sys
import types
import unittest

os.environ.setdefault("BOT_TOKEN", "1:x")
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from test_media_forward import FakeDB, FakeClient, text_message, SOURCE  # noqa: E402
from bot.forwarding import ForwardingEngine  # noqa: E402


class MultiTaskDB(FakeDB):
    def __init__(self, n_tasks, usage=0):
        super().__init__("silver", {})
        self.n_tasks, self.usage = n_tasks, usage

    async def list_tasks(self, uid):
        return [{"id": i, "task_name": f"T{i}", "is_paused": False,
                 "sources": [{"id": SOURCE}],
                 "destinations": [{"id": -1003000000000 - i, "title": f"D{i}"},
                                  {"id": -1004000000000 - i, "title": f"E{i}"}],
                 "settings": {}} for i in range(1, self.n_tasks + 1)]

    async def daily_usage(self, uid):
        return self.usage


def engine(db):
    eng = ForwardingEngine(db, telethon=None)
    client = FakeClient()
    eng.clients[7] = client

    async def peer(*a, **k):
        return "dest-peer"

    async def nothing(*a, **k):
        return None

    async def same_media(client_, msg, settings_, plan_):
        return msg.media

    eng._resolve_peer = peer
    eng._prepare_media = same_media
    eng._maybe_react = nothing
    eng._warn_once = nothing
    eng._warn_user = nothing
    eng._might_be_source = lambda uid, cid: True
    return eng, client


def event_for(message):
    ev = types.SimpleNamespace(message=message, chat_id=SOURCE)

    async def get_chat():
        return types.SimpleNamespace(id=SOURCE)
    ev.get_chat = get_chat
    return ev


class UsageCountTest(unittest.TestCase):
    def test_same_post_in_two_tasks_counts_once(self):
        db = MultiTaskDB(2)
        eng, client = engine(db)
        asyncio.run(eng._process_new_message(event_for(text_message("Deal", 50)), 7))
        self.assertEqual(len(client.sent), 4)        # 2 tasks × 2 destinations
        self.assertEqual(sum(db.counted), 1)         # par count sirf 1

    def test_second_task_still_runs_at_limit_edge(self):
        db = MultiTaskDB(2, usage=1499)              # silver cap 1500
        eng, client = engine(db)
        asyncio.run(eng._process_new_message(event_for(text_message("Deal", 51)), 7))
        self.assertEqual(len(client.sent), 4)        # dono tasks gaye
        self.assertEqual(sum(db.counted), 1)

    def test_duplicate_delivery_skipped(self):
        db = MultiTaskDB(1)
        eng, client = engine(db)
        msg = text_message("Deal", 52)

        async def go():
            await eng._on_new_message(event_for(msg), 7)
            await eng._on_new_message(event_for(msg), 7)   # Telegram ne dobara bheja
        asyncio.run(go())
        self.assertEqual(len(client.sent), 2)        # sirf ek baar forward
        self.assertEqual(sum(db.counted), 1)

    def test_two_different_posts_count_two(self):
        db = MultiTaskDB(1)
        eng, client = engine(db)

        async def go():
            await eng._on_new_message(event_for(text_message("A", 53)), 7)
            await eng._on_new_message(event_for(text_message("B", 54)), 7)
        asyncio.run(go())
        self.assertEqual(sum(db.counted), 2)


if __name__ == "__main__":
    unittest.main()
