import asyncio
import json
import logging
from contextlib import suppress
from datetime import datetime, timezone

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import Message

from .db import Database
from .plans import PLANS
from .telethon_service import TelethonService

logger = logging.getLogger("dealskoti.forwarding")

class ForwardingEngine:
    def __init__(self, db: Database, telethon: TelethonService, max_concurrent_tasks: int = 100):
        self.db = db
        self.telethon = telethon
        self.max_concurrent = max_concurrent_tasks
        
        # In-memory stores
        self.clients: dict[int, TelegramClient] = {}  # user_id -> TelegramClient
        self._running = False
        self.edit_map: dict[str, list[tuple[int, int]]] = {}  # "user_id:source_chat:msg_id" -> [(dest_chat, dest_msg_id)]

    async def start(self) -> None:
        """Starts the forwarding engine and connects all valid users."""
        self._running = True
        users = await self.db.list_users(limit=10000)
        for user in users:
            user_id = int(user["telegram_user_id"])
            if not user["is_blocked"]:
                await self.refresh_user(user_id)
        logger.info(f"Forwarding Engine started. Active clients: {len(self.clients)}")

    async def stop(self) -> None:
        """Stops the engine and safely disconnects all clients."""
        self._running = False
        for user_id in list(self.clients.keys()):
            await self.remove_user(user_id)
        logger.info("Forwarding Engine stopped.")

    async def run_until_stopped(self) -> None:
        while self._running:
            await asyncio.sleep(1)

    # --- CLIENT MANAGEMENT ---

    async def refresh_user(self, user_id: int) -> None:
        """Starts or restarts the TelegramClient for a user to apply new settings/tasks."""
        if not self._running:
            return

        # Ensure we don't have stale clients
        await self.remove_user(user_id)

        user = await self.db.get_user(user_id)
        if not user or user["is_blocked"]:
            return

        session_string = await self.telethon._get_session_string(user_id)
        if not session_string:
            return

        client = TelegramClient(StringSession(session_string), self.telethon.api_id, self.telethon.api_hash)
        
        try:
            await client.connect()
            if not await client.is_user_authorized():
                await self.telethon.disconnect(user_id)
                return
                
            # Register Event Handlers
            client.add_event_handler(
                lambda event: self._on_new_message(event, user_id),
                events.NewMessage()
            )
            client.add_event_handler(
                lambda event: self._on_message_edited(event, user_id),
                events.MessageEdited()
            )
            
            self.clients[user_id] = client
            
        except Exception as e:
            logger.error(f"Failed to start forwarding client for user {user_id}: {e}")
            if client.is_connected():
                await client.disconnect()

    async def remove_user(self, user_id: int) -> None:
        """Stops and removes the user's forwarding client."""
        client = self.clients.pop(user_id, None)
        if client:
            client.remove_event_handlers()
            if client.is_connected():
                await client.disconnect()

    async def refresh_task(self, task_id: int) -> None:
        """Hot-reloads a user's client if a specific task was updated."""
        task = await self.db.get_task(task_id)
        if task:
            await self.refresh_user(int(task["user_id"]))

    async def remove_task(self, task_id: int) -> None:
        """Handled gracefully by refresh_user/refresh_task dynamically checking DB."""
        pass

    # --- MESSAGE PROCESSING ENGINE ---

    def _clean_text(self, text: str, settings: dict, plan_name: str) -> str:
        """Applies Blacklist, Replace, Header, and Footer based on user plan."""
        if not text:
            return text
            
        # Blacklist/Whitelist is checked before this function.
        # This function only does replacements and append/prepend.
        
        if plan_name in ["gold", "platinum"]:
            replacements = settings.get("replace", {})
            if isinstance(replacements, dict):
                for old_word, new_word in replacements.items():
                    # Simple case-sensitive replace (could be upgraded to regex if needed)
                    text = text.replace(old_word, new_word)

        if plan_name in ["silver", "gold", "platinum"]:
            header = settings.get("header", "")
            footer = settings.get("footer", "")
            
            parts = []
            if header:
                parts.append(header)
            parts.append(text)
            if footer:
                parts.append(footer)
                
            text = "\n\n".join(parts)
            
        return text

    async def _on_new_message(self, event: events.NewMessage.Event, user_id: int) -> None:
        """Triggered when the user's account receives a new message in any chat."""
        message: Message = event.message
        chat_id = event.chat_id
        
        user = await self.db.get_user(user_id)
        if not user or user["is_blocked"]:
            return
            
        tasks = await self.db.list_tasks(user_id)
        if not tasks:
            return

        plan_name = str(user["plan"] or "free")
        plan = PLANS.get(plan_name, PLANS["free"])

        for task in tasks:
            if task["is_paused"]:
                continue
                
            # Check if this chat is a source for this task
            sources = json.loads(task["sources"] or "[]")
            source_ids = [s.get("id") for s in sources if isinstance(s, dict)]
            if chat_id not in source_ids:
                continue

            settings = json.loads(task["settings"] or "{}")
            
            # --- FILTERS ---
            
            # Sender Filter (Platinum)
            if plan_name == "platinum":
                allowed_senders = settings.get("user_filter", [])
                if allowed_senders and message.sender_id not in allowed_senders:
                    continue
            
            # Blacklist & Whitelist (Gold, Platinum)
            raw_text = (message.raw_text or "").lower()
            if plan_name in ["gold", "platinum"]:
                whitelist = settings.get("whitelist", [])
                blacklist = settings.get("blacklist", [])
                
                if whitelist and not any(w.lower() in raw_text for w in whitelist):
                    continue
                if blacklist and any(b.lower() in raw_text for b in blacklist):
                    continue

            # --- LIMITS ---
            usage = await self.db.daily_usage(user_id)
            if plan.daily_messages and usage >= plan.daily_messages:
                # Quota exceeded, ignore silently to prevent spam
                continue

            # --- FORWARDING ACTIONS ---
            destinations = json.loads(task["destinations"] or "[]")
            if not destinations:
                continue

            client = self.clients.get(user_id)
            if not client:
                return

            sent_tracking = []
            
            for dest in destinations:
                dest_id = dest.get("id")
                if not dest_id: continue

                try:
                    # FREE PLAN: Native Forward (Keeps 'Forwarded from' tag)
                    if plan_name == "free":
                        sent_msg = await client.forward_messages(dest_id, message)
                        if sent_msg:
                            sent_tracking.append((dest_id, sent_msg.id))
                            await self.db.increment_usage(user_id)
                            
                    # PREMIUM PLANS: Clean Copy (No tag, allows formatting)
                    else:
                        new_text = self._clean_text(message.text or "", settings, plan_name)
                        
                        # Watermark logic placeholder (Platinum)
                        # NOTE: Real image watermarking requires Pillow and downloading media.
                        # As per prompt, if it's too heavy, we pass clean media.
                        
                        sent_msg = await client.send_message(
                            dest_id, 
                            message=new_text, 
                            file=message.media, 
                            link_preview=bool(message.web_preview)
                        )
                        
                        if sent_msg:
                            sent_tracking.append((dest_id, sent_msg.id))
                            await self.db.increment_usage(user_id)

                            # Auto-Delete (Platinum)
                            auto_delete_secs = settings.get("auto_delete_seconds", 0)
                            if plan_name == "platinum" and auto_delete_secs > 0:
                                asyncio.create_task(self._auto_delete(client, dest_id, sent_msg.id, auto_delete_secs))

                except Exception as e:
                    logger.warning(f"Task {task['id']} failed to send to {dest_id} for user {user_id}: {e}")

            # Save to Edit Sync Map (Platinum)
            if plan_name == "platinum" and settings.get("edit_sync", False) and sent_tracking:
                map_key = f"{user_id}:{chat_id}:{message.id}"
                self.edit_map[map_key] = sent_tracking

    async def _on_message_edited(self, event: events.MessageEdited.Event, user_id: int) -> None:
        """Triggered when a source message is edited, applies live Edit Sync."""
        message: Message = event.message
        chat_id = event.chat_id
        
        map_key = f"{user_id}:{chat_id}:{message.id}"
        mapped_dests = self.edit_map.get(map_key)
        
        if not mapped_dests:
            return
            
        user = await self.db.get_user(user_id)
        if not user or user["plan"] != "platinum":
            return
            
        client = self.clients.get(user_id)
        if not client:
            return

        # Need to re-apply the user's text replacement settings for this task
        # Since we map dynamically, we fetch tasks that contain this source.
        tasks = await self.db.list_tasks(user_id)
        matched_settings = {}
        for t in tasks:
            srcs = json.loads(t["sources"] or "[]")
            if chat_id in [s.get("id") for s in srcs if isinstance(s, dict)]:
                matched_settings = json.loads(t["settings"] or "{}")
                break
                
        if not matched_settings.get("edit_sync", False):
            return

        new_text = self._clean_text(message.text or "", matched_settings, "platinum")
        
        for dest_id, dest_msg_id in mapped_dests:
            try:
                await client.edit_message(dest_id, dest_msg_id, text=new_text)
            except Exception as e:
                logger.debug(f"Failed to edit synced message {dest_msg_id} in {dest_id}: {e}")

    async def _auto_delete(self, client: TelegramClient, chat_id: int, message_id: int, delay_seconds: int) -> None:
        """Background task to delete a message after X seconds."""
        await asyncio.sleep(delay_seconds)
        if client.is_connected():
            with suppress(Exception):
                await client.delete_messages(chat_id, message_id)
