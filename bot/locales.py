# ==========================================
# COMMAND DEFINITIONS
# ==========================================

# Format: (command, description_en, description_hinglish)
USER_COMMANDS = [
    ("/start", "Start the bot and open main menu", "Bot chalu karein aur menu kholein"),
    ("/help", "View available commands and help", "Commands aur help dekhein"),
    ("/faq", "Frequently Asked Questions", "Aam taur par pooche gaye sawal"),
    ("/menu", "Open the main navigation menu", "Main menu kholein"),
    ("/connect", "Connect your Telegram account", "Apna Telegram account connect karein"),
    ("/account", "View your account details and limits", "Apne account ki details dekhein"),
    ("/language", "Change your language preference", "Apni bhasha (language) badlein"),
    ("/disconnect", "Disconnect your Telegram account safely", "Apna account safely hataein"),
    ("/plans", "View and upgrade your subscription plan", "Plans dekhein aur upgrade karein"),
    ("/tasks", "View and manage your forwarding tasks", "Apne forwarding tasks dekhein"),
    ("/newtask", "Create a new forwarding task", "Naya forwarding task banayein"),
    ("/pause", "Pause an active forwarding task", "Active task ko rokein"),
    ("/resume", "Resume a paused forwarding task", "Ruke hue task ko wapas chalu karein"),
    ("/deletetask", "Delete a forwarding task permanently", "Task ko hamesha ke liye delete karein"),
    ("/setting", "Configure advanced task settings", "Task ki advanced settings set karein"),
    ("/support", "Contact customer support", "Support team se baat karein"),
    ("/updates", "View the bot updates channel", "Bot ke updates channel se judein"),
]

# Format: (command, description_en)
ADMIN_COMMANDS = [
    ("/admin", "Open Admin Dashboard"),
    ("/stats", "View bot statistics"),
    ("/broadcast", "Send a message to users"),
    ("/block", "Block a user from using the bot"),
    ("/unblock", "Unblock a restricted user"),
    ("/grantdays", "Grant premium days to a user"),
    ("/setplan", "Set a specific plan for a user"),
    ("/listusers", "List recently registered users"),
    ("/userinfo", "View a user's details"),
    ("/referralpayout", "Process a referral payout"),
]

# ==========================================
# TRANSLATION DICTIONARY
# ==========================================

TRANSLATIONS = {
    "en": {
        "main_menu": "👋 **Welcome to DealsKoti Forwarder!**\n\nPlease select an option from the menu below:",
        "help_title": "📚 **Help & Commands**\n\nHere are the commands you can use:\n{commands}",
        "admin_help_title": "🛠️ **Admin Commands**\n\n{commands}",
        "admin_only": "⚠️ This command is restricted to administrators only.",
        "support": "📞 **Customer Support**\n\nIf you need help with payments, tasks, or setup, contact us here:\n{link}\n\n*Our team usually replies within 24 hours.*",
        "choose_language": "🌐 Choose your preferred language:",
        "language_saved": "✅ Language preference saved successfully!",
        "faq_title": "❓ **FAQ (Page {page} of {pages})**\n\nClick on a question below to see its answer:",
        "faq_answer": "❓ <b>{question}</b>\n\n💡 {answer}",
        "account_details": "👤 **My Account**\n\nName: {name}\nUsername: {username}\nUser ID: <code>{user_id}</code>\n\n💎 **Subscription**\nPlan: {plan}\nExpiry: {expiry}\nPayment Status: {payment}\n\n⚙️ **Usage & Status**\nTelegram Status: {session}\nActive Tasks: {tasks}\nMessages Today: {forwarding}\nChannel Membership: {membership}\nLanguage: {user_language}",
        "login_phone": "📱 **Login to Telegram**\n\nPlease send your Telegram Phone Number.\nInclude your country code without spaces (e.g., `+919876543210`).",
        "login_pin": "🔑 **Enter OTP/PIN**\n\nTelegram has sent a code to your app. Please enter it below.\n\n*(If your code is `12345`, simply type `12345`)*",
        "login_2fa": "🔒 **Two-Step Verification**\n\nYour account has a 2FA password. Please enter it below to continue:",
        "login_success": "✅ **Account Connected Successfully!**\n\nYour session is securely established. You can now create and manage tasks.",
        "login_cancelled": "↩️ Login process has been cancelled safely.",
        "login_failed": "⚠️ Login failed or timed out. Please try connecting again.",
        "choose_plan": "💎 **Choose a Subscription Plan**\n\nSelect a tier below to view limits and features:",
        "plan_details": "💎 **{plan} Plan**\n\nFeatures & Limits:\n{features}\n\nPrice: ₹{monthly} / month\n\nSelect your billing cycle below:",
        "billing_details": "💳 **Checkout Summary**\n\nPlan: {plan}\nCycle: {cycle}\nOriginal Price: {original}\nDiscount: {discount}\n\n**Payable Amount: {payable}**\n\nClick below to securely generate your payment link.",
        "payment_link": "🔗 **Payment Link Generated**\n\nPlan: {plan} ({cycle})\nPayable: {amount}\n\nClick the button below to pay via Razorpay. Your plan will activate automatically.",
        "payment_success": "🎉 **Payment Successful!**\n\nPlan: {plan}\nDuration added: {days} days\nAmount Paid: {amount}\nTransaction ID: <code>{txn_id}</code>\nNew Expiry Date: {expiry}\n\nThank you for subscribing to DealsKoti! Your new limits have been applied.",
        "payment_failed": "⚠️ Payment could not be initiated or was cancelled.",
        "tasks_title": "📋 **Your Forwarding Tasks:**",
        "no_tasks_short": "📋 You haven't created any tasks yet. Click 'Create New Task' to begin.",
        "task_name": "📝 **New Task: Name**\n\nPlease send a short, recognizable name for this task (e.g., `Amazon Deals`):",
        "task_source": "📥 **New Task: Source Chat**\n\nForward a message from the Source chat, or send its Public Username/ID.\n\n*(You can add multiple sources. Send `/done` when you are finished adding sources).* ",
        "task_destination": "📤 **New Task: Destination Chat**\n\nForward a message from the Destination chat, or send its Public Username/ID.\n\n*(You can add multiple destinations. Send `/done` when you are finished).* ",
        "task_created": "✅ Task #{task_id} has been created successfully!\n\nYou can now configure its settings or resume it from the Tasks menu.",
        "unknown_command": "⚠️ Unknown command. Please send /help to see the list of available commands.",
    },
    "hinglish": {
        "main_menu": "👋 **DealsKoti Forwarder me aapka swagat hai!**\n\nNeeche diye gaye menu se ek option chunein:",
        "help_title": "📚 **Help & Commands**\n\nYe commands aap use kar sakte hain:\n{commands}",
        "admin_help_title": "🛠️ **Admin Commands**\n\n{commands}",
        "admin_only": "⚠️ Ye command sirf Admins use kar sakte hain.",
        "support": "📞 **Customer Support**\n\nAgar aapko payment, tasks, ya setup me madad chahiye, toh yahan message karein:\n{link}\n\n*Humari team aam taur par 24 ghante me reply karti hai.*",
        "choose_language": "🌐 Apni bhasha (language) chunein:",
        "language_saved": "✅ Aapki language save ho gayi hai!",
        "faq_title": "❓ **FAQ (Page {page} of {pages})**\n\nJawab dekhne ke liye kisi bhi sawal par click karein:",
        "faq_answer": "❓ <b>{question}</b>\n\n💡 {answer}",
        "account_details": "👤 **Mera Account**\n\nNaam: {name}\nUsername: {username}\nUser ID: <code>{user_id}</code>\n\n💎 **Subscription**\nPlan: {plan}\nExpiry: {expiry}\nPayment Status: {payment}\n\n⚙️ **Usage & Status**\nTelegram Status: {session}\nActive Tasks: {tasks}\nMessages Today: {forwarding}\nChannel Membership: {membership}\nLanguage: {user_language}",
        "login_phone": "📱 **Telegram se Connect karein**\n\nApna Telegram Mobile Number bhejein.\nBina space diye country code lagana zaroori hai (jaise: `+919876543210`).",
        "login_pin": "🔑 **OTP/PIN Daalein**\n\nTelegram ne aapke app par ek code bheja hoga. Kripya use yahan daalein.\n\n*(Agar code `12345` hai, toh seedha `12345` type karein)*",
        "login_2fa": "🔒 **Two-Step Verification**\n\nAapke account me 2FA laga hua hai. Kripya apna password daalein:",
        "login_success": "✅ **Account Successfully Connect Ho Gaya!**\n\nAb aap tasks bana kar auto-forwarding shuru kar sakte hain.",
        "login_cancelled": "↩️ Login process safely cancel kar diya gaya hai.",
        "login_failed": "⚠️ Login fail ho gaya ya time out ho gaya. Kripya wapas try karein.",
        "choose_plan": "💎 **Subscription Plan Chunein**\n\nLimits aur features dekhne ke liye neeche se ek plan select karein:",
        "plan_details": "💎 **{plan} Plan**\n\nFeatures aur Limits:\n{features}\n\nPrice: ₹{monthly} / mahina\n\nNeeche se apna cycle (time) chunein:",
        "billing_details": "💳 **Checkout Summary**\n\nPlan: {plan}\nCycle: {cycle}\nOriginal Price: {original}\nDiscount: {discount}\n\n**Payable Amount: {payable}**\n\nSecure payment link generate karne ke liye click karein.",
        "payment_link": "🔗 **Payment Link Ban Gaya Hai**\n\nPlan: {plan} ({cycle})\nAmount: {amount}\n\nRazorpay se payment karne ke liye neeche click karein. Plan apne aap activate ho jayega.",
        "payment_success": "🎉 **Payment Successful!**\n\nPlan: {plan}\nDays Added: {days} din\nAmount Paid: {amount}\nTransaction ID: <code>{txn_id}</code>\nNayi Expiry: {expiry}\n\nDealsKoti se judne ke liye shukriya! Aapki nayi limits apply ho chuki hain.",
        "payment_failed": "⚠️ Payment initiate nahi ho paya ya cancel ho gaya.",
        "tasks_title": "📋 **Aapke Forwarding Tasks:**",
        "no_tasks_short": "📋 Abhi tak koi task nahi banaya gaya hai. Naya task banane ke liye 'Create New Task' dabayein.",
        "task_name": "📝 **Naya Task: Naam**\n\nIs task ko pehchanne ke liye ek chota naam bhejein (jaise: `Amazon Deals`):",
        "task_source": "📥 **Naya Task: Source Chat (Jahan se aayega)**\n\nSource chat se koi bhi message yahan forward karein, ya uska Username/ID bhejein.\n\n*(Aap ek se zyada source add kar sakte hain. Kaam poora hone par `/done` bhejein).* ",
        "task_destination": "📤 **Naya Task: Destination Chat (Jahan bhejnai hai)**\n\nDestination chat se koi bhi message yahan forward karein, ya uska Username/ID bhejein.\n\n*(Aap ek se zyada destination add kar sakte hain. Kaam poora hone par `/done` bhejein).* ",
        "task_created": "✅ Task #{task_id} successfully ban gaya hai!\n\nAb aap Tasks menu se iski settings set kar sakte hain ya isko Resume kar sakte hain.",
        "unknown_command": "⚠️ Galat command. Sahi commands ki list dekhne ke liye /help bhejein.",
    }
}

# ==========================================
# HELPER FUNCTIONS
# ==========================================

def language_for(pref: str | None) -> str:
    """Returns a valid language key based on user preference."""
    if pref and pref.lower() in TRANSLATIONS:
        return pref.lower()
    return "en"

def t(lang: str, key: str, **kwargs) -> str:
    """Retrieves and formats a translated string. Raises KeyError if missing (caught by safe_t in main.py)."""
    text = TRANSLATIONS[lang][key]
    if kwargs:
        return text.format(**kwargs)
    return text

def command_help(lang: str) -> str:
    """Generates the localized command help list for normal users."""
    lines = []
    for cmd, desc_en, desc_hin in USER_COMMANDS:
        desc = desc_hin if lang == "hinglish" else desc_en
        lines.append(f"{cmd} - {desc}")
    return "\n".join(lines)

def admin_help() -> str:
    """Generates the command help list for admins (defaults to English internally)."""
    lines = []
    for cmd, desc in ADMIN_COMMANDS:
        lines.append(f"{cmd} - {desc}")
    return "\n".join(lines)
