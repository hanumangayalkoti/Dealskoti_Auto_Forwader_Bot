"""
Session tootna (watcher) + session band hone pe admin hook.

    python -m unittest tests/test_session_alerts.py -v
"""
import asyncio
import os
import sys
import types
import unittest

os.environ.setdefault("BOT_TOKEN", "1:x")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bot.forwarding import ForwardingEngine  # noqa: E402


class WatchTest(unittest.TestCase):
    def engine(self):
        eng = ForwardingEngine(types.SimpleNamespace(), types.SimpleNamespace(), 2)
        eng._running = True
        self.calls = []

        async def refresh(uid):
            self.calls.append(uid)
            return "ok"
        eng.refresh_user = refresh
        return eng

    def test_unexpected_disconnect_reconnects(self):
        async def go():
            eng = self.engine()
            fut = asyncio.get_running_loop().create_future()
            client = types.SimpleNamespace(disconnected=fut)
            eng.clients[7] = client
            task = asyncio.create_task(eng._watch_client(7, client))
            fut.set_result(None)
            await task
            return eng
        eng = asyncio.run(go())
        self.assertEqual(self.calls, [7])
        self.assertNotIn(7, eng.clients)

    def test_our_own_removal_is_ignored(self):
        async def go():
            eng = self.engine()
            fut = asyncio.get_running_loop().create_future()
            client = types.SimpleNamespace(disconnected=fut)
            eng.clients[7] = client
            task = asyncio.create_task(eng._watch_client(7, client))
            eng.clients.pop(7)                 # refresh / stop / block ne hataya
            fut.set_result(None)
            await task
        asyncio.run(go())
        self.assertEqual(self.calls, [])

    def test_stopped_engine_is_ignored(self):
        async def go():
            eng = self.engine()
            fut = asyncio.get_running_loop().create_future()
            client = types.SimpleNamespace(disconnected=fut)
            eng.clients[7] = client
            task = asyncio.create_task(eng._watch_client(7, client))
            eng._running = False
            fut.set_result(None)
            await task
        asyncio.run(go())
        self.assertEqual(self.calls, [])

    def test_session_dead_calls_admin_hook(self):
        got = []

        async def hook(uid, reason):
            got.append((uid, reason))

        async def nowarn(*a, **k):
            pass

        async def go():
            eng = ForwardingEngine(types.SimpleNamespace(), types.SimpleNamespace(), 2)
            eng._warn_once = nowarn
            eng.on_session_dead = hook
            await eng._session_dead(9, "login khatam")
        asyncio.run(go())
        self.assertEqual(got, [(9, "login khatam")])


if __name__ == "__main__":
    unittest.main()
