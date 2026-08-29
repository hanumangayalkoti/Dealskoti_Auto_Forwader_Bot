"""
Telethon (MTProto userbot) service layer.

Responsibilities:
  * login flow (phone -> OTP -> optional 2FA) and encrypted session storage
  * entity validation for task sources/destinations
  * recent-chat picker
  * forum topic listing (Topics Forwarding)
  * emoji reactions (Auto Reaction System)
  * big-file download via an MTProto bot client (bypasses the 20MB Bot API cap)

NON-NEGOTIABLE RULES (do not "optimise" these away in a rewrite):
  1. Session strings are ALWAYS written through _encode_session(). If an
     encryption key is configured but invalid, we raise instead of silently
     writing plaintext.
  2. Every TelegramClient is lazily connected and ALWAYS disconnected in a
     `finally:` block. We never hold user sessions open between operations.
  3. OTP codes and 2FA passwords are never persisted anywhere.
"""

import logging
from contextlib import suppress

from telethon import TelegramClient, errors, functions, types, utils
from telethon.sessions import StringSession

from .config import Settings
from .db import Database
from .security import SessionCrypto

logger = logging.getLogger("dealskoti.telethon")


class SessionExpired(ValueError):
    """The stored Telethon session is no longer usable — the user must /connect again."""


class TelethonService:
    def __init__(self, settings: Settings, db: Database):
        self.api_id = settings.telegram_api_id
        self.api_hash = settings.telegram_api_hash
        self.db = db
        # Temporary login states: {user_id: {"client", "phone", "phone_code_hash"}}
        self.login_clients: dict[int, dict] = {}
        # Optional at-rest encryption for session strings.
        self._crypto: SessionCrypto | None = None
        self._encryption_key_configured = bool(settings.session_encryption_key)
        if settings.session_encryption_key:
            try:
                self._crypto = SessionCrypto(settings.session_encryption_key)
            except ValueError as exc:
                # Never silently fall back to writing a new Telegram session
                # in plaintext when an encryption key was configured.
                logger.error("Invalid session encryption key: %s", exc)

    # ==========================================
    # SESSION STORAGE (ENCRYPTED)
    # ==========================================

    def _encode_session(self, session_string: str) -> str:
        if self._crypto is None:
            if self._encryption_key_configured:
                raise RuntimeError("SESSION_ENCRYPTION_KEY is invalid; refusing to save plaintext session")
            return session_string
        return f"enc:{self._crypto.encrypt(session_string)}"

    def _decode_session(self, stored: str | None) -> str | None:
        """Reads a stored session. Rows written before encryption was enabled
        are still plaintext, so only the `enc:` prefix is decrypted."""
        if not stored:
            return None
        if not stored.startswith("enc:"):
            return stored
        if self._crypto is None:
            logger.error("Encrypted session found but SESSION_ENCRYPTION_KEY is not set")
            return None
        try:
            return self._crypto.decrypt(stored[4:])
        except ValueError as exc:
            logger.error("Could not decrypt stored session: %s", exc)
            return None

    async def _save_session(self, user_id: int, session_string: str) -> None:
        if self.db.pool is None:
            raise RuntimeError("Database not connected")
        async with self.db.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO sessions (user_id, session_string)
                VALUES ($1, $2)
                ON CONFLICT (user_id) DO UPDATE
                SET session_string = EXCLUDED.session_string, updated_at = CURRENT_TIMESTAMP
                """,
                user_id, self._encode_session(session_string)
            )

    async def _delete_session(self, user_id: int) -> None:
        if self.db.pool is None:
            return
        async with self.db.pool.acquire() as conn:
            await conn.execute("DELETE FROM sessions WHERE user_id = $1", user_id)

    async def _get_session_string(self, user_id: int) -> str | None:
        if self.db.pool is None:
            return None
        async with self.db.pool.acquire() as conn:
            stored = await conn.fetchval("SELECT session_string FROM sessions WHERE user_id = $1", user_id)
        return self._decode_session(stored)

    async def _client_for(self, user_id: int) -> TelegramClient:
        """Builds (but does NOT connect) a client from the user's stored session.

        Raises ValueError if there is no session or it is unreadable — the
        caller is expected to surface that to the user as 'please /connect'.
        """
        session_string = await self._get_session_string(user_id)
        if not session_string:
            raise ValueError("Your Telegram account is not connected. Please use /connect first.")
        try:
            return TelegramClient(StringSession(session_string), self.api_id, self.api_hash)
        except (ValueError, TypeError) as exc:
            logger.warning(f"Invalid stored session for user {user_id}: {exc}")
            await self.disconnect(user_id)
            raise ValueError("Your stored Telegram session is invalid. Please /connect again.")

    # ==========================================
    # LOGIN FLOW
    # ==========================================

    async def _account_info(self, client: TelegramClient) -> dict:
        """Reads the freshly-connected account's identity while the client is
        still alive. Used to build the post-login congratulations message.

        Never raises — a cosmetic message must not be able to fail a login
        that has already succeeded.
        """
        info = {"phone": None, "username": None, "first_name": None, "user_id": None}
        try:
            me = await client.get_me()
        except Exception as exc:
            logger.warning(f"Could not read account info after login: {exc}")
            return info
        if me is None:
            return info
        info["phone"] = getattr(me, "phone", None)
        info["username"] = getattr(me, "username", None)
        info["first_name"] = getattr(me, "first_name", None)
        info["user_id"] = getattr(me, "id", None)
        return info

    async def start_phone_login(self, user_id: int, phone: str) -> None:
        await self.cancel_login(user_id)

        client = TelegramClient(StringSession(), self.api_id, self.api_hash)
        await client.connect()

        try:
            sent_code = await client.send_code_request(phone)
            self.login_clients[user_id] = {
                "client": client,
                "phone": phone,
                "phone_code_hash": sent_code.phone_code_hash,
            }
        except errors.PhoneNumberInvalidError:
            await client.disconnect()
            raise ValueError("The phone number is invalid. Please include the country code (e.g. +91...)")
        except errors.FloodWaitError as e:
            await client.disconnect()
            raise ValueError(f"Telegram blocked requests for {e.seconds} seconds. Try again later.")
        except Exception as e:
            await client.disconnect()
            logger.error(f"Error starting phone login for {user_id}: {e}")
            raise ValueError("Failed to request OTP. Please try again.")

    async def submit_pin(self, user_id: int, pin: str) -> dict | str:
        """Submits the OTP.

        Returns the string "2fa_required" when a cloud password is still
        needed, otherwise a dict of account info for the success message.
        """
        login_data = self.login_clients.get(user_id)
        if not login_data:
            raise ValueError("Login session expired or not found. Please start over.")

        client: TelegramClient = login_data["client"]
        phone = login_data["phone"]
        phone_code_hash = login_data["phone_code_hash"]

        try:
            await client.sign_in(phone=phone, code=pin, phone_code_hash=phone_code_hash)
            session_string = client.session.save()
            await self._save_session(user_id, session_string)
            account_info = await self._account_info(client)
            account_info.setdefault("phone", None)
            if not account_info.get("phone"):
                # Fall back to what the user typed if Telegram withholds it.
                account_info["phone"] = phone
            # Login is fully complete (no 2FA needed), so release the in-memory
            # client NOW. Previously this only happened via submit_2fa() or
            # cancel_login(), which left non-2FA logins connected forever.
            await self.cancel_login(user_id)
            return account_info
        except errors.SessionPasswordNeededError:
            # DON'T cancel login — keep the client so 2FA can be submitted next.
            return "2fa_required"
        except (errors.PhoneCodeInvalidError, errors.PhoneCodeExpiredError):
            await self.cancel_login(user_id)
            raise ValueError("Invalid or expired PIN. Please try again or restart login.")
        except errors.FloodWaitError as e:
            await self.cancel_login(user_id)
            raise ValueError(f"Too many attempts. Try again in {e.seconds} seconds.")
        except Exception as e:
            logger.error(f"Error submitting PIN for {user_id}: {e}")
            await self.cancel_login(user_id)
            raise ValueError("An unexpected error occurred. Please try again.")

    async def submit_2fa(self, user_id: int, password: str) -> dict:
        """Submits the cloud password. Returns account info on success."""
        login_data = self.login_clients.get(user_id)
        if not login_data:
            raise ValueError("Login session expired or not found. Please start over.")

        client: TelegramClient = login_data["client"]
        phone = login_data.get("phone")

        try:
            await client.sign_in(password=password)
            session_string = client.session.save()
            await self._save_session(user_id, session_string)
            account_info = await self._account_info(client)
            if not account_info.get("phone"):
                account_info["phone"] = phone
            await self.cancel_login(user_id)  # release the client only on success
            return account_info
        except errors.PasswordHashInvalidError:
            # Keep the client alive so the user can retry — DO NOT cancel_login.
            raise ValueError("Incorrect 2FA password. Please try again.")
        except errors.FloodWaitError as e:
            await self.cancel_login(user_id)
            raise ValueError(f"Too many attempts. Try again in {e.seconds} seconds.")
        except Exception as e:
            logger.error(f"Error submitting 2FA for {user_id}: {e}")
            await self.cancel_login(user_id)
            raise ValueError("An unexpected error occurred. Please try again.")

    async def cancel_login(self, user_id: int) -> None:
        login_data = self.login_clients.pop(user_id, None)
        if login_data:
            client: TelegramClient = login_data["client"]
            with suppress(Exception):
                if client.is_connected():
                    await client.disconnect()

    async def cancel_all_logins(self) -> None:
        for uid in list(self.login_clients.keys()):
            await self.cancel_login(uid)

    async def disconnect(self, user_id: int) -> None:
        await self._delete_session(user_id)

    # ==========================================
    # ENTITY VALIDATION (SOURCES / DESTINATIONS)
    # ==========================================

    async def validate_for_user(self, user_id: int, target: str) -> dict:
        """
        Checks the user's account can actually reach `target` (username or ID).
        Returns a dict suitable for storing in the task's JSONB columns.
        """
        client = await self._client_for(user_id)

        try:
            await client.connect()
            if not await client.is_user_authorized():
                await self.disconnect(user_id)
                raise SessionExpired("Session expired. Please use /connect to login again.")

            target_clean = target.strip()
            if target_clean.lstrip("-").isdigit():
                target_obj: object = int(target_clean)
            else:
                target_obj = target_clean.lstrip("@") or target_clean

            try:
                entity = await client.get_entity(target_obj)
            except (ValueError, TypeError) as exc:
                # Telethon raises ValueError for unknown usernames and for
                # un-cached ids; retry with a dialog sweep before giving up.
                logger.info(f"Direct entity lookup failed for '{target}': {exc}")
                entity = None
                with suppress(Exception):
                    async for dialog in client.iter_dialogs(limit=200):
                        ent = dialog.entity
                        if isinstance(target_obj, int):
                            if getattr(ent, "id", None) == abs(target_obj) or utils.get_peer_id(ent) == target_obj:
                                entity = ent
                                break
                        else:
                            uname = (getattr(ent, "username", None) or "").lower()
                            if uname and uname == str(target_obj).lower():
                                entity = ent
                                break
                if entity is None:
                    raise ValueError(
                        "Could not find this chat. Make sure your account is a member of it, "
                        "or forward any message from that chat to me instead."
                    )

            title = getattr(
                entity, "title",
                getattr(entity, "username", getattr(entity, "first_name", str(entity.id)))
            )

            return {
                "id": entity.id,
                "title": title,
                "access_hash": getattr(entity, "access_hash", None),
                "username": getattr(entity, "username", None),
                "type": type(entity).__name__,
                # True when the chat is a forum (has Topics). Lets the UI offer
                # topic selection only where it actually applies.
                "is_forum": bool(getattr(entity, "forum", False)),
            }

        except (SessionExpired, ValueError):
            raise
        except errors.FloodWaitError as e:
            raise ValueError(f"Telegram is limiting requests. Try again in {e.seconds} seconds.")
        except Exception as e:
            logger.error(f"Error validating entity '{target}' for user {user_id}: {e}")
            raise ValueError("Failed to validate chat. Please try forwarding a message from it instead.")
        finally:
            with suppress(Exception):
                if client.is_connected():
                    await client.disconnect()

    # ==========================================
    # RECENT CHATS (PICKER)
    # ==========================================

    async def get_top_dialogs(self, user_id: int, limit: int = 20) -> list[dict]:
        """The user's most recent chats/channels, in Telegram's own order
        (pinned first). Bots, groups, channels and private chats are ALL
        included — the only omissions are chats Telegram itself won't show
        this account."""
        session_string = await self._get_session_string(user_id)
        if not session_string:
            return []
        client = TelegramClient(StringSession(session_string), self.api_id, self.api_hash)
        results: list[dict] = []
        try:
            await client.connect()
            if not await client.is_user_authorized():
                logger.warning(f"get_top_dialogs: session for user {user_id} is not authorized")
                return []
            async for dialog in client.iter_dialogs(limit=limit):
                entity = dialog.entity
                results.append({
                    "id": entity.id,
                    "title": dialog.title or getattr(entity, "username", "") or str(entity.id),
                    "access_hash": getattr(entity, "access_hash", None),
                    "username": getattr(entity, "username", None),
                    "type": type(entity).__name__,
                    "is_forum": bool(getattr(entity, "forum", False)),
                })
        except Exception as e:
            # Full traceback so an unexpectedly empty picker is diagnosable
            # from the Railway logs instead of silently returning [].
            logger.error(f"Error fetching dialogs for user {user_id}: {e}", exc_info=True)
        finally:
            with suppress(Exception):
                if client.is_connected():
                    await client.disconnect()
        return results

    # ==========================================
    # FORUM TOPICS (TOPICS FORWARDING)
    # ==========================================

    async def get_forum_topics(self, user_id: int, chat_ref: dict | int, limit: int = 100) -> list[dict]:
        """Lists the topics (message threads) of a forum-enabled supergroup.

        Returns [] for non-forum chats or on any error, so the caller can
        simply fall back to 'forward the whole chat'.
        """
        try:
            client = await self._client_for(user_id)
        except ValueError:
            return []

        topics: list[dict] = []
        try:
            await client.connect()
            if not await client.is_user_authorized():
                return []

            peer = await self._resolve_ref(client, chat_ref)
            if peer is None:
                return []

            result = await client(functions.channels.GetForumTopicsRequest(
                channel=peer,
                offset_date=0,
                offset_id=0,
                offset_topic=0,
                limit=limit,
            ))
            for topic in getattr(result, "topics", []) or []:
                topic_id = getattr(topic, "id", None)
                if topic_id is None:
                    continue
                topics.append({
                    "id": int(topic_id),
                    "title": getattr(topic, "title", None) or f"Topic {topic_id}",
                    "closed": bool(getattr(topic, "closed", False)),
                })
        except errors.FloodWaitError as e:
            logger.warning(f"FloodWait while listing topics for {user_id}: {e.seconds}s")
        except Exception as e:
            # A non-forum chat raises here — that's expected, not an error.
            logger.info(f"Could not list forum topics for user {user_id}: {e}")
        finally:
            with suppress(Exception):
                if client.is_connected():
                    await client.disconnect()
        return topics

    # ==========================================
    # AUTO REACTION SYSTEM
    # ==========================================

    async def send_reaction(
        self,
        user_id: int,
        chat_ref: dict | int,
        message_id: int,
        emoji: str,
        big: bool = False,
    ) -> bool:
        """Reacts to a message with a single emoji using the user's account.

        Returns True on success. Never raises: a chat that disallows the
        chosen reaction, or a FloodWait, must not break the forward that
        just succeeded — it is logged and skipped.
        """
        if not emoji:
            return False
        try:
            client = await self._client_for(user_id)
        except ValueError:
            return False

        try:
            await client.connect()
            if not await client.is_user_authorized():
                return False

            peer = await self._resolve_ref(client, chat_ref)
            if peer is None:
                return False

            await client(functions.messages.SendReactionRequest(
                peer=peer,
                msg_id=int(message_id),
                big=bool(big),
                reaction=[types.ReactionEmoji(emoticon=emoji)],
            ))
            return True
        except errors.ReactionInvalidError:
            logger.info(f"Reaction {emoji!r} not allowed in chat for user {user_id}")
        except errors.FloodWaitError as e:
            logger.warning(f"FloodWait on reaction for user {user_id}: {e.seconds}s")
        except Exception as e:
            logger.info(f"Could not send reaction for user {user_id}: {e}")
        finally:
            with suppress(Exception):
                if client.is_connected():
                    await client.disconnect()
        return False

    async def get_available_reactions(self, user_id: int, chat_ref: dict | int) -> list[str]:
        """Emojis a chat actually permits, so the picker can only offer valid
        ones. An empty list means 'unknown / all allowed' — the caller should
        then fall back to a sensible default set."""
        try:
            client = await self._client_for(user_id)
        except ValueError:
            return []

        allowed: list[str] = []
        try:
            await client.connect()
            if not await client.is_user_authorized():
                return []
            peer = await self._resolve_ref(client, chat_ref)
            if peer is None:
                return []
            full = await client(functions.channels.GetFullChannelRequest(channel=peer))
            reactions = getattr(getattr(full, "full_chat", None), "available_reactions", None)
            for item in getattr(reactions, "reactions", []) or []:
                emoticon = getattr(item, "emoticon", None)
                if emoticon:
                    allowed.append(emoticon)
        except Exception as e:
            logger.info(f"Could not read available reactions for user {user_id}: {e}")
        finally:
            with suppress(Exception):
                if client.is_connected():
                    await client.disconnect()
        return allowed

    # ==========================================
    # SHARED HELPERS
    # ==========================================

    async def _resolve_ref(self, client: TelegramClient, ref: dict | int):
        """Turns a stored source/destination ref (or a bare id) into a peer the
        connected client can use. Tries the cached access_hash first, then
        username, then a plain id lookup."""
        if isinstance(ref, int):
            with suppress(Exception):
                return await client.get_input_entity(ref)
            return None

        if not isinstance(ref, dict):
            return None

        raw_id = ref.get("id")
        access_hash = ref.get("access_hash")
        username = ref.get("username")
        ref_type = (ref.get("type") or "").lower()

        if raw_id is not None and access_hash is not None:
            with suppress(Exception):
                if "user" in ref_type:
                    return types.InputPeerUser(user_id=int(raw_id), access_hash=int(access_hash))
                if "chat" in ref_type and "channel" not in ref_type:
                    return types.InputPeerChat(chat_id=int(raw_id))
                return types.InputPeerChannel(channel_id=int(raw_id), access_hash=int(access_hash))

        if username:
            with suppress(Exception):
                return await client.get_input_entity(str(username).lstrip("@"))

        if raw_id is not None:
            for candidate in (int(raw_id), -int(raw_id), int(f"-100{raw_id}")):
                with suppress(Exception):
                    return await client.get_input_entity(candidate)
        return None

    # ==========================================
    # BIG FILE DOWNLOAD (MTProto BOT CLIENT)
    # ==========================================

    async def media_exists_big(self, bot_token: str, chat_id: int, message_id: int) -> bool:
        """Checks a stored file is still present in the storage channel.

        forwarding.py calls this before attaching a user's stored file. It was
        missing entirely, which raised AttributeError mid-forward and broke
        forwarding for every Platinum user who had uploaded a file.

        Returns True only when the message still exists AND still has media.
        """
        client = TelegramClient(StringSession(), self.api_id, self.api_hash)
        try:
            await client.start(bot_token=bot_token)
            msg = await client.get_messages(chat_id, ids=[message_id])
            msg = msg[0] if isinstance(msg, list) else msg
            return bool(msg and msg.media)
        except Exception as e:
            logger.warning(f"media_exists_big check failed for chat {chat_id} msg {message_id}: {e}")
            # Assume it still exists on a transient error — deleting a user's
            # working file because of one network blip would be far worse.
            return True
        finally:
            with suppress(Exception):
                if client.is_connected():
                    await client.disconnect()

    async def download_media_big(self, bot_token: str, chat_id: int, message_id: int, dest_path: str) -> bool:
        """Downloads media using an MTProto bot client — no 20MB Bot API cap."""
        client = TelegramClient(StringSession(), self.api_id, self.api_hash)
        try:
            await client.start(bot_token=bot_token)
            msg = await client.get_messages(chat_id, ids=[message_id])
            msg = msg[0] if isinstance(msg, list) else msg
            if not msg or not msg.media:
                return False
            await client.download_media(msg, file=dest_path)
            return True
        except Exception as e:
            logger.error(f"download_media_big failed for chat {chat_id} msg {message_id}: {e}")
            return False
        finally:
            with suppress(Exception):
                if client.is_connected():
                    await client.disconnect()
