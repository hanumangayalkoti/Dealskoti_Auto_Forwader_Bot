from __future__ import annotations

from typing import Literal

Language = Literal["en", "hinglish"]

TEXT: dict[Language, dict[str, str]] = {
    "en": {
        "choose_language": "🌐 Choose your language:",
        "language_saved": "✅ Language saved successfully.",
        "join_required": "⚠️ Please join our Updates Channel before using the bot.",
        "join_continue": "✅ Membership verified. Welcome back!",
        "main_menu": "🏠 <b>Main Menu</b> — choose an action:",
        "unknown_command": "I did not understand that. Use /help to see available commands.",
        "admin_only": "⛔ This command is available only to the admin.",
        "faq_title": "❓ <b>Frequently Asked Questions</b> — page {page}/{pages}",
        "faq_answer": "❓ <b>{question}</b>\n\n{answer}",
        "help_title": "📚 <b>User commands:</b>\n\n{commands}",
        "admin_help_title": "🛠️ <b>Admin commands:</b>\n\n{commands}",
        "support": "📞 <b>Support</b>\n\nHaving issues with payments, tasks, or settings? Don't worry, we're here to help! Send a message to our support team and we will get back to you within 24 hours.\n\nContact: {link}",
        "no_tasks": "📋 You have no tasks yet. Use /newtask to create one.",
        "no_tasks_short": "📋 No tasks yet.",
        "tasks_title": "📋 <b>Your Forwarding Tasks:</b>\n",
        "task_created": "✅ <b>Task #{task_id}</b> created successfully. Forwarding will start automatically.",
        "task_not_found": "⚠️ Task not found.",
        "task_name": "📝 Send a short name for this forwarding task (e.g., 'My Crypto VIP').\n\nUse /back to cancel.",
        "task_source": "📥 <b>Source Selection</b>\n\nSend a public username or chat ID (or forward a message from it).\n\n<i>Tip: You can temporarily pin the chat in your Telegram list so it's easier to find, then unpin it later.</i>\n\nSend another to add more, or /done when finished.\nUse /back to cancel.",
        "task_destination": "📤 <b>Destination Selection</b>\n\nSend a destination username or chat ID (or forward a message from it).\n\nSend another to add more, or /done when finished.\nUse /back to cancel.",
        "invalid_entity": "⚠️ That chat could not be validated for your connected account. Ensure you are a member of it.",
        
        # --- PLANS & SUBSCRIPTIONS ---
        "plans": "💎 <b>Plans:</b>\n\n{plans}\n\nUse /subscribe to choose a plan.",
        "choose_plan": "💎 Choose a plan to view its benefits:",
        "plan_details": "💎 <b>{plan}</b>\n\n{features}\n\nPrice: ₹{monthly}/month\n\nChoose a billing cycle below:",
        "billing_details": "💳 <b>{plan}</b> — {cycle}\n\nOriginal amount: ₹{original}\nDiscount: ₹{discount}\nFinal payable: <b>₹{payable}</b>\n\n<i>Weekly = 7 days | Monthly = 30 days | Yearly = 365 days (20% Off).</i>\n{upgrade_info}\n\nClick below to generate a safe Razorpay link.",
        "upgrade_info": "<i>Note: Since you are upgrading, the unused value of your current plan will be converted into extra days for your new plan!</i>",
        "payment_unavailable": "⚠️ Payments are not configured yet. Please contact support.",
        "payment_created": "💳 Order created for <b>{amount}</b>.\nOrder ID: <code>{order_id}</code>\n\nOpen the checkout link from your payment setup. Plan activation happens only after verified payment.",
        "payment_link": "✅ Payment link created.\n\nPlan: <b>{plan}</b>\nCycle: {cycle}\nAmount: <b>₹{amount}</b>\n\nOpen the official Razorpay link to pay. Your plan activates automatically after payment.",
        "payment_success": "✅ <b>Payment received!</b>\nPlan: <b>{plan}</b>\nDuration: {days} days\nAmount paid: {amount}\nTransaction ID: <code>{txn_id}</code>\nValid until: {expiry}\n\nYour <b>{plan}</b> plan is now active.",
        "payment_failed": "⚠️ Payment link could not be created right now. Please try again shortly.",
        
        # --- ACCOUNT & LOGIN ---
        "account_details": "👤 <b>My Account</b>\n\nName: {name}\nUsername: {username}\nTelegram ID: <code>{user_id}</code>\nPlan: <b>{plan}</b>\nExpiry: <code>{expiry}</code>\nPayment: {payment}\nTelegram session: {session}\nActive tasks: {tasks}\nForwarding usage: {forwarding}\nUpdates membership: {membership}\nLanguage: {user_language}",
        "login_phone": "📱 Send your Telegram phone number with country code (e.g., <code>+919876543210</code>). Do not use spaces.\n\nUse /back to cancel.",
        "login_pin": "🔢 Send the Telegram login code you just received, wrapped in text like <code>PIN12345</code>. Do not send bare digits.\n\nUse /back to cancel.",
        "login_2fa": "🔐 2FA is enabled. Send your password now.\n\nUse /back to cancel.",
        "login_success": "✅ Telegram account connected. Your encrypted session was saved securely.",
        "login_cancelled": "↩️ Login cancelled safely.",
        "login_failed": "⚠️ Telegram could not complete the login. Please check the details and try again via /connect.",
        
        # --- SETTINGS & FLOWS ---
        "back": "◀️ Back",
        "home": "🏠 Home",
        "cancel": "✖️ Cancel",
        "feature_locked": "🔒 <b>Feature Locked</b>\n\nThis setting requires the <b>{plan}</b> plan. Please upgrade your plan to unlock this feature.",
        "settings_saved": "✅ Settings saved successfully for this task.",
        "broadcast_message": "📣 Send the broadcast text to continue. /back to cancel.",
    },
    
    "hinglish": {
        "choose_language": "🌐 Apni language choose karo:",
        "language_saved": "✅ Language save ho gayi.",
        "join_required": "⚠️ Bot use karne se pehle hamara Updates Channel join karo.",
        "join_continue": "✅ Membership verify ho gayi. Welcome back!",
        "main_menu": "🏠 <b>Main Menu</b> — action choose karo:",
        "unknown_command": "Samajh nahi aaya. Available commands ke liye /help use karo.",
        "admin_only": "⛔ Ye command sirf admin ke liye hai.",
        "faq_title": "❓ <b>Frequently Asked Questions</b> — page {page}/{pages}",
        "faq_answer": "❓ <b>{question}</b>\n\n{answer}",
        "help_title": "📚 <b>User commands:</b>\n\n{commands}",
        "admin_help_title": "🛠️ <b>Admin commands:</b>\n\n{commands}",
        "support": "📞 <b>Support</b>\n\nPayments, tasks, ya settings ko lekar koi dikkat hai? Tension mat lo, hum help karenge! Niche diye gaye link par hamari support team ko message bhejo, hum 24 hours ke andar reply karenge.\n\nContact: {link}",
        "no_tasks": "📋 Abhi aapka koi task nahi hai. Naya task banane ke liye /newtask use karo.",
        "no_tasks_short": "📋 Abhi koi task nahi hai.",
        "tasks_title": "📋 <b>Aapke Forwarding Tasks:</b>\n",
        "task_created": "✅ <b>Task #{task_id}</b> ban gaya. Forwarding apne aap start ho jayegi.",
        "task_not_found": "⚠️ Task nahi mila.",
        "task_name": "📝 Is forwarding task ka ek chota naam bhejo (jaise: 'My Crypto VIP').\n\nCancel karne ke liye /back bhejein.",
        "task_source": "📥 <b>Source Selection</b>\n\nEk public username ya chat ID bhejo (ya wahan se koi message forward karo).\n\n<i>Tip: Aap Telegram list me us chat ko temporarily pin kar sakte ho taaki wo easily mil jaye. Baad me unpin kar dena.</i>\n\nEk aur add karne ke liye dusra bhejo, ya finish karne ke liye /done.\nCancel karne ke liye /back.",
        "task_destination": "📤 <b>Destination Selection</b>\n\nEk destination username ya chat ID bhejo (ya wahan se koi message forward karo).\n\nEk aur add karne ke liye dusra bhejo, ya finish karne ke liye /done.\nCancel karne ke liye /back.",
        "invalid_entity": "⚠️ Aapke connected account se ye chat validate nahi ho paayi. Make sure aap uske member ho.",
        
        # --- PLANS & SUBSCRIPTIONS ---
        "plans": "💎 <b>Plans:</b>\n\n{plans}\n\nPlan choose karne ke liye /subscribe use karo.",
        "choose_plan": "💎 Plan choose karke uske benefits dekho:",
        "plan_details": "💎 <b>{plan}</b>\n\n{features}\n\nPrice: ₹{monthly}/month\n\nNiche se billing cycle choose karo:",
        "billing_details": "💳 <b>{plan}</b> — {cycle}\n\nOriginal amount: ₹{original}\nDiscount: ₹{discount}\nFinal payable: <b>₹{payable}</b>\n\n<i>Weekly = 7 din | Monthly = 30 din | Yearly = 365 din (20% Off).</i>\n{upgrade_info}\n\nSafe Razorpay link generate karne ke liye niche click karo.",
        "upgrade_info": "<i>Note: Aap higher plan me upgrade kar rahe ho, isliye aapke current plan ke bache hue dino ka paisa naye plan me extra days ban kar jud jayega!</i>",
        "payment_unavailable": "⚠️ Payments abhi configure nahi hain. Support se contact karo.",
        "payment_created": "💳 <b>{amount}</b> ka order ban gaya.\nOrder ID: <code>{order_id}</code>\n\nPayment setup ka checkout link kholo. Plan verified payment ke baad hi active hoga.",
        "payment_link": "✅ Payment link ban gaya.\n\nPlan: <b>{plan}</b>\nCycle: {cycle}\nAmount: <b>₹{amount}</b>\n\nPay karne ke liye official Razorpay link kholo. Plan payment aate hi apne aap active ho jayega.",
        "payment_success": "✅ <b>Payment mil gaya!</b>\nPlan: <b>{plan}</b>\nDuration: {days} din\nPaid amount: {amount}\nTransaction ID: <code>{txn_id}</code>\nValid: {expiry} tak\n\nTumhara <b>{plan}</b> plan ab active hai.",
        "payment_failed": "⚠️ Payment link abhi nahi ban paaya. Thodi der baad dobara try karo.",
        
        # --- ACCOUNT & LOGIN ---
        "account_details": "👤 <b>My Account</b>\n\nName: {name}\nUsername: {username}\nTelegram ID: <code>{user_id}</code>\nPlan: <b>{plan}</b>\nExpiry: <code>{expiry}</code>\nPayment: {payment}\nTelegram session: {session}\nActive tasks: {tasks}\nForwarding usage: {forwarding}\nUpdates membership: {membership}\nLanguage: {user_language}",
        "login_phone": "📱 Apna Telegram phone number country code ke saath bhejo (jaise: <code>+919876543210</code>). Spaces mat dena.\n\nCancel karne ke liye /back use karo.",
        "login_pin": "🔢 Telegram login code jo abhi aaya hai, usko text me wrap karke bhejo jaise <code>PIN12345</code>. Sirf numbers mat bhejna.\n\nCancel karne ke liye /back use karo.",
        "login_2fa": "🔐 2FA enabled hai. Apna password bhejo.\n\nCancel karne ke liye /back use karo.",
        "login_success": "✅ Telegram account connect ho gaya. Aapka session securely encrypted aur save ho gaya hai.",
        "login_cancelled": "↩️ Login safely cancel ho gaya.",
        "login_failed": "⚠️ Telegram login pura nahi ho paaya. Details check karke /connect ke through dobara try karein.",
        
        # --- SETTINGS & FLOWS ---
        "back": "◀️ Back",
        "home": "🏠 Home",
        "cancel": "✖️ Cancel",
        "feature_locked": "🔒 <b>Feature Locked</b>\n\nYe setting use karne ke liye <b>{plan}</b> plan chahiye. Niche button dabakar upgrade karein.",
        "settings_saved": "✅ Is task ki settings successfully save ho gayi.",
        "broadcast_message": "📣 Broadcast text bhejo. Cancel ke liye /back.",
    },
}

# --- PRIMARY COMMANDS FOR MENU & HELP ---
# Removed aliases like /myaccount, /createtask, /contact, /channel so they don't clutter the UI.
# They are handled safely in main.py to maintain backward compatibility.

USER_COMMANDS: tuple[tuple[str, str, str], ...] = (
    ("/start", "Start the bot", "Bot start karo"),
    ("/help", "Show user commands", "User commands dekho"),
    ("/faq", "Open Frequently Asked Questions", "FAQs kholo"),
    ("/menu", "Open main menu", "Main menu kholo"),
    ("/connect", "Connect Telegram account", "Telegram account connect karo"),
    ("/account", "View account details", "Account details dekho"),
    ("/language", "Change language", "Language badlo"),
    ("/disconnect", "Disconnect only the session", "Sirf session disconnect karo"),
    ("/plans", "View plans", "Plans dekho"),
    ("/subscribe", "Buy a plan", "Plan buy karo"),
    ("/tasks", "View forwarding tasks", "Forwarding tasks dekho"),
    ("/newtask", "Create a forwarding task", "Naya forwarding task banao"),
    ("/pause", "Pause a task", "Task pause karo"),
    ("/resume", "Resume a task", "Task resume karo"),
    ("/deletetask", "Delete a task", "Task delete karo"),
    ("/setting", "Configure task settings", "Task settings configure karo"),
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

# --- HELPER FUNCTIONS ---

def language_for(value: str | None) -> Language:
    return "hinglish" if value == "hinglish" else "en"

def t(language: Language, key: str, **kwargs: object) -> str:
    """Fetches and formats a localized string."""
    return TEXT[language][key].format(**kwargs)

def command_help(language: Language) -> str:
    """Generates the user command list for /help."""
    return "\n".join(
        f"{command} — {hinglish if language == 'hinglish' else english}"
        for command, english, hinglish in USER_COMMANDS
    )

def admin_help() -> str:
    """Generates the admin command list for /adminhelp."""
    return "\n".join(f"{command} — {description}" for command, description in ADMIN_COMMANDS)
