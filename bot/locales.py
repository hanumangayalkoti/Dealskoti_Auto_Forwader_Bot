# ==========================================
# COMMAND DEFINITIONS
# ==========================================

# Format: (command, description_en, description_hinglish)
USER_COMMANDS = [
    ("/start", "Start the bot and open main menu", "Bot chalu karein aur menu kholein"),
    ("/menu", "Open the main navigation menu", "Main menu kholein"),
    ("/help", "View available commands and help", "Commands aur help dekhein"),
    ("/connect", "Connect your Telegram account", "Apna Telegram account connect karein"),
    ("/account", "View your account details and limits", "Apne account ki details dekhein"),
    ("/disconnect", "Disconnect your Telegram account safely", "Apna account safely hataein"),
    ("/plans", "View and upgrade your subscription plan", "Plans dekhein aur upgrade karein"),
    ("/tasks", "View and manage your forwarding tasks", "Apne forwarding tasks dekhein"),
    ("/newtask", "Create a new forwarding task", "Naya forwarding task banayein"),
    ("/settings", "Configure task settings", "Task ki settings set karein"),
    ("/updates", "Join the updates/announcements channel", "Updates channel join karein"),
    ("/support", "Contact customer support", "Support team se baat karein"),
    ("/language", "Change your language preference", "Apni bhasha (language) badlein"),
]

# Format: (command, description_en)
ADMIN_COMMANDS = [
    ("/admin", "Open Admin Dashboard"),
    ("/stats", "View bot statistics"),
    ("/broadcast", "Send a message to users"),
    ("/block", "Block a user from using the bot"),
    ("/unblock", "Unblock a restricted user"),
    ("/grantdays", "Grant premium days to a user (optionally a specific plan)"),
    ("/listusers", "List recently registered users"),
    ("/userinfo", "View a user's details"),
    ("/referralpayout", "Process a referral payout"),
]

# ==========================================
# TRANSLATION DICTIONARY
# ==========================================

TRANSLATIONS = {
    "en": {
        "main_menu": "Hi {name},\nWelcome to Dealskoti Auto Forwarder Bot.\nMake auto forwarding so simple.\nOur Bot Gives 100% Security For Account Data.\n\nPlease select an option from the menu below:",
        "help_title": "📚 <b>Help & Commands</b>\n\nHere are the commands you can use:\n{commands}",
        "admin_help_title": "🛠️ <b>Admin Commands</b>\n\n{commands}",
        "admin_only": "⚠️ This command is restricted to administrators only.",
        "support": "📞 <b>Customer Support</b>\n\nIf you need help with payments, tasks, or setup, contact us here:\n{link}\n\n*Our team usually replies within 24 hours.*",
        "choose_language": "🌐 Choose your preferred language:",
        "language_saved": "✅ Language preference saved successfully!",
        "faq_title": "❓ <b>FAQ (Page {page} of {pages})</b>",
        "faq_hint": "👇 Tap a question to see the answer:",
        "faq_answer": "❓ <b>{question}</b>\n\n💡 {answer}",
        "account_details": "👤 <b>My Account</b>\n\nName: {name}\nUsername: {username}\nUser ID: <code>{user_id}</code>\n\n💎 <b>Subscription</b>\nActive Plan: {plan}\nPlan Started: {plan_started}\nPlan Expiry: {expiry}\nLast Transaction ID: <code>{txn_id}</code>\nPayment Status: {payment}\n\n⚙️ <b>Usage & Status</b>\nTelegram Status: {session}\nActive Tasks: {tasks}\nMessages Today: {forwarding}\nChannel Membership: {membership}\nLanguage: {user_language}",
        "login_phone": "📱 <b>Connect Account</b>\n\nPlease send your Telegram phone number with country code.\n\nExample (India): <code>+919876543210</code>\n\n⚠️ Important:\n• Number must start with <code>+</code> and country code\n• NO spaces inside the number\n• Correct: <code>+914527896325</code>\n• Wrong: <code>+91 45278 96325</code>",
        "login_pin": "🔑 <b>Enter OTP/PIN</b>\n\nTelegram has sent a verification code to your Telegram app. Please enter the code below.\n\n⚠️ <b>Important:</b> If your code is <code>12345</code>, please enter it as <code>PIN12345</code>.\n\nThe <code>PIN</code> prefix is required, otherwise your account will not connect.",
        "login_2fa": "🔒 <b>Two-Step Verification (Cloud Password)</b>\n\nYour account has an extra password enabled (this is <b>not</b> your account password).\n\n<b>What to enter:</b>\n• The 'Cloud Password' or 'Two-Step Verification' password you set in Telegram Settings → Privacy → Two-Step Verification.\n• This is <b>different</b> from your SMS login code.\n\n<b>If you forgot it:</b>\n1. Open Telegram on your phone\n2. Go to Settings → Privacy → Two-Step Verification\n3. Tap 'Forgot password?' to reset it\n4. Come back here and use /connect to retry\n\nPlease enter your 2FA password below:",
        "login_success": "✅ <b>Account Connected Successfully!</b>\n\nYour session is securely established. You can now create and manage tasks.",
        "login_cancelled": "↩️ Connect process has been cancelled safely.",
        "login_failed": "⚠️ Login failed or timed out. Please try connecting again.",
        "choose_plan": "💎 <b>Choose a Subscription Plan</b>\n\nSelect a tier below to view limits and features:",
        "plan_details": "💎 <b>{plan} Plan</b>\n\nFeatures & Limits:\n{features}\n\nPrice: ₹{monthly} / month\n\nSelect your billing cycle below:",
        "billing_details": "💳 <b>Checkout Summary</b>\n\nPlan: {plan}\nCycle: {cycle}\nOriginal Price: {original}\nDiscount: {discount}\n\n<b>Payable Amount: {payable}</b>\n\nClick below to securely generate your payment link.",
        "payment_link": "🔗 <b>Payment Link Generated</b>\n\nPlan: {plan} ({cycle})\nPayable: {amount}\n\nClick the button below to pay via Razorpay. Your plan will activate automatically.",
        "payment_success": "🎉 <b>Payment Successful!</b>\n\nPlan: {plan}\nDuration added: {days} days\nAmount Paid: {amount}\nTransaction ID: <code>{txn_id}</code>\nNew Expiry Date: {expiry}\n\nThank you for subscribing to DealsKoti! Your new limits have been applied.",
        "payment_failed": "⚠️ Payment could not be initiated or was cancelled.",
        "tasks_title": "📋 <b>Your Forwarding Tasks:</b>",
        "no_tasks_short": "📋 You haven't created any tasks yet. Click 'Create New Task' to begin.",
        "task_name": "📝 <b>New Task: Name</b>\n\nPlease send a short, recognizable name for this task (e.g., <code>Amazon Deals</code>):",
        "task_source": "📥 <b>New Task: Source Chat</b>\n\nForward a message from the Source chat, or send its Public Username/ID.\n\n*(You can add multiple sources. Send <code>/done</code> when you are finished adding sources).* ",
        "task_destination": "📤 <b>New Task: Destination Chat</b>\n\nForward a message from the Destination chat, or send its Public Username/ID.\n\n*(You can add multiple destinations. Send <code>/done</code> when you are finished).* ",
        "task_created": "✅ Task <b>{task_name}</b> has been created successfully!\n\nYou can now configure its settings or resume it from the Tasks menu.",
        "connect_required": "🔌 <b>Connect your account first</b>\n\nYou need to connect your Telegram account before creating tasks, uploading a file, or buying a plan — even on the Free plan.\n\nTap below to connect:",
        "upload_replace_confirm": "⚠️ You already have a file uploaded: <b>{old_name}</b>\n\nReplace it with <b>{new_name}</b>? You can only have one stored file at a time — the old one will be deleted.",
        "unknown_command": "⚠️ Unknown command. Please send /help to see the list of available commands.",
        # ===== PHASE 2 NEW KEYS =====
        "settings_select_task": "⚙️ <b>Task Settings</b>\n\nSelect a task to configure:",
        "settings_no_tasks": "📋 You don't have any tasks yet. Create one to unlock settings.",
        "settings_main_title": "⚙️ <b>Settings for:</b> {task_name}\n\nChoose a category:",
        "settings_cat_messages": "💬 <b>Message Settings</b>\n\nCustomize the text that is forwarded:",
        "settings_cat_filters": "🔍 <b>Filters</b>\n\nControl which messages get forwarded:",
        "settings_cat_media": "🖼️ <b>Media Settings</b>\n\nControl media handling:",
        "settings_cat_forwarding": "🚀 <b>Forwarding Settings</b>\n\nControl forwarding behavior:",
        "settings_cat_senderfilter": "👤 <b>Sender Filter</b>\n\nAllow only specific senders (Platinum):",
        "feature_locked": "🔒 <b>Feature Locked</b>\n\n<b>{feature}</b> is available on the <b>{required_plan}</b> plan.\n\nUpgrade to unlock this feature.",
        "feature_locked_short": "🔒 Locked — requires {required_plan}",
        "header_prompt": "✏️ Send the new <b>Header</b> text. A short line added to the top of every forwarded message.\n\nSend /clear to remove.\nSend /back to cancel.",
        "footer_prompt": "✏️ Send the new <b>Footer</b> text. A short line added to the bottom of every forwarded message.\n\nSend /clear to remove.\nSend /back to cancel.",
        "replace_prompt": "✏️ Send replacement rules.\n\nFormat: <code>old=new</code>\nExamples:\n• <code>sale=SALE</code>\n• <code>amazon=Amazon</code>\n\nSeparate multiple rules with commas.\nSend /clear to remove all.\nSend /back to cancel.",
        "blacklist_prompt": "🔍 Send words to <b>block</b>. Messages containing these words will be skipped.\n\nFormat: <code>spam, scam, ad</code>\nSend /clear to remove all.\nSend /back to cancel.",
        "whitelist_prompt": "🔍 Send words to <b>require</b>. Only messages containing at least one of these words will be forwarded (leave empty to allow all).\n\nFormat: <code>deal, offer, free</code>\nSend /clear to remove.\nSend /back to cancel.",
        "autodelete_prompt": "🗑️ Send the number of seconds after which forwarded messages should be auto-deleted from the destination.\n\nExample: <code>3600</code> (1 hour)\nSend <code>0</code> or /clear to turn off.\nSend /back to cancel.",
        "userfilter_prompt": "👤 <b>Sender Filter</b> — only these users' messages will be forwarded from the source; everyone else is skipped.\n\nYou can send Telegram user IDs or @usernames:\nFormat: <code>123456789, @dealkoti</code>\n\nTip: To find a user's ID, forward one of their messages to @userinfobot.\nSend /clear to allow everyone.\nSend /back to cancel.",
        "watermark_text_prompt": "💧 Send the watermark text that will be drawn on forwarded images (Platinum-only).\n\nExample: <code>@YourChannel</code>\nSend /clear to reset to default.\nSend /back to cancel.",
        "watermark_on": "✅ Watermark: <b>On</b>",
        "watermark_off": "❌ Watermark: <b>Off</b>",
        "editsync_on": "✅ Edit Sync: <b>On</b>",
        "editsync_off": "❌ Edit Sync: <b>Off</b>",
        "setting_saved": "✅ <b>{feature}</b> saved.",
        "setting_cleared": "✅ <b>{feature}</b> cleared.",
        "setting_invalid_number": "⚠️ Please send a valid number.",
        "setting_invalid_ids": "⚠️ Please send only numeric Telegram IDs separated by commas.",
        "setting_invalid_replace": "⚠️ Invalid format. Use <code>old=new</code> separated by commas.",
        "tasks_summary": "📋 <b>Your Tasks</b>\n\nPlan: {plan}\nCreated: {created}\nRemaining: {remaining}\nTotal allowed: {total}",
        "task_renamed": "✅ Task renamed to <b>{name}</b>.",
        "task_rename_prompt": "✏️ Send a new name for this task (max 120 characters).",
        "rename_empty": "⚠️ Name cannot be empty.",
        "source_list_title": "📥 <b>Select Source Chat</b>\n\nPick a chat from the list, OR forward any message from the desired chat, OR type its public username/ID.\n\nTip: Temporarily pin the desired chat to make it easier to find.",
        "dest_list_title": "📤 <b>Select Destination Chat</b>\n\nPick a chat from the list, OR forward any message from the desired chat, OR type its public username/ID.",
        "chat_page": "Page {page} of {pages}",
        "no_chats_found": "⚠️ No chats found on your account yet. Forward a message or type a public @username.",
        "language_current": "🌐 Current language: <b>{current}</b>",
        "language_confirm_title": "🌐 <b>Confirm Language Change</b>\n\nSwitch to <b>{new}</b>?",
        "language_confirm_yes": "✅ Yes, switch",
        "language_confirm_no": "✖️ Cancel",
        "support_intro": "📞 <b>Support</b>\n\nDon't worry — most issues (payment, setup, task errors) can be solved quickly.\n\nTap the button below to contact our support team. We usually reply within 24 hours.",
        "updates_intro": "📢 <b>Updates Channel</b>\n\nThis channel posts:\n• New features\n• Important changes\n• Announcements for all DealsKoti bots\n\nYou must stay joined to use the bot.",
        "cycle_explainer": "🗓️ <b>Billing Cycles</b>\n\n• <b>Weekly</b>: 7 days\n• <b>Monthly</b>: 30 days\n• <b>Yearly</b>: 365 days with 20% off\n\nFinal payable amount is calculated server-side and shown before payment.",
        "next_scheduled_plan": "📅 <b>Scheduled:</b> {plan} starts on {date}",
        "expired_notice": "⏳ Your {plan} plan expired. Renew from /plans.",
        "downgrade_scheduled_notice": "📅 Your current plan stays active until {date}. Then it will switch to {scheduled}.",
        "validation_invalid": "⚠️ Invalid input. Please try again or send /back.",
        "disconnect_confirm": "⚠️ <b>Disconnect Telegram?</b>\n\nYour session will be removed and tasks will be paused. Your subscription and data are kept safely.",
        "disconnect_done": "✅ Telegram session disconnected.",
        "delete_confirm": "⚠️ Delete task <b>{name}</b> permanently?",
        "broadcast_preview_title": "📣 <b>Broadcast Preview</b>",
        "broadcast_sending": "📣 Sending to {count} users…",
        "broadcast_done": "✅ Broadcast complete\nSent: {sent}\nFailed: {failed}\nBlocked: {blocked}",
        # ===== PHASE 1 NEW KEYS =====
        "already_connected": "ℹ️ You're already connected. Reconnecting replaces your existing session.",
        "reconnect_anyway": "🔄 Reconnect Anyway",
        "invalid_phone": "⚠️ Invalid phone number format.\n\nPlease send your number with country code and NO spaces:\n• India: <code>+919876543210</code>\n• Format: <code>+&lt;country code&gt;&lt;number&gt;</code>\n\nTry again:",
        "picker_bad_number": "⚠️ Please tap one of the buttons above, or reply with a number from the list (for example <code>3</code>).",
        "help_intro": "📖 <b>Help</b> — Try the commands below:\n\n{commands}\n\nTap a command button or send the command to see its features.",
        "help_cat_setting": "General bot settings",
        "help_cat_forwarding": "All forwarding-related settings",
        "help_cat_filters": "Manage filters and replacements",
        "help_cat_media": "Media controls (Platinum only)",
        "help_configure_hint": "Tap a feature to configure it. 🔒 features need a plan upgrade.",
        "back_to_help": "◀️ Back to Help",
        "open_settings_btn": "⚙️ Open Settings",
        "view_plans_btn": "💎 View Plans",
        "refer_intro": "🎁 <b>Refer &amp; Earn</b>\n\nShare your referral link with friends:\n{link}\n\nUsers joined via your link: <b>{count}</b>\n\nReferral rewards are processed by admin via payouts.",
        # ===== PHASE 2-5 NEW KEYS =====
        "tier_limit_reached": "⚠️ You have the <b>{plan}</b> plan — you can select a maximum of <b>{limit}</b> {field}.\n\nUpgrade your plan for more.",
        "invalid_channel_format": "⚠️ Invalid format. Please send the channel in one of these formats:\n• <code>@Dealkoti</code>\n• <code>https://t.me/Dealkoti</code>",
        "picker_title_src": "📥 <b>Select the number below to set your SOURCE</b>\nYou can pick up to {limit}.\n\n💡 Tip: Pin a chat in Telegram so it appears at the top of this list.",
        "picker_title_dst": "📤 <b>Select the number below to set your DESTINATION</b>\nYou can pick up to {limit}.\n\n💡 Tip: Pin a chat in Telegram so it appears at the top of this list.",
        "picker_done": "✅ Done",
        "picker_refresh": "🔄 Refresh List",
        "picker_cancel": "✖️ Cancel",
        "picker_refreshed": "List refreshed",
        "picker_expired": "This list is out of date. Tap 🔄 Refresh List or start again.",
        "picker_limit_reached": "⚠️ You have reached your plan limit of {limit}. Tap ✅ Done to continue, or upgrade with /plans.",
        "picker_already_added": "ℹ️ That chat is already in your selection.",
        "picker_need_source": "⚠️ Pick at least one source chat first — tap a number button above.",
        "picker_need_source_toast": "Pick at least one source first",
        "picker_need_destination": "⚠️ Pick at least one destination chat first — tap a number button above.",
        "picker_need_destination_toast": "Pick at least one destination first",
        "sources_updated": "✅ Sources updated.",
        "destinations_updated": "✅ Destinations updated.",
        "generic_error": "⚠️ Something went wrong. Please try again.",
        "picker_selected_marker": "⬅️ selected",
        "picker_instructions": "👆 Tap a number button below to select or deselect a chat\n✅ Tap <b>Done</b> when finished\n✍️ Not in the list? Forward any message from that chat, or send its @username",
        "picker_empty": "⚠️ No recent chats found. Type the @username manually or forward a message from the chat.",
        "min_one_source": "⚠️ Select at least one source first.",
        "min_one_dest": "⚠️ Select at least one destination first.",
        "settings_cat_channels": "📥 <b>Source/Target Channels</b>\n\nManage this task's channels:",
        "upload_prompt": "📎 <b>Upload File</b>\n\nSend me any file (as document). It will be stored securely and can be attached to your forwarded messages.\n\nMax size: {max} MB\nSend /back to cancel.",
        "upload_success": "✅ File stored: <b>{name}</b> ({size})\n\nThis file will now be attached to every forwarded message automatically (Platinum).",
        "upload_too_big": "⚠️ File is larger than the {max} MB limit.",
        "upload_not_platinum": "🔒 File upload is a Platinum feature. Upgrade to use it.",
        "upload_no_channel": "⚠️ File storage is not configured yet. Contact admin.",
        "usdt_unavailable": "⚠️ USDT payments are currently unavailable. Please pay via Razorpay (INR).",
        "usdt_instructions": "🪙 <b>USDT Payment</b>\n\nPlan: <b>{plan}</b> ({cycle})\nAmount: <b>{amount} USDT</b>\nNetwork: <b>{network}</b>\nWallet:\n<code>{wallet}</code>\n\n1️⃣ Send EXACTLY {amount} USDT ({network}) to the wallet above.\n2️⃣ Tap 'I Have Paid' and paste your Transaction Hash (TXID).\n3️⃣ Admin verifies and your plan activates automatically.",
        "usdt_paid_btn": "✅ I Have Paid — Submit TXID",
        "usdt_txid_prompt": "🧾 Send your USDT Transaction Hash (TXID):\n\nSend /back to cancel.",
        "usdt_submitted": "✅ TXID submitted! Admin will verify it shortly. You'll be notified once approved.",
        "usdt_approved_user": "🎉 Your USDT payment was approved!\n\nPlan: {plan}\nDuration: {days} days\nNew Expiry: {expiry}",
        "usdt_rejected_user": "❌ Your USDT payment could not be verified.\n\nTXID: <code>{txid}</code>\nPlease contact support or submit a correct TXID.",
        "plan_benefits_free": "┌─ 1 Task\n├─ 1 Source + 1 Destination\n├─ 50 msg/day\n├─ Forwarded-from tag visible\n└─ Header/Footer text",
        "plan_benefits_silver": "┌─ Everything in Free\n├─ 3 Tasks\n├─ 5 Sources + 5 Targets per task\n├─ Clean copy (no forwarded-from tag)\n├─ Header control\n├─ Footer text\n├─ 200 msg/day\n└─ No BOT watermark",
        "plan_benefits_gold": "┌─ Everything in Silver\n├─ 5 Tasks\n├─ 10 Sources + 10 Targets per task\n├─ Blacklist keywords (block words)\n├─ Whitelist keywords (allow only these)\n├─ 1000 msg forwards/day\n└─ Instant VIP Support",
        "plan_benefits_platinum": "┌─ Everything in Gold\n├─ 10 Tasks\n├─ 15 Sources + 15 Targets per task\n├─ Unlimited forwards/day\n├─ Replace Usernames / Words / Links\n├─ Image watermark\n├─ Auto Delete Messages ON/OFF\n├─ Sender Filter (IDs & @usernames)\n├─ Live message edit sync from source\n├─ Attach your file to every message (/upload_file)\n└─ Super Fast Message Delivery",
    },
    "hinglish": {
        "main_menu": "Hi {name},\nDealskoti Auto Forwarder Bot me aapka swagat hai.\nAuto forwarding ab bilkul simple hai.\nHamara Bot aapke Account Data ki 100% Security deta hai.\n\nNeeche diye menu se ek option chunein:",
        "help_title": "📚 <b>Help & Commands</b>\n\nYe commands aap use kar sakte hain:\n{commands}",
        "admin_help_title": "🛠️ <b>Admin Commands</b>\n\n{commands}",
        "admin_only": "⚠️ Ye command sirf Admins use kar sakte hain.",
        "support": "📞 <b>Customer Support</b>\n\nAgar aapko payment, tasks, ya setup me madad chahiye, toh yahan message karein:\n{link}\n\n*Humari team aam taur par 24 ghante me reply karti hai.*",
        "choose_language": "🌐 Apni bhasha (language) chunein:",
        "language_saved": "✅ Aapki language save ho gayi hai!",
        "faq_title": "❓ <b>FAQ (Page {page} of {pages})</b>",
        "faq_hint": "👇 Jawab dekhne ke liye sawal par tap karein:",
        "faq_answer": "❓ <b>{question}</b>\n\n💡 {answer}",
        "account_details": "👤 <b>Mera Account</b>\n\nNaam: {name}\nUsername: {username}\nUser ID: <code>{user_id}</code>\n\n💎 <b>Subscription</b>\nActive Plan: {plan}\nPlan Start Hua: {plan_started}\nPlan Expiry: {expiry}\nLast Transaction ID: <code>{txn_id}</code>\nPayment Status: {payment}\n\n⚙️ <b>Usage & Status</b>\nTelegram Status: {session}\nActive Tasks: {tasks}\nAaj ke Messages: {forwarding}\nChannel Membership: {membership}\nLanguage: {user_language}",
        "login_phone": "📱 <b>Connect Account</b>\n\nApna Telegram phone number country code ke sath bhejein.\n\nExample (India): <code>+919876543210</code>\n\n⚠️ Dhyan rakhein:\n• Number <code>+</code> aur country code se shuru ho\n• Number ke beech me KOI SPACE nahi hona chahiye\n• Sahi: <code>+914527896325</code>\n• Galat: <code>+91 45278 96325</code>",
        "login_pin": "🔑 <b>OTP/PIN Daalein</b>\n\nTelegram ne aapke Telegram app par verification code bheja hoga. Kripya neeche code enter karein.\n\n⚠️ <b>Zaroori:</b> Agar aapka code <code>12345</code> hai, toh use <code>PIN12345</code> ke format mein enter karein.\n\n<code>PIN</code> prefix lagana zaroori hai, warna account connect nahi hoga.",
        "login_2fa": "🔒 <b>Two-Step Verification (Cloud Password)</b>\n\nAapke account par ek extra password hai (ye aapke account password se <b>alag</b> hai).\n\n<b>Kya daalna hai:</b>\n• Telegram Settings → Privacy → Two-Step Verification me jo 'Cloud Password' set kiya hai wahi.\n• Ye aapke SMS login code se <b>different</b> hai.\n\n<b>Agar bhool gaye:</b>\n1. Phone me Telegram kholein\n2. Settings → Privacy → Two-Step Verification par jayein\n3. 'Forgot password?' dabayein aur reset karein\n4. Wapas yahan aakar /connect se retry karein\n\nNeeche apna 2FA password daalein:",
        "login_success": "✅ <b>Account Successfully Connect Ho Gaya!</b>\n\nAb aap tasks bana kar auto-forwarding shuru kar sakte hain.",
        "login_cancelled": "↩️ Login process safely cancel kar diya gaya hai.",
        "login_failed": "⚠️ Login fail ho gaya ya time out ho gaya. Kripya wapas try karein.",
        "choose_plan": "💎 <b>Subscription Plan Chunein</b>\n\nLimits aur features dekhne ke liye neeche se ek plan select karein:",
        "plan_details": "💎 <b>{plan} Plan</b>\n\nFeatures aur Limits:\n{features}\n\nPrice: ₹{monthly} / mahina\n\nNeeche se apna cycle (time) chunein:",
        "billing_details": "💳 <b>Checkout Summary</b>\n\nPlan: {plan}\nCycle: {cycle}\nOriginal Price: {original}\nDiscount: {discount}\n\n<b>Payable Amount: {payable}</b>\n\nSecure payment link generate karne ke liye click karein.",
        "payment_link": "🔗 <b>Payment Link Ban Gaya Hai</b>\n\nPlan: {plan} ({cycle})\nAmount: {amount}\n\nRazorpay se payment karne ke liye neeche click karein. Plan apne aap activate ho jayega.",
        "payment_success": "🎉 <b>Payment Successful!</b>\n\nPlan: {plan}\nDays Added: {days} din\nAmount Paid: {amount}\nTransaction ID: <code>{txn_id}</code>\nNayi Expiry: {expiry}\n\nDealsKoti se judne ke liye shukriya! Aapki nayi limits apply ho chuki hain.",
        "payment_failed": "⚠️ Payment initiate nahi ho paya ya cancel ho gaya.",
        "tasks_title": "📋 <b>Aapke Forwarding Tasks:</b>",
        "no_tasks_short": "📋 Abhi tak koi task nahi banaya gaya hai. Naya task banane ke liye 'Create New Task' dabayein.",
        "task_name": "📝 <b>Naya Task: Naam</b>\n\nIs task ko pehchanne ke liye ek chota naam bhejein (jaise: <code>Amazon Deals</code>):",
        "task_source": "📥 <b>Naya Task: Source Chat (Jahan se aayega)</b>\n\nSource chat se koi bhi message yahan forward karein, ya uska Username/ID bhejein.\n\n*(Aap ek se zyada source add kar sakte hain. Kaam poora hone par <code>/done</code> bhejein).* ",
        "task_destination": "📤 <b>Naya Task: Destination Chat (Jahan bhejnai hai)</b>\n\nDestination chat se koi bhi message yahan forward karein, ya uska Username/ID bhejein.\n\n*(Aap ek se zyada destination add kar sakte hain. Kaam poora hone par <code>/done</code> bhejein).* ",
        "task_created": "✅ Task <b>{task_name}</b> successfully ban gaya hai!\n\nAb aap Tasks menu se iski settings set kar sakte hain ya isko Resume kar sakte hain.",
        "connect_required": "🔌 <b>Pehle account connect karein</b>\n\nTask banane, file upload karne, ya plan kharidne se pehle apna Telegram account connect karna zaroori hai — Free plan me bhi.\n\nNeeche tap karke connect karein:",
        "upload_replace_confirm": "⚠️ Aapki ek file already uploaded hai: <b>{old_name}</b>\n\nIse <b>{new_name}</b> se replace karein? Ek time me sirf ek hi file rakh sakte ho — purani file delete ho jayegi.",
        "unknown_command": "⚠️ Galat command. Sahi commands ki list dekhne ke liye /help bhejein.",
        # ===== PHASE 2 NEW KEYS =====
        "settings_select_task": "⚙️ <b>Task Settings</b>\n\nSettings karne ke liye task chunein:",
        "settings_no_tasks": "📋 Abhi koi task nahi hai. Settings unlock karne ke liye pehle ek task banayein.",
        "settings_main_title": "⚙️ <b>Settings:</b> {task_name}\n\nCategory chunein:",
        "settings_cat_messages": "💬 <b>Message Settings</b>\n\nForward hone wale message ka text customize karein:",
        "settings_cat_filters": "🔍 <b>Filters</b>\n\nKun messages forward honge ye control karein:",
        "settings_cat_media": "🖼️ <b>Media Settings</b>\n\nMedia handling control karein:",
        "settings_cat_forwarding": "🚀 <b>Forwarding Settings</b>\n\nForwarding ka behavior control karein:",
        "settings_cat_senderfilter": "👤 <b>Sender Filter</b>\n\nSirf kuch senders ko allow karein (Platinum):",
        "feature_locked": "🔒 <b>Feature Locked</b>\n\n<b>{feature}</b> sirf <b>{required_plan}</b> plan pe available hai.\n\nUnlock karne ke liye upgrade karein.",
        "feature_locked_short": "🔒 Locked — {required_plan} chahiye",
        "header_prompt": "✏️ Naya <b>Header</b> text bhejein. Ye har forwarded message ke upar add hoga.\n\nHatane ke liye /clear.\nCancel ke liye /back.",
        "footer_prompt": "✏️ Naya <b>Footer</b> text bhejein. Ye har forwarded message ke neeche add hoga.\n\nHatane ke liye /clear.\nCancel ke liye /back.",
        "replace_prompt": "✏️ Replacement rules bhejein.\n\nFormat: <code>purana=naya</code>\nExamples:\n• <code>sale=SALE</code>\n• <code>amazon=Amazon</code>\n\nMultiple rules ke liye comma lagayein.\nSab hatane ke liye /clear.\nCancel ke liye /back.",
        "blacklist_prompt": "🔍 Words bhejein jo <b>block</b> karne hain. In words wale messages skip ho jayenge.\n\nFormat: <code>spam, scam, ad</code>\nSab hatane ke liye /clear.\nCancel ke liye /back.",
        "whitelist_prompt": "🔍 Words bhejein jo <b>required</b> hain. Sirf in me se koi ek word hone wale messages forward honge (khali chhodein toh sab allowed).\n\nFormat: <code>deal, offer, free</code>\nHatane ke liye /clear.\nCancel ke liye /back.",
        "autodelete_prompt": "🗑️ Seconds ki sankhya bhejein. Utne seconds baad message destination se auto-delete ho jayega.\n\nExample: <code>3600</code> (1 ghanta)\nBand karne ke liye <code>0</code> ya /clear.\nCancel ke liye /back.",
        "userfilter_prompt": "👤 <b>Sender Filter</b> — sirf in users ke messages hi source se forward honge; baaki sab skip ho jayenge.\n\nAap Telegram user IDs ya @usernames bhej sakte ho:\nFormat: <code>123456789, @dealkoti</code>\n\nTip: Kisi user ki ID jaanne ke liye uska message @userinfobot ko forward karo.\nSab allow karne ke liye /clear.\nCancel ke liye /back.",
        "watermark_text_prompt": "💧 Watermark text bhejein jo forwarded images ke neeche draw hoga (sirf Platinum).\n\nExample: <code>@AapkaChannel</code>\nDefault par reset karne ke liye /clear.\nCancel ke liye /back.",
        "watermark_on": "✅ Watermark: <b>On</b>",
        "watermark_off": "❌ Watermark: <b>Off</b>",
        "editsync_on": "✅ Edit Sync: <b>On</b>",
        "editsync_off": "❌ Edit Sync: <b>Off</b>",
        "setting_saved": "✅ <b>{feature}</b> save ho gaya.",
        "setting_cleared": "✅ <b>{feature}</b> hata diya gaya.",
        "setting_invalid_number": "⚠️ Kripya ek valid number bhejein.",
        "setting_invalid_ids": "⚠️ Kripya sirf numeric Telegram IDs comma se alag karke bhejein.",
        "setting_invalid_replace": "⚠️ Galat format. <code>purana=naya</code> comma se alag karke bhejein.",
        "tasks_summary": "📋 <b>Aapke Tasks</b>\n\nPlan: {plan}\nBanaye: {created}\nBache: {remaining}\nTotal allowed: {total}",
        "task_renamed": "✅ Task ka naam <b>{name}</b> ho gaya.",
        "task_rename_prompt": "✏️ Task ke liye naya naam bhejein (max 120 characters).",
        "rename_empty": "⚠️ Naam khali nahi ho sakta.",
        "source_list_title": "📥 <b>Source Chat Chunein</b>\n\nList se chat pick karein, ya desired chat se koi bhi message forward karein, ya public username/ID type karein.\n\nTip: Chat ko temporarily pin kar lein taaki asani se mile.",
        "dest_list_title": "📤 <b>Destination Chat Chunein</b>\n\nList se chat pick karein, ya desired chat se koi bhi message forward karein, ya public username/ID type karein.",
        "chat_page": "Page {page} / {pages}",
        "no_chats_found": "⚠️ Aapke account par abhi koi chat nahi mili. Koi message forward karein ya public @username type karein.",
        "language_current": "🌐 Abhi ki bhasha: <b>{current}</b>",
        "language_confirm_title": "🌐 <b>Language Badlein?</b>\n\n<b>{new}</b> par switch karein?",
        "language_confirm_yes": "✅ Haan, badlein",
        "language_confirm_no": "✖️ Cancel",
        "support_intro": "📞 <b>Support</b>\n\nChinta na karein — zyada tar problems (payment, setup, task errors) jaldi solve ho jati hain.\n\nNeeche button dabakar support team se baat karein. Hum 24 ghante me reply karte hain.",
        "updates_intro": "📢 <b>Updates Channel</b>\n\nIs channel par milta hai:\n• Naye features\n• Important updates\n• Saare DealsKoti bots ki announcements\n\nBot use karne ke liye joined rehna zaroori hai.",
        "cycle_explainer": "🗓️ <b>Billing Cycles</b>\n\n• <b>Weekly</b>: 7 din\n• <b>Monthly</b>: 30 din\n• <b>Yearly</b>: 365 din aur 20% off\n\nFinal payable amount server-side calculate hoke payment se pehle dikhaya jata hai.",
        "next_scheduled_plan": "📅 <b>Scheduled:</b> {plan} {date} se shuru",
        "expired_notice": "⏳ Aapka {plan} plan expire ho gaya. /plans se renew karein.",
        "downgrade_scheduled_notice": "📅 Aapka current plan {date} tak active rahega. Phir {scheduled} par switch hoga.",
        "validation_invalid": "⚠️ Galat input. Kripya dobara try karein ya /back bhejein.",
        "disconnect_confirm": "⚠️ <b>Telegram Disconnect Karein?</b>\n\nAapka session remove ho jayega aur tasks pause ho jayenge. Subscription aur data safe hain.",
        "disconnect_done": "✅ Telegram session disconnect ho gaya.",
        "delete_confirm": "⚠️ Task <b>{name}</b> hamesha ke liye delete karein?",
        "broadcast_preview_title": "📣 <b>Broadcast Preview</b>",
        "broadcast_sending": "📣 {count} users ko bhej raha hai…",
        "broadcast_done": "✅ Broadcast complete\nSent: {sent}\nFailed: {failed}\nBlocked: {blocked}",
        # ===== PHASE 1 NEW KEYS =====
        "already_connected": "ℹ️ Aap already connected ho. Dobara connect karne se purana session replace ho jayega.",
        "reconnect_anyway": "🔄 Phir Se Connect Karo",
        "invalid_phone": "⚠️ Phone number ka format galat hai.\n\nCountry code ke sath, bina space number bhejein:\n• India: <code>+919876543210</code>\n• Format: <code>+&lt;country code&gt;&lt;number&gt;</code>\n\nDobara try karein:",
        "picker_bad_number": "⚠️ Upar diye gaye buttons me se koi tap karein, ya list me se number bhejein (jaise <code>3</code>).",
        "help_intro": "📖 <b>Help</b> — Neeche di gayi commands try karo:\n\n{commands}\n\nKisi command par tap karo ya command bhejo, uske features dikhenge.",
        "help_cat_setting": "General bot settings",
        "help_cat_forwarding": "Forwarding se related sab settings",
        "help_cat_filters": "Filters aur replacements manage karo",
        "help_cat_media": "Media controls (sirf Platinum)",
        "help_configure_hint": "Feature configure karne ke liye tap karo. 🔒 features ke liye upgrade karna hoga.",
        "back_to_help": "◀️ Help Par Wapas",
        "open_settings_btn": "⚙️ Settings Kholein",
        "view_plans_btn": "💎 Plans Dekhein",
        "refer_intro": "🎁 <b>Refer &amp; Earn</b>\n\nApna referral link dosto ke sath share karo:\n{link}\n\nAapke link se jude users: <b>{count}</b>\n\nReferral rewards admin payout se process hote hain.",
        # ===== PHASE 2-5 NEW KEYS =====
        "tier_limit_reached": "⚠️ Aapke paas <b>{plan}</b> plan hai — aap maximum <b>{limit}</b> {field} hi select kar sakte ho.\n\nZyada ke liye plan upgrade karo.",
        "invalid_channel_format": "⚠️ Format galat hai. Channel in formats me bhejein:\n• <code>@Dealkoti</code>\n• <code>https://t.me/Dealkoti</code>",
        "picker_title_src": "📥 <b>Neeche se number chuno — ye aapka SOURCE banega</b>\nMax {limit} select kar sakte ho.\n\n💡 Tip: Telegram me chat ko pin karo taaki wo list me sabse upar dikhe.",
        "picker_title_dst": "📤 <b>Neeche se number chuno — ye aapka DESTINATION banega</b>\nMax {limit} select kar sakte ho.\n\n💡 Tip: Telegram me chat ko pin karo taaki wo list me sabse upar dikhe.",
        "picker_done": "✅ Done",
        "picker_refresh": "🔄 List Refresh Karo",
        "picker_cancel": "✖️ Cancel",
        "picker_refreshed": "List refresh ho gayi",
        "picker_expired": "Ye list purani ho gayi hai. 🔄 List Refresh Karo dabao ya dobara shuru karo.",
        "picker_limit_reached": "⚠️ Aapke plan ki limit {limit} poori ho gayi. Aage badhne ke liye ✅ Done dabao, ya /plans se upgrade karo.",
        "picker_already_added": "ℹ️ Ye chat pehle se selected hai.",
        "picker_need_source": "⚠️ Pehle kam se kam ek source chat chuno — upar number button par tap karo.",
        "picker_need_source_toast": "Pehle ek source chuno",
        "picker_need_destination": "⚠️ Pehle kam se kam ek destination chat chuno — upar number button par tap karo.",
        "picker_need_destination_toast": "Pehle ek destination chuno",
        "sources_updated": "✅ Sources update ho gaye.",
        "destinations_updated": "✅ Destinations update ho gaye.",
        "generic_error": "⚠️ Kuch galat ho gaya. Dobara try karein.",
        "picker_selected_marker": "⬅️ selected",
        "picker_instructions": "👆 Neeche number button par tap karke chat select/deselect karo\n✅ Ho jaye to <b>Done</b> dabao\n✍️ List me nahi hai? Us chat se koi message forward karo, ya uska @username bhejo",
        "picker_empty": "⚠️ Koi recent chat nahi mila. @username manually type karo ya chat se koi message forward karo.",
        "min_one_source": "⚠️ Pehle kam se kam ek source select karo.",
        "min_one_dest": "⚠️ Pehle kam se kam ek destination select karo.",
        "settings_cat_channels": "📥 <b>Source/Target Channels</b>\n\nIs task ke channels manage karo:",
        "upload_prompt": "📎 <b>File Upload Karo</b>\n\nMujhe koi bhi file bhejo (document ki tarah). Ye safely store hogi aur aapke forwarded messages me attach ho sakti hai.\n\nMax size: {max} MB\nCancel karne ke liye /back bhejein.",
        "upload_success": "✅ File store ho gayi: <b>{name}</b> ({size})\n\nAb ye file har forwarded message ke sath apne aap attach hogi (Platinum).",
        "upload_too_big": "⚠️ File {max} MB limit se badi hai.",
        "upload_not_platinum": "🔒 File upload Platinum feature hai. Use karne ke liye upgrade karo.",
        "upload_no_channel": "⚠️ File storage abhi configure nahi hua hai. Admin se baat karein.",
        "usdt_unavailable": "⚠️ USDT payment abhi available nahi hai. Kripya Razorpay (INR) se pay karein.",
        "usdt_instructions": "🪙 <b>USDT Payment</b>\n\nPlan: <b>{plan}</b> ({cycle})\nAmount: <b>{amount} USDT</b>\nNetwork: <b>{network}</b>\nWallet:\n<code>{wallet}</code>\n\n1️⃣ Upar diye wallet me EXACTLY {amount} USDT ({network}) bhejein.\n2️⃣ 'I Have Paid' dabakar apna Transaction Hash (TXID) paste karein.\n3️⃣ Admin verify karega aur plan apne aap activate ho jayega.",
        "usdt_paid_btn": "✅ Pay Kar Diya — TXID Bhejo",
        "usdt_txid_prompt": "🧾 Apna USDT Transaction Hash (TXID) bhejein:\n\nCancel karne ke liye /back bhejein.",
        "usdt_submitted": "✅ TXID submit ho gaya! Admin jaldi verify karega. Approve hote hi notification milega.",
        "usdt_approved_user": "🎉 Aapka USDT payment approve ho gaya!\n\nPlan: {plan}\nDuration: {days} din\nNayi Expiry: {expiry}",
        "usdt_rejected_user": "❌ Aapka USDT payment verify nahi ho paya.\n\nTXID: <code>{txid}</code>\nKripya support se baat karein ya sahi TXID bhejein.",
        "plan_benefits_free": "┌─ 1 Task\n├─ 1 Source + 1 Destination\n├─ 50 msg/day\n├─ Forwarded-from tag dikhega\n└─ Header/Footer text",
        "plan_benefits_silver": "┌─ Free ka sab kuch\n├─ 3 Tasks\n├─ 5 Sources + 5 Targets per task\n├─ Clean copy (forwarded-from tag nahi)\n├─ Header control\n├─ Footer text\n├─ 200 msg/day\n└─ No BOT watermark",
        "plan_benefits_gold": "┌─ Silver ka sab kuch\n├─ 5 Tasks\n├─ 10 Sources + 10 Targets per task\n├─ Blacklist keywords (words block)\n├─ Whitelist keywords (sirf ye allow)\n├─ 1000 msg forwards/day\n└─ Instant VIP Support",
        "plan_benefits_platinum": "┌─ Gold ka sab kuch\n├─ 10 Tasks\n├─ 15 Sources + 15 Targets per task\n├─ Unlimited forwards/day\n├─ Replace Usernames / Words / Links\n├─ Image watermark\n├─ Auto Delete Messages ON/OFF\n├─ Sender Filter (IDs & @usernames)\n├─ Live message edit sync from source\n├─ Apni file har message ke sath attach (/upload_file)\n└─ Super Fast Message Delivery",
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


class _LenientDict(dict):
    """Leaves unknown placeholders visible instead of raising, so one forgotten
    format argument can never turn a whole screen into an error message."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def t(lang: str, key: str, **kwargs) -> str:
    """Retrieves and formats a translated string.

    Falls back to English when a key is only present in one language, and never
    raises on formatting problems — a partially formatted string is far better
    than an exception mid-conversation.
    """
    table = TRANSLATIONS.get(language_for(lang), TRANSLATIONS["en"])
    text = table.get(key)
    if text is None:
        text = TRANSLATIONS["en"].get(key)
    if text is None:
        raise KeyError(key)
    if not kwargs:
        return text
    try:
        return text.format_map(_LenientDict(kwargs))
    except (IndexError, ValueError, TypeError, AttributeError):
        # Unbalanced braces in the source string — show it unformatted.
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
