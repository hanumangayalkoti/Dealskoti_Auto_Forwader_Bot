from __future__ import annotations

from typing import Literal

Language = Literal["en", "hinglish"]


TEXT: dict[Language, dict[str, str]] = {
    "en": {
        "choose_language": "🌐 Choose your language:",
        "language_saved": "✅ Language saved.",
        "join_required": "⚠️ Please join our Updates Channel before using the bot.",
        "join_continue": "✅ Membership verified. Welcome back!",
        "main_menu": "🏠 Main menu — choose an action:",
        "unknown_command": "I did not understand that. Use /help to see available commands.",
        "admin_only": "⛔ This command is available only to the admin.",
        "faq_title": "❓ Frequently Asked Questions — page {page}/{pages}",
        "faq_answer": "❓ {question}\n\n{answer}",
        "help_title": "📚 User commands:\n\n{commands}",
        "admin_help_title": "🛠️ Admin commands:\n\n{commands}",
        "support": "📞 Contact support: {link}",
        "no_tasks": "📋 You have no tasks yet. Use /newtask to create one.",
        "task_created": "✅ Task #{task_id} created. Forwarding will start after access validation.",
        "task_not_found": "⚠️ Task not found.",
        "plans": "💎 Plans:\n\n{plans}\n\nUse /subscribe <plan> <weekly|monthly|yearly> to continue.",
        "payment_unavailable": "⚠️ Payments are not configured yet. Please contact support.",
        "payment_created": "💳 Order created for {amount}.\nOrder ID: {order_id}\n\nOpen the checkout link from your payment setup. Plan activation happens only after verified payment.",
        "payment_success": "✅ Payment verified. Your {plan} plan is active for {days} days.",
        "account": "👤 Account\nPlan: {plan}\nExpiry: {expiry}\nTelegram session: {session}",
        "login_phone": "📱 Send your phone number with country code, for example +919876543210. /back to cancel.",
        "login_pin": "🔢 Send the Telegram code wrapped like PIN123. Do not send bare digits.",
        "login_2fa": "🔐 2FA is enabled. Send the password now, or /back to cancel.",
        "login_success": "✅ Telegram account connected. Your encrypted session was saved securely.",
        "login_cancelled": "↩️ Login cancelled. Sensitive login data was discarded.",
        "login_failed": "⚠️ Telegram could not complete login. Check the details and try /connect again.",
        "task_name": "📝 Send a name for this forwarding task. /back to cancel.",
        "task_source": "📥 Send a public source username or chat ID.",
        "task_destination": "📤 Send a destination username or chat ID.",
        "invalid_entity": "⚠️ That entity could not be validated for this connected account.",
    },
    "hinglish": {
        "choose_language": "🌐 Apni language choose karo:",
        "language_saved": "✅ Language save ho gayi.",
        "join_required": "⚠️ Bot use karne se pehle Updates Channel join karo.",
        "join_continue": "✅ Membership verify ho gayi. Welcome back!",
        "main_menu": "🏠 Main menu — action choose karo:",
        "unknown_command": "Samajh nahi aaya. Available commands ke liye /help use karo.",
        "admin_only": "⛔ Ye command sirf admin ke liye hai.",
        "faq_title": "❓ Frequently Asked Questions — page {page}/{pages}",
        "faq_answer": "❓ {question}\n\n{answer}",
        "help_title": "📚 User commands:\n\n{commands}",
        "admin_help_title": "🛠️ Admin commands:\n\n{commands}",
        "support": "📞 Support se contact karo: {link}",
        "no_tasks": "📋 Abhi koi task nahi hai. /newtask se task banao.",
        "task_created": "✅ Task #{task_id} ban gaya. Access validation ke baad forwarding start hogi.",
        "task_not_found": "⚠️ Task nahi mila.",
        "plans": "💎 Plans:\n\n{plans}\n\nAage badhne ke liye /subscribe <plan> <weekly|monthly> use karo.",
        "payment_unavailable": "⚠️ Payments abhi configure nahi hain. Support se contact karo.",
        "payment_created": "💳 {amount} ka order ban gaya.\nOrder ID: {order_id}\n\nPayment setup ka checkout link kholo. Plan sirf verified payment ke baad active hoga.",
        "payment_success": "✅ Payment verify ho gaya. Tumhara {plan} plan {days} din ke liye active hai.",
        "account": "👤 Account\nPlan: {plan}\nExpiry: {expiry}\nTelegram session: {session}",
        "login_phone": "📱 Country code ke saath phone number bhejo, jaise +919876543210. Cancel ke liye /back.",
        "login_pin": "🔢 Telegram code PIN123 format me bhejo. Bare digits mat bhejna.",
        "login_2fa": "🔐 2FA enabled hai. Password bhejo, ya cancel ke liye /back.",
        "login_success": "✅ Telegram account connect ho gaya. Encrypted session securely save hua.",
        "login_cancelled": "↩️ Login cancel ho gaya. Sensitive login data delete kar diya.",
        "login_failed": "⚠️ Telegram login complete nahi hua. Details check karke /connect dobara try karo.",
        "task_name": "📝 Forwarding task ka naam bhejo. Cancel ke liye /back.",
        "task_source": "📥 Public source username ya chat ID bhejo.",
        "task_destination": "📤 Destination username ya chat ID bhejo.",
        "invalid_entity": "⚠️ Is connected account ke liye entity validate nahi ho paayi.",
    },
}


USER_COMMANDS: tuple[tuple[str, str, str], ...] = (
    ("/start", "Start the bot", "Bot start karo"),
    ("/help", "Show user commands", "User commands dekho"),
    ("/faq", "Open the 15 FAQs", "15 FAQs kholo"),
    ("/menu", "Open main menu", "Main menu kholo"),
    ("/connect", "Connect Telegram account", "Telegram account connect karo"),
    ("/account", "View account details", "Account details dekho"),
    ("/language", "Change language", "Language badlo"),
    ("/disconnect", "Disconnect only the session", "Sirf session disconnect karo"),
    ("/plans", "View plans", "Plans dekho"),
    ("/subscribe", "Buy a plan", "Plan buy karo"),
    ("/tasks", "View forwarding tasks", "Forwarding tasks dekho"),
    ("/newtask", "Create a forwarding task", "Forwarding task banao"),
    ("/deletetask", "Delete a task", "Task delete karo"),
    ("/pause", "Pause a task", "Task pause karo"),
    ("/resume", "Resume a task", "Task resume karo"),
    ("/support", "Contact support", "Support se contact karo"),
    ("/updates", "Open updates channel", "Updates channel kholo"),
)

ADMIN_COMMANDS: tuple[tuple[str, str], ...] = (
    ("/admin", "Open admin panel"),
    ("/adminhelp", "Show admin commands"),
    ("/broadcast", "Broadcast to a filtered audience"),
    ("/grantdays", "Extend a user's plan"),
    ("/block", "Block a user"),
    ("/unblock", "Unblock a user"),
    ("/userinfo", "View a user's details"),
    ("/setplan", "Set a user's plan"),
    ("/stats", "View bot statistics"),
    ("/listusers", "List users"),
    ("/referralpayout", "Process a referral payout"),
)


def language_for(value: str | None) -> Language:
    return "hinglish" if value == "hinglish" else "en"


def t(language: Language, key: str, **kwargs: object) -> str:
    return TEXT[language][key].format(**kwargs)


def command_help(language: Language) -> str:
    return "\n".join(
        f"{command} — {hinglish if language == 'hinglish' else english}"
        for command, english, hinglish in USER_COMMANDS
    )


def admin_help() -> str:
    return "\n".join(f"{command} — {description}" for command, description in ADMIN_COMMANDS)