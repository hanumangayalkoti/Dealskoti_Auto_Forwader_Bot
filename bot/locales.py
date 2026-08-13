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
        "faq_answer": "❓ <b>{question}</b>\n\n{answer}",
        "help_title": "📚 <b>User commands:</b>\n\n{commands}",
        "admin_help_title": "🛠️ <b>Admin commands:</b>\n\n{commands}",
        "support": "📞 Contact support: {link}",
        "no_tasks": "📋 You have no tasks yet. Use /newtask to create one.",
        "task_created": "✅ <b>Task #{task_id}</b> created. Forwarding will start after access validation.",
        "task_not_found": "⚠️ Task not found.",
        "plans": "💎 <b>Plans:</b>\n\n{plans}\n\nUse /subscribe <plan> <weekly|monthly|yearly> to continue.",
        "payment_unavailable": "⚠️ Payments are not configured yet. Please contact support.",
        "payment_created": "💳 Order created for <b>{amount}</b>.\nOrder ID: <code>{order_id}</code>\n\nOpen the checkout link from your payment setup. Plan activation happens only after verified payment.",
        "payment_success": "✅ <b>Payment received!</b>\nPlan: <b>{plan}</b>\nDuration: {days} days\nAmount paid: {amount}\nTransaction ID: <code>{txn_id}</code>\nValid until: {expiry}\n\nYour <b>{plan}</b> plan is now active.",
        "account": "👤 <b>Account</b>\nPlan: <b>{plan}</b>\nExpiry: <code>{expiry}</code>\nTelegram session: {session}",
        "login_phone": "📱 Send your phone number with country code, for example <code>+919876543210</code>. /back to cancel.",
        "login_pin": "🔢 Send the Telegram code wrapped like <code>PIN123</code>. Do not send bare digits.",
        "login_2fa": "🔐 2FA is enabled. Send the password now, or /back to cancel.",
        "login_success": "✅ Telegram account connected. Your encrypted session was saved securely.",
        "login_cancelled": "↩️ Login cancelled. Sensitive login data was discarded.",
        "login_failed": "⚠️ Telegram could not complete login. Check the details and try /connect again.",
        "task_name": "📝 Send a name for this forwarding task. /back to cancel.",
        "task_source": "📥 Send a public source username or chat ID (forward a message from it also works). Send another to add more, or /done when finished.",
        "task_destination": "📤 Send a destination username or chat ID (forward a message from it also works). Send another to add more, or /done when finished.",
        "invalid_entity": "⚠️ That entity could not be validated for this connected account.",
        "back": "◀️ Back",
        "home": "🏠 Home",
        "cancel": "✖️ Cancel",
        "choose_plan": "💎 Choose a plan:",
        "plan_details": "💎 <b>{plan}</b>\n\n{features}\n\nPrice: ₹{monthly}/month\n\nChoose a billing cycle:",
        "billing_details": "💳 <b>{plan}</b> — {cycle}\n\nOriginal amount: ₹{original}\nDiscount: ₹{discount}\nFinal payable: <b>₹{payable}</b>\n\nConfirm to generate an official Razorpay payment link.",
        "payment_link": "✅ Payment link created.\n\nPlan: <b>{plan}</b>\nCycle: {cycle}\nAmount: <b>₹{amount}</b>\n\nOpen the official Razorpay link to pay. Your plan activates only after verified webhook confirmation.",
        "payment_failed": "⚠️ Payment link could not be created. Please try again shortly.",
        "tasks_title": "📋 <b>Your forwarding tasks:</b>",
        "no_tasks_short": "📋 No tasks yet.",
        "account_details": "👤 <b>My Account</b>\n\nName: {name}\nUsername: {username}\nTelegram ID: <code>{user_id}</code>\nPlan: <b>{plan}</b>\nExpiry: <code>{expiry}</code>\nPayment: {payment}\nTelegram session: {session}\nActive tasks: {tasks}\nForwarding: {forwarding}\nUpdates membership: {membership}\nLanguage: {user_language}",
        "broadcast_message": "📣 Send the broadcast text to continue. /back to cancel.",
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
        "faq_answer": "❓ <b>{question}</b>\n\n{answer}",
        "help_title": "📚 <b>User commands:</b>\n\n{commands}",
        "admin_help_title": "🛠️ <b>Admin commands:</b>\n\n{commands}",
        "support": "📞 Support se contact karo: {link}",
        "no_tasks": "📋 Abhi koi task nahi hai. /newtask se task banao.",
        "task_created": "✅ <b>Task #{task_id}</b> ban gaya. Access validation ke baad forwarding start hogi.",
        "task_not_found": "⚠️ Task nahi mila.",
        "plans": "💎 <b>Plans:</b>\n\n{plans}\n\nAage badhne ke liye /subscribe <plan> <weekly|monthly> use karo.",
        "payment_unavailable": "⚠️ Payments abhi configure nahi hain. Support se contact karo.",
        "payment_created": "💳 <b>{amount}</b> ka order ban gaya.\nOrder ID: <code>{order_id}</code>\n\nPayment setup ka checkout link kholo. Plan sirf verified payment ke baad active hoga.",
        "payment_success": "✅ <b>Payment mil gaya!</b>\nPlan: <b>{plan}</b>\nDuration: {days} din\nPaid amount: {amount}\nTransaction ID: <code>{txn_id}</code>\nValid: {expiry} tak\n\nTumhara <b>{plan}</b> plan ab active hai.",
        "account": "👤 <b>Account</b>\nPlan: <b>{plan}</b>\nExpiry: <code>{expiry}</code>\nTelegram session: {session}",
        "login_phone": "📱 Country code ke saath phone number bhejo, jaise <code>+919876543210</code>. Cancel ke liye /back.",
        "login_pin": "🔢 Telegram code <code>PIN123</code> format me bhejo. Bare digits mat bhejna.",
        "login_2fa": "🔐 2FA enabled hai. Password bhejo, ya cancel ke liye /back.",
        "login_success": "✅ Telegram account connect ho gaya. Encrypted session securely save hua.",
        "login_cancelled": "↩️ Login cancel ho gaya. Sensitive login data delete kar diya.",
        "login_failed": "⚠️ Telegram login complete nahi hua. Details check karke /connect dobara try karo.",
        "task_name": "📝 Forwarding task ka naam bhejo. Cancel ke liye /back.",
        "task_source": "📥 Public source username ya chat ID bhejo (uska koi message forward karna bhi chalega). Aur ek bhejo add karne ke liye, ya /done bhejo khatam hone pe.",
        "task_destination": "📤 Destination username ya chat ID bhejo (uska koi message forward karna bhi chalega). Aur ek bhejo add karne ke liye, ya /done bhejo khatam hone pe.",
        "invalid_entity": "⚠️ Is connected account ke liye entity validate nahi ho paayi.",
        "back": "◀️ Back",
        "home": "🏠 Home",
        "cancel": "✖️ Cancel",
        "choose_plan": "💎 Plan choose karo:",
        "plan_details": "💎 <b>{plan}</b>\n\n{features}\n\nPrice: ₹{monthly}/month\n\nBilling cycle choose karo:",
        "billing_details": "💳 <b>{plan}</b> — {cycle}\n\nOriginal amount: ₹{original}\nDiscount: ₹{discount}\nFinal payable: <b>₹{payable}</b>\n\nOfficial Razorpay payment link banane ke liye confirm karo.",
        "payment_link": "✅ Payment link ban gaya.\n\nPlan: <b>{plan}</b>\nCycle: {cycle}\nAmount: <b>₹{amount}</b>\n\nOfficial Razorpay link kholo. Plan sirf verified webhook ke baad active hoga.",
        "payment_failed": "⚠️ Payment link nahi ban paaya. Thodi der baad dobara try karo.",
        "tasks_title": "📋 <b>Aapke forwarding tasks:</b>",
        "no_tasks_short": "📋 Abhi koi task nahi hai.",
        "account_details": "👤 <b>My Account</b>\n\nName: {name}\nUsername: {username}\nTelegram ID: <code>{user_id}</code>\nPlan: <b>{plan}</b>\nExpiry: <code>{expiry}</code>\nPayment: {payment}\nTelegram session: {session}\nActive tasks: {tasks}\nForwarding: {forwarding}\nUpdates membership: {membership}\nLanguage: {user_language}",
        "broadcast_message": "📣 Broadcast message bhejo. Cancel ke liye /back.",
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
