USER_COMMANDS = [
    ("/start", "Open the main menu", "Main menu kholen"),
    ("/menu", "Open the main menu", "Main menu kholen"),
    ("/help", "View plan features and controls", "Plan features aur controls dekhein"),
    ("/connect", "Connect your Telegram account", "Apna Telegram account connect karein"),
    ("/account", "View account and subscription details", "Account aur plan details dekhein"),
    ("/disconnect", "Disconnect your Telegram account", "Telegram account disconnect karein"),
    ("/plans", "View plans and payment options", "Plans aur payment options dekhein"),
    ("/tasks", "Manage forwarding tasks", "Forwarding tasks manage karein"),
    ("/newtask", "Create a forwarding task", "Naya forwarding task banayein"),
    ("/settings", "Configure forwarding settings", "Forwarding settings configure karein"),
    ("/support", "Contact support", "Support se contact karein"),
    ("/language", "Change language", "Language badlein"),
    ("/updates", "Open the updates channel", "Updates channel kholen"),
]

ADMIN_COMMANDS = [
    ("/admin", "Open admin panel"),
    ("/stats", "View bot statistics"),
    ("/broadcast", "Broadcast a message"),
    ("/grantdays", "Grant plan days"),
    ("/block", "Block a user"),
    ("/unblock", "Unblock a user"),
    ("/listusers", "List recent users"),
    ("/userinfo", "View user information"),
]

TRANSLATIONS = {
    "en": {
        "main_menu": "Hi {name},\n\nWelcome to Dealskoti Auto Forwarder Bot.\nMake auto forwarding so simple.\n\nOur bot gives 100% security for your account data.\n\nPlease select an option from the menu below.",
        "language": "Choose your preferred language:",
        "language_saved": "Language preference saved.",
        "connect_phone": "Connect Account\n\nPlease send your phone number with country code and no spaces.\nExample: +919876543210",
        "connect_otp": "Enter the OTP sent to your Telegram app.",
        "connect_2fa": "Your Telegram account has two-step verification enabled. Send your cloud password.",
        "connected": "Telegram account connected successfully.",
        "cancelled": "Cancelled safely.",
        "account": "My Account\n\nName: {name}\nUsername: {username}\nTelegram ID: {user_id}\n\nPlan: {plan}\nPlan started: {started}\nPlan ends: {expiry}\nLast transaction: {txn}\nActive tasks: {tasks}\nMessages today: {usage}\nTelegram account: {session}",
        "plans": "Choose a plan to see its features.",
        "tasks": "Your forwarding tasks:",
        "no_tasks": "You do not have any forwarding tasks yet.",
        "task_name": "Send a name for this task.",
        "task_source": "Select source chats below, or send a public username/link.\nExample: @Dealkoti or https://t.me/Dealkoti",
        "task_destination": "Select destination chats below, or send a public username/link.\nExample: @Dealkoti or https://t.me/Dealkoti",
        "task_created": "Task created successfully.",
        "task_limit": "Your {plan} plan allows a maximum of {limit} {kind}. Upgrade for more.",
        "invalid_chat": "Please use this format:\n@Dealkoti\nor\nhttps://t.me/Dealkoti",
        "settings": "Bot Settings for {task}:",
        "locked": "{feature} is locked on your current plan. Upgrade to use it.",
        "faq": "FAQ page {page}/{pages}\n\nChoose a question:",
        "faq_answer": "Question:\n{question}\n\nAnswer:\n{answer}",
        "support": "For support, contact: {link}",
        "payment_unavailable": "Razorpay is not configured yet. Please contact support.",
        "usdt": "USDT payment is manual. Send the payment proof and transaction hash to support for verification.",
        "upload_prompt": "Send the file you want to store for forwarding. This feature is available on Platinum.",
        "upload_saved": "File saved. You can enable file attachment in task settings.",
    },
    "hinglish": {
        "main_menu": "Hi {name},\n\nDealskoti Auto Forwarder Bot me welcome.\nAuto forwarding ko simple banayein.\n\nAapke account data ki 100% security rakhi jaati hai.\n\nNeeche se option select karein.",
        "language": "Apni preferred language choose karein:",
        "language_saved": "Language save ho gayi.",
        "connect_phone": "Connect Account\n\nApna phone number country code ke saath bina space bhejein.\nExample: +919876543210",
        "connect_otp": "Telegram app par aaya OTP bhejein.",
        "connect_2fa": "Aapke Telegram account par two-step verification hai. Cloud password bhejein.",
        "connected": "Telegram account successfully connect ho gaya.",
        "cancelled": "Safe tareeke se cancel ho gaya.",
        "account": "My Account\n\nNaam: {name}\nUsername: {username}\nTelegram ID: {user_id}\n\nPlan: {plan}\nPlan start: {started}\nPlan end: {expiry}\nLast transaction: {txn}\nActive tasks: {tasks}\nAaj ke messages: {usage}\nTelegram account: {session}",
        "plans": "Features dekhne ke liye plan select karein.",
        "tasks": "Aapke forwarding tasks:",
        "no_tasks": "Abhi koi forwarding task nahi hai.",
        "task_name": "Is task ka naam bhejein.",
        "task_source": "Neeche source chat select karein, ya public username/link bhejein.\nExample: @Dealkoti ya https://t.me/Dealkoti",
        "task_destination": "Neeche destination chat select karein, ya public username/link bhejein.\nExample: @Dealkoti ya https://t.me/Dealkoti",
        "task_created": "Task successfully create ho gaya.",
        "task_limit": "Aapke {plan} plan me maximum {limit} {kind} allowed hain. Zyada ke liye upgrade karein.",
        "invalid_chat": "Sahi format use karein:\n@Dealkoti\nya\nhttps://t.me/Dealkoti",
        "settings": "{task} ke Bot Settings:",
        "locked": "{feature} aapke current plan me locked hai. Use karne ke liye upgrade karein.",
        "faq": "FAQ page {page}/{pages}\n\nQuestion select karein:",
        "faq_answer": "Question:\n{question}\n\nAnswer:\n{answer}",
        "support": "Support ke liye contact karein: {link}",
        "payment_unavailable": "Razorpay abhi configure nahi hai. Support se contact karein.",
        "usdt": "USDT payment manual hai. Proof aur transaction hash support ko bhejein.",
        "upload_prompt": "Forwarding ke liye file bhejein. Ye feature Platinum users ke liye hai.",
        "upload_saved": "File save ho gayi. Task settings me attachment enable kar sakte hain.",
    },
}


def language_for(pref: str | None) -> str:
    return pref if pref in TRANSLATIONS else "en"


def t(lang: str, key: str, **kwargs) -> str:
    text = TRANSLATIONS.get(lang, TRANSLATIONS["en"]).get(key, TRANSLATIONS["en"].get(key, key))
    return text.format(**kwargs) if kwargs else text


def command_help(lang: str) -> str:
    return "\n".join(
        f"{cmd} - {(hinglish if lang == 'hinglish' else english)}"
        for cmd, english, hinglish in USER_COMMANDS
    )


def admin_help() -> str:
    return "\n".join(f"{cmd} - {description}" for cmd, description in ADMIN_COMMANDS)
