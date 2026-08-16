from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import defaultdict
from contextlib import suppress
from typing import Any

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, MessageIdInvalidError, MessageNotModifiedError
from telethon.tl.custom import Message

from .db import Database
from .plans import PLANS
from .telethon_service import TelethonService

logger = logging.getLogger("dealskoti.forwarding")

class ForwardingEngine:
    def __init__(self, db: Database, telethon_service: TelethonService, max_concurrent_tasks: int = 100):
        self.db = db
        self.telethon = telethon_service
        self.max_tasks = max_concurrent_tasks
        
        # In-memory maps for dynamic runtime
        self._active_tasks: dict[int, dict[str, Any]] = {}
        self._user_plans: dict[int, str] = {}
        
        # Message Mapping for Edit Sync: {task_id: {source_msg_id: {dest_id: dest_msg_id}}}
        self._edit_map: dict[int, dict[int, dict[int, int]]] = defaultdict(lambda: defaultdict(dict))
        
        self._running = False
        self._task_queue: asyncio.Queue = asyncio.Queue()
        self._workers: list[asyncio.Task] = []

    async def start(self) -> None:
        self._running = True
        for _ in range(self.max_tasks):
            worker = asyncio.create_task(self._worker_loop())
            self._workers.append(worker)
            
        await self.reload_all_tasks()
        logger.info("ForwardingEngine started with %d workers.", self.max_tasks)

    async def stop(self) -> None:
        self._running = False
        for worker in self._workers:
            worker.cancel()
        with suppress(asyncio.CancelledError):
            await asyncio.gather(*self._workers, return_exceptions=True)
        logger.info("ForwardingEngine stopped.")

    async def run_until_stopped(self) -> None:
        while self._running:
            await asyncio.sleep(1)

    # --- TASK MANAGEMENT ---

    async def reload_all_tasks(self) -> None:
        self._active_tasks.clear()
        if self.db.pool is None: return
        
        async with self.db.pool.acquire() as conn:
            tasks = await conn.fetch("SELECT * FROM tasks WHERE is_paused = FALSE")
            for task in tasks:
                task_id = task["id"]
                user_id = task["user_id"]
                self._active_tasks[task_id] = dict(task)
                
                # Pre-fetch user plan
                user = await self.db.get_user(user_id)
                self._user_plans[user_id] = str(user["plan"]) if user else "free"
                
                await self._attach_handlers(user_id)

    async def refresh_task(self, task_id: int) -> None:
        task = await self.db.get_task(task_id)
        if not task:
            self._active_tasks.pop(task_id, None)
            return
            
        user_id = task["user_id"]
        if task["is_paused"]:
            self._active_tasks.pop(task_id, None)
        else:
            self._active_tasks[task_id] = dict(task)
            user = await self.db.get_user(user_id)
            self._user_plans[user_id] = str(user["plan"]) if user else "free"
            await self._attach_handlers(user_id)

    async def remove_task(self, task_id: int) -> None:
        self._active_tasks.pop(task_id, None)
        self._edit_map.pop(task_id, None)

    async def refresh_user(self, user_id: int) -> None:
        # Reload all tasks for this user
        tasks = await self.db.list_tasks(user_id)
        for task in tasks:
            await self.refresh_task(task["id"])

    async def remove_user(self, user_id: int) -> None:
        # Remove all tasks for this user from active memory
        to_remove = [tid for tid, t in self._active_tasks.items() if t["user_id"] == user_id]
        for tid in to_remove:
            await self.remove_task(tid)
        self._user_plans.pop(user_id, None)

    # --- TELETHON EVENT HANDLERS ---

    async def _attach_handlers(self, user_id: int) -> None:
        client = await self.telethon.get_client(user_id)
        if not client: return
        
        # Remove old handlers to prevent duplicates
        client.remove_event_handler(self._on_new_message, events.NewMessage)
        client.remove_event_handler(self._on_message_edited, events.MessageEdited)
        
        # Add new handlers
        client.add_event_handler(self._on_new_message, events.NewMessage)
        client.add_event_handler(self._on_message_edited, events.MessageEdited)

    async def _on_new_message(self, event: events.NewMessage.Event) -> None:
        await self._task_queue.put(("new", event))

    async def _on_message_edited(self, event: events.MessageEdited.Event) -> None:
        await self._task_queue.put(("edit", event))

    # --- WORKER LOOP ---

    async def _worker_loop(self) -> None:
        while self._running:
            try:
                action, event = await self._task_queue.get()
                if action == "new":
                    await self._process_new_message(event)
                elif action == "edit":
                    await self._process_edited_message(event)
                self._task_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in forwarding worker: {e}", exc_info=True)

    # --- CORE LOGIC ---

    async def _process_new_message(self, event: events.NewMessage.Event) -> None:
        client: TelegramClient = event.client
        # Telethon client session filename typically contains user_id or we can fetch it
        # For safety, we match the client instance from our TelethonService
        user_id = None
        for uid, c in self.telethon._clients.items():
            if c == client:
                user_id = uid
                break
        if not user_id: return

        chat_id = event.chat_id
        
        # Find tasks that have this chat_id as a source
        for task_id, task in self._active_tasks.items():
            if task["user_id"] != user_id: continue
            
            sources = json.loads(task["sources"] or "[]")
            source_ids = [self._extract_id(s) for s in sources]
            
            if chat_id in source_ids:
                await self._execute_forward(user_id, task, event.message, client)

    async def _execute_forward(self, user_id: int, task: dict, message: Message, client: TelegramClient) -> None:
        plan_name = self._user_plans.get(user_id, "free")
        plan = PLANS.get(plan_name, PLANS["free"])
        settings = json.loads(task["settings"] or "{}")
        task_id = task["id"]

        # 1. Quota Check
        if plan.daily_messages:
            usage = await self.db.daily_usage(user_id)
            if usage >= plan.daily_messages:
                return  # Quota exceeded

        # 2. User Filter (Sender Filter - Platinum Only)
        if plan_name == "platinum" and "user_filter" in settings and settings["user_filter"]:
            sender_id = message.sender_id
            if sender_id not in settings["user_filter"]:
                return

        # 3. Text Processing (Replace, Header, Footer)
        text = message.text or ""
        
        # Blacklist/Whitelist Check
        if plan_name in ("gold", "platinum"):
            blacklist = settings.get("blacklist", [])
            whitelist = settings.get("whitelist", [])
            
            if blacklist and any(b.lower() in text.lower() for b in blacklist):
                return
            if whitelist and not any(w.lower() in text.lower() for w in whitelist):
                return
                
            # Replace rules
            replace_rules = settings.get("replace", {})
            for old_word, new_word in replace_rules.items():
                # Case-insensitive replace mapping
                text = re.sub(re.escape(old_word), new_word, text, flags=re.IGNORECASE)

        # Apply Header & Footer (Silver, Gold, Platinum)
        if plan_name in ("silver", "gold", "platinum"):
            header = settings.get("header", "")
            footer = settings.get("footer", "")
            if header: text = f"{header}\n\n{text}"
            if footer: text = f"{text}\n\n{footer}"

        # Platinum Watermark
        if plan_name == "platinum" and settings.get("watermark"):
            text = f"{text}\n\n<i>@DealsKoti</i>"

        # 4. Dispatch to Destinations
        destinations = json.loads(task["destinations"] or "[]")
        dest_ids = [self._extract_id(d) for d in destinations]
        
        success_count = 0

        for dest_id in dest_ids:
            try:
                sent_msg = None
                
                # --- FORWARDING BEHAVIOR RULE ---
                if plan_name == "free":
                    # Free tier: Keep native "Forwarded from" tag
                    sent_msg = await client.forward_messages(entity=dest_id, messages=message)
                else:
                    # Silver/Gold/Platinum: Clean copy (No forwarded tag)
                    if message.media:
                        sent_msg = await client.send_message(entity=dest_id, message=text, file=message.media)
                    else:
                        sent_msg = await client.send_message(entity=dest_id, message=text)
                
                if sent_msg:
                    success_count += 1
                    # Save Edit Sync Mapping
                    if plan_name == "platinum" and settings.get("edit_sync"):
                        self._edit_map[task_id][message.id][dest_id] = sent_msg.id
                        
                    # Auto Delete (Platinum)
                    auto_delete_secs = settings.get("auto_delete_seconds", 0)
                    if plan_name == "platinum" and auto_delete_secs > 0:
                        asyncio.create_task(self._auto_delete_message(client, dest_id, sent_msg.id, auto_delete_secs))

            except FloodWaitError as e:
                logger.warning(f"FloodWait in task {task_id} for user {user_id}. Sleeping {e.seconds}s.")
                await asyncio.sleep(e.seconds)
            except Exception as e:
                logger.debug(f"Failed to forward message to {dest_id} in task {task_id}: {e}")

        # 5. Deduct Quota ONLY on Success
        if success_count > 0 and plan.daily_messages:
            await self.db.increment_usage(user_id)

    # --- EDIT SYNC ---
    
    async def _process_edited_message(self, event: events.MessageEdited.Event) -> None:
        client: TelegramClient = event.client
        user_id = None
        for uid, c in self.telethon._clients.items():
            if c == client:
                user_id = uid
                break
        if not user_id: return

        chat_id = event.chat_id
        source_msg_id = event.message.id
        
        for task_id, task in self._active_tasks.items():
            if task["user_id"] != user_id: continue
            
            plan_name = self._user_plans.get(user_id, "free")
            settings = json.loads(task["settings"] or "{}")
            
            # Edit sync is Platinum only
            if plan_name != "platinum" or not settings.get("edit_sync"):
                continue
                
            # Multi-Destination Fix: Only edit if mapping exists
            if task_id in self._edit_map and source_msg_id in self._edit_map[task_id]:
                dest_map = self._edit_map[task_id][source_msg_id]
                
                # Apply text processing again for the edited text
                text = event.message.text or ""
                replace_rules = settings.get("replace", {})
                for old_word, new_word in replace_rules.items():
                    text = re.sub(re.escape(old_word), new_word, text, flags=re.IGNORECASE)
                    
                header = settings.get("header", "")
                footer = settings.get("footer", "")
                if header: text = f"{header}\n\n{text}"
                if footer: text = f"{text}\n\n{footer}"
                if settings.get("watermark"): text = f"{text}\n\n<i>@DealsKoti</i>"

                for dest_id, dest_msg_id in dest_map.items():
                    try:
                        await client.edit_message(entity=dest_id, message=dest_msg_id, text=text)
                    except MessageNotModifiedError:
                        pass # Ignore if content didn't actually change after rules
                    except Exception as e:
                        logger.debug(f"Failed to edit synced message {dest_msg_id} in {dest_id}: {e}")

    # --- UTILS ---

    async def _auto_delete_message(self, client: TelegramClient, chat_id: int, message_id: int, delay: int) -> None:
        await asyncio.sleep(delay)
        try:
            await client.delete_messages(entity=chat_id, message_ids=[message_id])
        except Exception as e:
            logger.debug(f"Auto-delete failed for {message_id} in {chat_id}: {e}")

    def _extract_id(self, entity_data: dict) -> int:
        # Handles extracting pure integer IDs from Telethon entity dicts
        return int(entity_data.get("id", 0))
