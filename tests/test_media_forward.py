"""
Media Forwarding toggle ke test — asli Telegram / database ke bina.

Chalane ka tareeka (repo ke root se):
    python -m unittest tests/test_media_forward.py -v
"""
import asyncio
import os
import sys
import types
import unittest
from datetime import datetime, timezone

os.environ.setdefault("BOT_TOKEN", "1:x")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from telethon.tl.types import MessageMediaPhoto  # noqa: E402

from bot.forwarding import ForwardingEngine  # noqa: E402

SOURCE = -1001111111111
DEST = -1002222222222


class FakeDB:
    def __init__(self, plan, settings):
        self.plan, self.settings, self.counted = plan, settings, []

    async def get_user(self, uid):
        return {"plan": self.plan, "is_blocked": False, "telegram_user_id": uid}

    async def list_tasks(self, uid):
        return [{"id": 1, "task_name": "T", "is_paused": False,
                 "sources": [{"id": SOURCE}], "destinations": [{"id": DEST, "title": "D"}],
                 "settings": self.settings}]

    async def daily_usage(self, uid):
        return 0

    async def increment_usage_bulk(self, uid, tid, n, quota=None):
        self.counted.append(quota if quota is not None else n)

    def __getattr__(self, name):                 # baaki saare DB kaam — kuch nahi
        async def nothing(*a, **k):
            return None
        return nothing


class FakeClient:
    def __init__(self):
        self.sent = []

    async def send_message(self, peer, **kw):
        self.sent.append(("send", kw.get("message"), kw.get("file")))
        return types.SimpleNamespace(id=len(self.sent) + 100)

    async def forward_messages(self, peer, msg, **kw):
        self.sent.append(("forward", getattr(msg, "message", None), "native"))
        return types.SimpleNamespace(id=len(self.sent) + 100)


def photo_message(caption: str, mid: int = 10):
    media = MessageMediaPhoto(photo=None)
    return types.SimpleNamespace(
        id=mid, message=caption, raw_text=caption, text=caption, media=media, entities=[],
        web_preview=None, grouped_id=None, reply_to=None, fwd_from=None, sender_id=1,
        date=datetime.now(timezone.utc), post=True, out=False, buttons=None, reply_markup=None,
    )


def text_message(text: str, mid: int = 11):
    m = photo_message(text, mid)
    m.media = None
    return m


class MediaForwardTest(unittest.TestCase):
    def run_case(self, plan, settings, message, album=None):
        db = FakeDB(plan, settings)
        eng = ForwardingEngine(db, telethon=None)
        client = FakeClient()
        eng.clients[7] = client

        async def peer(*a, **k):
            return "dest-peer"

        async def same_media(client_, msg, settings_, plan_):
            return msg.media

        async def nothing(*a, **k):
            return None

        eng._resolve_peer = peer
        eng._prepare_media = same_media
        eng._maybe_react = nothing
        eng._warn_once = nothing
        event = types.SimpleNamespace(message=message, chat_id=SOURCE)

        async def get_chat():
            return types.SimpleNamespace(id=SOURCE)
        event.get_chat = get_chat

        async def go():
            await eng._process_new_message(event, 7, album=album)
            await asyncio.sleep(0)
        asyncio.run(go())
        return client.sent, db.counted

    def test_paid_media_on_sends_photo_with_caption(self):
        sent, counted = self.run_case("silver", {}, photo_message("Deal 50% off"))
        self.assertEqual(len(sent), 1)
        self.assertIsNotNone(sent[0][2])             # photo saath gayi
        self.assertEqual(counted, [1])

    def test_paid_media_off_sends_caption_only(self):
        sent, counted = self.run_case("silver", {"media_forward": False}, photo_message("Deal 50% off"))
        self.assertEqual(len(sent), 1)
        self.assertIsNone(sent[0][2])                # photo NAHI gayi
        self.assertIn("Deal 50% off", sent[0][1])    # sirf caption
        self.assertEqual(counted, [1])

    def test_paid_media_off_photo_without_caption_is_skipped(self):
        sent, counted = self.run_case("gold", {"media_forward": False}, photo_message(""))
        self.assertEqual(sent, [])                   # kuch nahi gaya
        self.assertEqual(counted, [])                # daily limit nahi kati

    def test_paid_media_off_plain_text_unchanged(self):
        sent, _ = self.run_case("basic", {"media_forward": False}, text_message("Hello deal"))
        self.assertEqual(len(sent), 1)
        self.assertIn("Hello deal", sent[0][1])

    def test_free_plan_unchanged_native_forward_even_if_off(self):
        sent, _ = self.run_case("free", {"media_forward": False}, photo_message("Deal"))
        self.assertEqual(sent[0][2], "native")       # pehle jaisa native forward, photo ke saath

    def test_paid_media_off_album_sends_caption_once(self):
        items = [photo_message("", 20), photo_message("Album deal", 21), photo_message("", 22)]
        sent, counted = self.run_case("gold", {"media_forward": False}, items[0], album=items)
        self.assertEqual(len(sent), 1)               # ek hi message
        self.assertIsNone(sent[0][2])                # koi photo nahi
        self.assertIn("Album deal", sent[0][1])
        self.assertEqual(counted, [1])

    def test_media_on_helper(self):
        self.assertTrue(ForwardingEngine._media_on({}, "silver"))
        self.assertFalse(ForwardingEngine._media_on({"media_forward": False}, "platinum"))
        self.assertTrue(ForwardingEngine._media_on({"media_forward": False}, "free"))


if __name__ == "__main__":
    unittest.main()
