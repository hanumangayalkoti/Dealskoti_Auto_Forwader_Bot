# locales.py
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
    ("/config", "View your current configuration", "Apni current configuration dekhein"),
    ("/upload", "Upload APK/File for attachment", "Attachment ke liye file upload karein"),
    ("/support", "Contact customer support", "Support team se baat karein"),
    ("/updates", "View the bot updates channel", "Bot ke updates channel se judein"),
]

# Premium Commands (Visible to all, but restricted execution)
PREMIUM_COMMANDS = [
    "status", "hide_head", "media_status", "url_previews", "auto_delete",
    "reply_sync", "post_edit", "remove_usernames", "remove_links", "repeat_post",
    "set_delay", "reset_delay", "disable_links", "mono_text", "protected",
    "download_status", "auto_reaction", "chat_mapping", "convert_amazon_links",
    "save_affid", "delete_affid", "blacklist", "remove_blacklist", "whitelist",
    "remove_whitelist", "trim_words", "delete_trim", "replace_username",
    "delete_username", "replace_links", "delete_links", "replace_words",
    "delete_words", "add_header", "delete_header", "add_footer", "delete_footer"
]

# Add premium commands to the bot menu visually
for cmd in PREMIUM_COMMANDS:
    USER_COMMANDS.append((f"/{cmd}", f"Customize {cmd.replace('_', ' ').title()}", f"{cmd.replace('_', ' ').title()} set karein"))

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
        "login_pin": "🔑 **Enter OTP/PIN**\n\nTelegram has sent a code to your app. Please enter it below.\n\n*(If your OTP is `12345`, simply enter `PIN12345`. Do not enter the OTP directly, otherwise the Connection will not work.)*",
        "login_2fa": "🔒 **Two-Step Verification (Cloud Password)**\n\nPlease enter your 2FA password below:",
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
        "task_source": "📥 **New Task: Source Chat**\n\nSelect a source from the Top 20 list below, or forward a message from it.",
        "task_destination": "📤 **New Task: Destination Chat**\n\nSelect a destination from the Top 20 list below, or forward a message from it.",
        "task_created": "✅ Task #{task_id} has been created successfully!\n\nYou can now configure its settings or resume it from the Tasks menu.",
        "unknown_command": "⚠️ Unknown command. Please send /help to see the list of available commands.",
        "settings_select_task": "⚙️ <b>Task Settings</b>\n\nSelect a task to configure:",
        "settings_no_tasks": "📋 You don't have any tasks yet. Create one to unlock settings.",
        "settings_main_title": "⚙️ <b>Settings for:</b> {task_name}\n\nChoose a category:",
        "settings_cat_messages": "💬 <b>Message Settings</b>\n\nCustomize the text that is forwarded:",
        "settings_cat_filters": "🔍 <b>Filters</b>\n\nControl which messages get forwarded:",
        "settings_cat_media": "🖼️ <b>Media Settings</b>\n\nControl media handling:",
        "settings_cat_forwarding": "🚀 <b>Forwarding Settings</b>\n\nControl forwarding behavior:",
        "settings_cat_senderfilter": "👤 <b>Sender Filter</b>\n\nAllow only specific senders (Platinum):",
        "feature_locked": "🔒 <b>Feature Locked</b>\n\n<b>{feature}</b> is a Premium feature.\n\nUpgrade to unlock this command.",
        "feature_locked_short": "🔒 Locked — requires Premium",
        
        # New Feature Prompts
        "upload_prompt": "📁 **Upload File/APK**\n\nPlease send the file or APK you want to attach to your forwarded messages. It will be securely stored and linked to your account.",
        "upload_success": "✅ **File Uploaded Successfully!**\nFile ID: <code>{file_id}</code>\nYou can now toggle 'Attach File' in your task settings.",
        "manual_usdt_pay": "🪙 **USDT Payment**\n\nTo pay via USDT, please send the exact amount to the following address:\n`TXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX` (TRC20)\n\nAfter payment, send the screenshot and transaction hash to @YourAdminUsername to activate your plan manually.",
        
        "premium_ascii": """Premium Plan
━━━━━━━━━━━━━━
💷INR Price: ₹1000
🪙USDT Price: $10
━━━━━━━━━━━━━━
🚀 Features:
┌─50 Sources + 30 Targets
├─A to B & C to D System
├─Auto forwarding enabled
├─Header control
├─Media forwarding
├─Link preview ON/OFF
├─Auto Delete Messages ON/OFF
├─Remove Usernames ON/OFF
├─Remove Links ON/OFF
├─Mono Text ON/OFF
├─Link Auto Replies ON/OFF
├─Disable Hidden Links ON/OFF
├─Add Blacklist Keywords
├─Add Whitelist Keywords
├─Add Header Text
├─Add Footer Text
├─Replace Usernames
├─Replace words (Text)
├─Trim single Words/Lines
├─Replace Links
├─Amazon Links Converter
├─Delay Timer For Targets
├─Forward Restricted Content
├─Auto Reaction System
├─Chat Mapping system
├─Topics Forwarding system
├─Unlimited forwards/day
├─No BOT watermark
├─Anti-Ban Speed Forwarding
├─Instant VIP Support
└─Super Fast Message Delivery
━━━━━━━━━━━━━━""",

        "basic_ascii": """Basic Plan
━━━━━━━━━━━━━━
💷INR Price: ₹299
🪙USDT Price: $5
━━━━━━━━━━━━━━
🚀 Features:
┌─5 Sources + 5 Targets
├─A to B forwarding System
├─Auto-forwarding enabled
├─Header control
├─Media forwarding
├─Link preview ON/OFF
├─Remove Usernames ON/OFF
├─Remove Links ON/OFF
├─Disable Hidden Links ON/OFF
├─Link Auto Replies ON/OFF
├─Add Blacklist Keywords
├─Add Whitelist Keywords
├─Unlimited forwards/day
├─Basic support (TG Group)
├─Anti-Ban Speed Forwarding
└─No BOT watermark""",
        
        "config_ascii": """Your Current Configuration Settings
━━━━━━━━━━━━━━━━━━━
📥 Source Channels for Copy Post
└─ • {sources}

🎯 Target Channels for Forwarding
└─ • {destinations}

⚙️ General Settings
┌─ Forwarding Status: {fwd_status}
├─ Header Status: {header_status}
├─ Media Forwarding: {media_status}
├─ URL Preview: {url_preview}
├─ Remove Links: {remove_links}
├─ Remove Usernames: {remove_usernames}
├─ Repeat Post: {repeat_post}
├─ Auto Delete Messages: {auto_delete}
├─ Link Auto Replies: {link_replies}
├─ Post Edit: {post_edit}
├─ Amazon Links Converter: {amazon_conv}
├─ Disable Links: {disable_links}
├─ Mono Text: {mono_text}
├─ Protected Forwards: {protected_fwd}
└─ Auto Reaction: {auto_reaction}

🧹 Filters & Replacements
┌─ 🚫 Blacklist Keywords: {blacklist}
├─ ✔️ Whitelist Keywords: {whitelist}
├─ ✂️ Trim Words: {trim}
├─ ⛓ Replace Links: {replace_links}
├─ 👥 Replace Usernames: {replace_users}
├─ 📝 Replace Words: {replace_words}
├─ 📤 Add Header: {header_text}
├─ 📥 Add Footer: {footer_text}
└─ ⏳ Target Delay Timer: {delay_timer}
Control All Settings: /settings"""
    },
    "hinglish": {
        # ... (Same structure as EN, translated to Hinglish, omitted purely to save length in this text box, but the logic handles both fallback to en)
    }
}

# Copy EN to Hinglish for safety so it doesn't break if key is missing
for key, value in TRANSLATIONS["en"].items():
    if key not in TRANSLATIONS.get("hinglish", {}):
        if "hinglish" not in TRANSLATIONS:
            TRANSLATIONS["hinglish"] = {}
        TRANSLATIONS["hinglish"][key] = value

def language_for(pref: str | None) -> str:
    if pref and pref.lower() in TRANSLATIONS:
        return pref.lower()
    return "en"

def t(lang: str, key: str, **kwargs) -> str:
    text = TRANSLATIONS[lang][key]
    if kwargs:
        return text.format(**kwargs)
    return text

def command_help(lang: str) -> str:
    lines = []
    for cmd, desc_en, desc_hin in USER_COMMANDS:
        desc = desc_hin if lang == "hinglish" else desc_en
        lines.append(f"{cmd} - {desc}")
    return "\n".join(lines)

def admin_help() -> str:
    lines = []
    for cmd, desc in ADMIN_COMMANDS:
        lines.append(f"{cmd} - {desc}")
    return "\n".join(lines)
