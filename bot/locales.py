"""
All user-facing text for the DealsKoti forwarder bot.

Two languages: "en" and "hinglish". `t()` falls back to English whenever a key
is missing from Hinglish, so adding an English-only key never crashes a screen.

RULES when adding text:
  * Only HTML parse mode is used — escape any literal < > & as &lt; &gt; &amp;
  * Every placeholder must be {named}, never positional {}
  * Keep both language tables in sync; if you cannot translate, omit the key
    from "hinglish" and the English one is used automatically.
"""

# ==========================================
# COMMAND DEFINITIONS
# ==========================================

# Format: (command, description_en, description_hinglish)
# Telegram only allows a-z, 0-9 and _ in the command NAME, but the description
# accepts emoji — which is what actually shows in the "/" menu, so that is
# where they go.
USER_COMMANDS = [
    ("/start", "🚀 Start the bot and open main menu", "🚀 Bot chalu karein aur menu kholein"),
    ("/menu", "🏠 Open the main navigation menu", "🏠 Main menu kholein"),
    ("/connect", "🔌 Connect your Telegram account", "🔌 Apna Telegram account connect karein"),
    ("/newtask", "➕ Create a new forwarding task", "➕ Naya forwarding task banayein"),
    ("/tasks", "📋 View and manage your forwarding tasks", "📋 Apne forwarding tasks dekhein"),
    ("/settings", "⚙️ Configure task settings", "⚙️ Task ki settings set karein"),
    ("/config", "🛠️ Check your configuration settings", "🛠️ Apni configuration settings dekhein"),
    ("/mystats", "📊 See how many messages were forwarded", "📊 Kitne messages forward hue dekhein"),
    ("/plans", "💎 View and upgrade your subscription plan", "💎 Plans dekhein aur upgrade karein"),
    ("/account", "👤 View your account details and limits", "👤 Apne account ki details dekhein"),
    ("/refer", "🎁 Refer friends and earn commission", "🎁 Dosto ko refer karke kamayein"),
    ("/disconnect", "🔒 Disconnect your Telegram account safely", "🔒 Apna account safely hataein"),
    ("/language", "🌐 Change your language preference", "🌐 Apni bhasha (language) badlein"),
    ("/updates", "📢 Join the updates/announcements channel", "📢 Updates channel join karein"),
    ("/support", "📞 Contact customer support", "📞 Support team se baat karein"),
    ("/help", "❓ View available commands and help", "❓ Commands aur help dekhein"),
]

# Format: (command, description_en)
ADMIN_COMMANDS = [
    ("/admin", "🛠️ Open Admin Dashboard"),
    ("/stats", "📊 View bot statistics"),
    ("/pending", "🧾 Review pending USDT / Stars payments"),
    ("/payouts", "💰 Review and pay referral commissions"),
    ("/grantdays", "🎁 Grant premium days to a user"),
    ("/userinfo", "👤 View a user's details"),
    ("/broadcast", "📣 Send a message to users"),
    ("/block", "⛔ Block a user from using the bot"),
    ("/unblock", "✅ Unblock a restricted user"),
    ("/withdrawals", "💸 Review pending payout requests"),
    ("/backup", "💾 Create a database backup now"),
    ("/restore", "♻️ Restore the database from a backup"),
]

# ==========================================
# TRANSLATION DICTIONARY
# ==========================================

TRANSLATIONS = {
    "en": {
        # ---------- CORE / MENU ----------
        "main_menu": "Hi {name},\nWelcome to Dealskoti Auto Forwarder Bot.\nMake auto forwarding so simple.\nOur Bot Gives 100% Security For Account Data.\n\nPlease select an option from the menu below:",
        "help_title": "📚 <b>Help &amp; Commands</b>\n\nHere are the commands you can use:\n{commands}",
        "admin_help_title": "🛠️ <b>Admin Commands</b>\n\n{commands}",
        "admin_only": "⚠️ This command is restricted to administrators only.",
        "support": "📞 <b>Customer Support</b>\n\nIf you need help with payments, tasks, or setup, contact us here:\n{link}\n\nOur team usually replies within 24 hours.",
        "choose_language": "🌐 Choose your preferred language:",
        "language_saved": "✅ Language preference saved successfully!",
        "faq_title": "❓ <b>FAQ (Page {page} of {pages})</b>",
        "faq_hint": "👇 Tap a question to see the answer:",
        "faq_answer": "❓ <b>{question}</b>\n\n💡 {answer}",
        "unknown_command": "⚠️ Unknown command. Please send /help to see the list of available commands.",
        "generic_error": "⚠️ Something went wrong. Please try again.",
        "validation_invalid": "⚠️ Invalid input. Please try again or send /back.",

        # ---------- ACCOUNT ----------
        "account_details": "👤 <b>My Account</b>\n\nName: {name}\nUsername: {username}\nUser ID: <code>{user_id}</code>\n\n💎 <b>Subscription</b>\nActive Plan: {plan}\nPlan Started: {plan_started}\nPlan Expiry: {expiry}\nLast Transaction ID: <code>{txn_id}</code>\nPayment Status: {payment}\n\n⚙️ <b>Usage &amp; Status</b>\nTelegram Status: {session}\nActive Tasks: {tasks}\nMessages Today: {forwarding}\nChannel Membership: {membership}\nLanguage: {user_language}",

        # ---------- LOGIN FLOW ----------
        "login_phone": "📱 <b>Connect Account</b>\n\nPlease send your Telegram phone number with country code.\n\nExample (India): <code>+919876543210</code>\n\n⚠️ Important:\n• Number must start with <code>+</code> and country code\n• NO spaces inside the number\n• Correct: <code>+914527896325</code>\n• Wrong: <code>+91 45278 96325</code>",
        "login_pin": "🔑 <b>Enter OTP/PIN</b>\n\nTelegram has sent a verification code to your Telegram app. Please enter the code below.\n\n⚠️ <b>Important:</b> If your code is <code>12345</code>, please enter it as <code>PIN12345</code>.\n\nThe <code>PIN</code> prefix is required, otherwise your account will not connect.",
        "login_2fa": "🔒 <b>Two-Step Verification (Cloud Password)</b>\n\nYour account has an extra password enabled (this is <b>not</b> your account password).\n\n<b>What to enter:</b>\n• The 'Cloud Password' you set in Telegram Settings → Privacy → Two-Step Verification.\n• This is <b>different</b> from your SMS login code.\n\n<b>If you forgot it:</b>\n1. Open Telegram on your phone\n2. Go to Settings → Privacy → Two-Step Verification\n3. Tap 'Forgot password?' to reset it\n4. Come back here and use /connect to retry\n\n🔐 Your password is deleted from this chat the instant you send it.\n\nPlease enter your 2FA password below:",
        "login_congrats": "🎉 <b>Congrats {name},</b>\n<b>Your Account is Connected Successfully!</b>\n\n📱 Phone: <code>{phone}</code>\n👤 Logged In as - {username}\n\n📋 Use /tasks to create tasks!\n💎 Use /plans to upgrade!",
        "login_success": "✅ <b>Account Connected Successfully!</b>\n\nYour session is securely established. You can now create and manage tasks.",
        "login_cancelled": "↩️ Connect process has been cancelled safely.",
        "login_failed": "⚠️ Login failed or timed out. Please try connecting again.",
        "already_connected": "ℹ️ You're already connected. Reconnecting replaces your existing session.",
        "reconnect_anyway": "🔄 Reconnect Anyway",
        "invalid_phone": "⚠️ Invalid phone number format.\n\nPlease send your number with country code and NO spaces:\n• India: <code>+919876543210</code>\n• Format: <code>+&lt;country code&gt;&lt;number&gt;</code>\n\nTry again:",
        "connect_required": "🔌 <b>Connect your account first</b>\n\nYou need to connect your Telegram account before creating tasks, uploading a file, or buying a plan — even on the Free plan.\n\nTap below to connect:",
        "disconnect_confirm": "⚠️ <b>Disconnect Telegram?</b>\n\nYour session will be removed and tasks will be paused. Your subscription and data are kept safely.",
        "disconnect_done": "✅ Telegram session disconnected.",

        # ---------- PLANS & BILLING ----------
        "choose_plan": "💎 <b>Choose Your Plan</b>\n\nSelect a plan below to see its full price and feature list:",
        "plan_details": "{details}\n\n👇 Select your billing cycle below:",
        "plan_details_free": "{details}",
        "billing_details": "💳 <b>Checkout Summary</b>\n\nPlan: {plan}\nCycle: {cycle}\nOriginal Price: {original}\nDiscount: {discount}\n\n<b>Payable Amount: {payable}</b>\n\n💷 Pay with UPI/Card — instant activation\n🪙 Pay with USDT — admin verifies\n⭐ Pay with Telegram Stars — admin verifies\n\nChoose your payment method below:",
        "payment_link": "🔗 <b>Payment Link Generated</b>\n\nPlan: {plan} ({cycle})\nPayable: {amount}\n\nTap the button below to pay via Razorpay. Your plan will activate automatically.",
        "payment_success": "🎉 <b>Payment Successful!</b>\n\nPlan: {plan}\nDuration added: {days} days\nAmount Paid: {amount}\nTransaction ID: <code>{txn_id}</code>\nNew Expiry Date: {expiry}\n\nThank you for subscribing to DealsKoti! Your new limits have been applied.",
        "payment_failed": "⚠️ Payment could not be initiated or was cancelled.",
        "cycle_explainer": "🗓️ <b>Billing Cycles</b>\n\n• <b>Weekly</b>: 7 days\n• <b>Monthly</b>: 30 days\n• <b>Yearly</b>: 365 days with 20% off\n\nThe final payable amount is calculated server-side and shown before payment.",
        "next_scheduled_plan": "📅 <b>Scheduled:</b> {plan} starts on {date}",
        "expired_notice": "⏳ Your {plan} plan expired. Renew from /plans.",
        "downgrade_scheduled_notice": "📅 Your current plan stays active until {date}. Then it will switch to {scheduled}.",

        # ---------- USDT (ADMIN-VERIFIED) ----------
        "usdt_unavailable": "⚠️ USDT payments are currently unavailable. Please pay via Razorpay (INR).",
        "usdt_instructions": "🪙 <b>USDT Payment</b>\n\nPlan: <b>{plan}</b> ({cycle})\nAmount: <b>{amount} USDT</b>\nNetwork: <b>{network}</b>\nWallet:\n<code>{wallet}</code>\n\n1️⃣ Send EXACTLY {amount} USDT ({network}) to the wallet above.\n2️⃣ Tap 'I Have Paid' and paste your Transaction Hash (TXID).\n3️⃣ Admin verifies and your plan activates automatically.",
        "usdt_paid_btn": "✅ I Have Paid — Submit TXID",
        "usdt_txid_prompt": "🧾 Send your USDT Transaction Hash (TXID):\n\nSend /back to cancel.",
        "usdt_submitted": "✅ TXID submitted! Admin will verify it shortly. You'll be notified once approved.",
        "usdt_approved_user": "🎉 Your USDT payment was approved!\n\nPlan: {plan}\nDuration: {days} days\nNew Expiry: {expiry}",
        "usdt_rejected_user": "❌ Your USDT payment could not be verified.\n\nTXID: <code>{txid}</code>\nPlease contact support or submit a correct TXID.",

        # ---------- TELEGRAM STARS (ADMIN-VERIFIED) ----------
        "stars_unavailable": "⚠️ Telegram Stars payment is not available for this plan. Please use UPI/Card or USDT.",
        "stars_instructions": "⭐ <b>Telegram Stars Payment</b>\n\nPlan: <b>{plan}</b> ({cycle})\nAmount: <b>{amount} Stars</b>\n\n1️⃣ Send <b>{amount} Stars</b> to {receiver}\n2️⃣ Tap 'I Have Paid' below and send a screenshot or the transaction ID.\n3️⃣ Admin verifies it and your plan activates automatically.\n\n💡 You can buy Stars inside Telegram: Settings → My Stars → Buy More Stars.",
        "stars_paid_btn": "✅ I Have Paid — Submit Proof",
        "stars_proof_prompt": "🧾 Send your Stars payment proof.\n\nYou can send a <b>screenshot</b> of the transaction, or paste the transaction ID as text.\n\nSend /back to cancel.",
        "stars_submitted": "✅ Proof submitted! Admin will verify it shortly. You'll be notified once approved.",
        "stars_approved_user": "🎉 Your Stars payment was approved!\n\nPlan: {plan}\nDuration: {days} days\nNew Expiry: {expiry}",
        "stars_rejected_user": "❌ Your Stars payment could not be verified.\n\nReference: <code>{ref}</code>\nPlease contact support or submit correct proof.",
        "stars_receiver_missing": "⚠️ Stars payments are not configured yet. Please contact support.",

        # ---------- ADMIN PAYMENT REVIEW ----------
        "admin_payment_review": "🧾 <b>Manual Payment Review</b>\n\nMethod: <b>{method}</b>\nUser: {name} (<code>{user_id}</code>)\nPlan: <b>{plan}</b> ({cycle})\nAmount: <b>{amount}</b>\nReference:\n<code>{ref}</code>\n\nApprove to activate this plan immediately.",
        "admin_no_pending": "✅ No pending manual payments right now.",
        "admin_payment_approved": "✅ Approved — {plan} activated for <code>{user_id}</code>.",
        "admin_payment_rejected": "❌ Rejected — user has been notified.",
        "admin_payment_gone": "⚠️ This payment was already handled.",

        # ---------- TASKS ----------
        "tasks_title": "📋 <b>Your Forwarding Tasks:</b>",
        "no_tasks_short": "📋 You haven't created any tasks yet. Tap 'Create New Task' to begin.",
        "tasks_summary": "📋 <b>Your Tasks</b>\n\nPlan: {plan}\nCreated: {created}\nRemaining: {remaining}\nTotal allowed: {total}",
        "task_name": "📝 <b>New Task: Name</b>\n\nSend a short, recognizable name for this task (e.g. <code>Amazon Deals</code>):",
        "task_source": "📥 <b>New Task: Source Chat</b>\n\nForward a message from the Source chat, or send its public username/ID.\n\nYou can add multiple sources. Send <code>/done</code> when finished.",
        "task_destination": "📤 <b>New Task: Destination Chat</b>\n\nForward a message from the Destination chat, or send its public username/ID.\n\nYou can add multiple destinations. Send <code>/done</code> when finished.",
        "task_created": "✅ Task <b>{task_name}</b> has been created successfully!\n\nYou can now configure its settings or resume it from the Tasks menu.",
        "task_creation_reminder": "👋 <b>Create your forwarding task</b>\n\nYou started the bot but haven't created a task yet. Create one and start forwarding.\n\nIf anything is unclear, ask the Support bot: {support}",
        "task_renamed": "✅ Task renamed to <b>{name}</b>.",
        "task_rename_prompt": "✏️ Send a new name for this task (max 120 characters).",
        "rename_empty": "⚠️ Name cannot be empty.",
        "delete_confirm": "⚠️ Delete task <b>{name}</b> permanently?",

        # ---------- CHAT PICKER ----------
        "source_list_title": "📥 <b>Select Source Chat</b>\n\nPick a chat from the list, OR forward any message from the desired chat, OR type its public username/ID.\n\nTip: Temporarily pin the desired chat to make it easier to find.",
        "dest_list_title": "📤 <b>Select Destination Chat</b>\n\nPick a chat from the list, OR forward any message from the desired chat, OR type its public username/ID.",
        "chat_page": "Page {page} of {pages}",
        "no_chats_found": "⚠️ No chats found on your account yet. Forward a message or type a public @username.",
        "picker_bad_number": "⚠️ Please tap one of the buttons above, or reply with a number from the list (for example <code>3</code>).",
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
        "picker_selected_marker": "⬅️ selected",
        "picker_instructions": "👆 Tap a number button below to select or deselect a chat\n✅ Tap <b>Done</b> when finished\n✍️ Not in the list? Forward any message from that chat, or send its @username",
        "picker_empty": "⚠️ No recent chats found. Type the @username manually or forward a message from the chat.",
        "sources_updated": "✅ Sources updated.",
        "destinations_updated": "✅ Destinations updated.",
        "min_one_source": "⚠️ Select at least one source first.",
        "min_one_dest": "⚠️ Select at least one destination first.",
        "tier_limit_reached": "⚠️ You have the <b>{plan}</b> plan — you can select a maximum of <b>{limit}</b> {field}.\n\nUpgrade your plan for more.",
        "invalid_channel_format": "⚠️ Invalid format. Please send the channel in one of these formats:\n• <code>@Dealkoti</code>\n• <code>https://t.me/Dealkoti</code>",

        # ---------- SETTINGS: NAVIGATION ----------
        "flow_cancelled": "↩️ <b>Cancelled: {flow}</b>\n\n{hint}\nThat step has been cancelled — carrying on with your new command below 👇",
        "home_first_time": "🎉 <b>Welcome to DealsKoti Auto Forwarder!</b>\n\nCopy posts from any channel to yours — automatically, 24/7.",
        "home_not_connected": "🏠 <b>Main Menu</b>\n\n🔌 Account: <b>not connected</b>\n\nConnect your Telegram account to create tasks and start forwarding.",
        "home_no_tasks": "🏠 <b>Main Menu</b>\n\n👤 {who}\n💎 Plan: <b>{plan}</b>\n📋 Tasks: none yet\n\n👉 Create your first task to start forwarding.",
        "home_ready": "🏠 <b>Main Menu</b>\n\n👤 {who}\n💎 Plan: <b>{plan}</b>\n📋 Tasks: {tasks}\n📊 Today: {today} / {cap} forwarded\n🕐 Last forward: {last}\n\nWhat would you like to do?",
        "limit_reached_notice": "⚠️ <b>Daily Limit Reached</b>\n\nYou've used all <b>{cap}</b> messages for today.\n\n🕛 Resets at midnight\n💎 Upgrade for a higher limit",
        "destination_failed": "⚠️ <b>Forwarding Problem</b>\n\nTask: <b>{task}</b>\nCouldn't send to: <b>{dest}</b>\n\nPossible reasons:\n• Your account was removed from that channel\n• The channel was deleted\n• You don't have permission to post there\n\nFix it in Settings, or remove that destination.",
        "protected_source_blocked": "🛡️ <b>This channel can't be used as a source</b>\n\n<b>{name}</b> has <b>Restrict saving content</b> turned on — its owner does not allow their posts to be copied.\n\nCopying from it would put <b>your own Telegram account</b> at risk: the owner can report it, and Telegram can act on your account, not ours.\n\nWe block these on purpose. Your account matters more to us than one extra source.\n\n✅ <b>Everything else works</b> — private channels, invite-only groups and bots are all fine.",
        "protected_source_task": "🛡️ <b>Forwarding stopped for this source</b>\n\nTask: <b>{task}</b>\n\nOne of this task's source channels has turned on <b>Restrict saving content</b>, so its posts can no longer be copied.\n\nThis protects <b>your account</b> — copying from a protected channel can get your account reported.\n\nYour other sources are still working normally.",
        "expiry_warning": "⏳ <b>Your {plan} plan expires in {days}</b>\n\n📅 Expires: {expiry}\n\nAfter that you move to the <b>Free plan</b>:\n• 1 task only\n• 1 source, 1 target\n• 50 messages/day\n\nYour extra tasks will be paused and your settings kept safe — renew any time to get them back.\n\n👉 Use /plans to continue.",
        "expiry_today": "⚠️ <b>Your {plan} plan expires today</b>\n\n📅 Expires: {expiry}\n\nFrom tomorrow you'll be on the <b>Free plan</b> — 1 task, 1 source, 1 target, 50 messages/day.\n\nYour tasks and settings are kept safe, just paused.\n\n👉 Renew now with /plans",
        "expiry_done": "❌ <b>Your {plan} plan has expired</b>\n\nYou're now on the <b>Free plan</b>:\n• 1 task, 1 source, 1 target\n• 50 messages/day\n\nYour other tasks are paused, not deleted — every setting is still there.\n\n👉 Use /plans to get them back instantly.",
        "config_no_tasks": "📋 <b>No tasks yet</b>\n\nYou haven't created any forwarding task, so there is nothing to show.\n\nUse /tasks to create your first task.",
        "config_select_task": "🛠️ <b>Your Configuration</b>\n\nSelect a task to view its current settings:",
        "config_footer": "🦾 Control All Settings: /settings",
        "settings_select_task": "⚙️ <b>Task Settings</b>\n\nSelect a task to configure:",
        "settings_no_tasks": "📋 You don't have any tasks yet. Create one to unlock settings.",
        "settings_main_title": "⚙️ <b>Settings for:</b> {task_name}\n\nChoose a category:",
        "settings_cat_channels": "📥 <b>Source/Target Channels</b>\n\nManage this task's channels:",
        "settings_cat_messages": "💬 <b>Message Settings</b>\n\nCustomize the text that is forwarded:",
        "settings_cat_cleanup": "🧹 <b>Text Cleanup</b>\n\nStrip or reformat parts of the message:",
        "settings_cat_filters": "🔍 <b>Filters</b>\n\nControl which messages get forwarded:",
        "settings_cat_replace": "🔁 <b>Replacements</b>\n\nSwap words, usernames and links:",
        "settings_cat_media": "🖼️ <b>Media &amp; Watermark</b>\n\nControl media handling:",
        "settings_cat_forwarding": "🚀 <b>Forwarding Settings</b>\n\nControl forwarding behavior:",
        "settings_cat_senderfilter": "👤 <b>Sender Filter</b>\n\nAllow only specific senders:",
        "settings_cat_reaction": "😍 <b>Auto Reaction</b>\n\nAutomatically react to messages:",
        "settings_cat_topics": "🧵 <b>Topics Forwarding</b>\n\nForward from specific forum topics:",
        "feature_locked": "🔒 <b>Feature Locked</b>\n\n<b>{feature}</b> is available on the <b>{required_plan}</b> plan.\n\nUpgrade to unlock this feature.",
        "feature_locked_short": "🔒 Locked — requires {required_plan}",
        "feature_locked_toast": "🔒 Requires {required_plan} plan",
        "setting_saved": "✅ <b>{feature}</b> saved.",
        "setting_cleared": "✅ <b>{feature}</b> cleared.",
        "setting_invalid_number": "⚠️ Please send a valid number.",
        "setting_invalid_ids": "⚠️ Please send only numeric Telegram IDs or @usernames separated by commas.",
        "setting_invalid_replace": "⚠️ Invalid format. Use <code>old=new</code> separated by commas.",
        "toggle_on": "✅ <b>{feature}</b> is now <b>ON</b>.",
        "toggle_off": "❌ <b>{feature}</b> is now <b>OFF</b>.",

        # ---------- SETTINGS: HEADER / FOOTER ----------
        "header_prompt": "✏️ Send the new <b>Header</b> text. A short line added to the top of every forwarded message.\n\nSend /clear to remove.\nSend /back to cancel.",
        "footer_prompt": "✏️ Send the new <b>Footer</b> text. A short line added to the bottom of every forwarded message.\n\nSend /clear to remove.\nSend /back to cancel.",
        "per_target_hf_prompt": "🎯 <b>Custom Header/Footer Per Target</b>\n\nSet a different header and footer for each destination.\n\nFormat: <code>target=header|footer</code>\nExample:\n<code>@dealkoti=🔥 Hot Deal|Join @dealkoti</code>\n\nSeparate multiple targets with new lines.\nSend /clear to remove all.\nSend /back to cancel.",

        # ---------- SETTINGS: TEXT CLEANUP ----------
        "link_preview_prompt": "🔗 <b>Link Preview</b>\n\nWhen ON, forwarded messages show the website preview card below the link.\nWhen OFF, only the plain text is sent.",
        "remove_usernames_prompt": "🙈 <b>Remove Usernames</b>\n\nWhen ON, every <code>@handle</code> is stripped out of the forwarded text.\n\n💡 If you want to swap a handle for your own instead of deleting it, use <b>Replace Usernames</b>.",
        "remove_links_prompt": "🚫 <b>Remove Links</b>\n\nWhen ON, every URL is stripped out of the forwarded text.\n\n💡 If you want to swap a link for your own instead of deleting it, use <b>Replace Links</b>.",
        "mono_text_prompt": "🎁 <b>Code Filter</b>\n\nForwards ONLY the gift/coupon code from a post and drops everything else.\n\nChannels hide codes in two different formats, so pick the one your source uses:\n\n🔠 <b>Monospace only</b> — code-style text, tap to copy\n🫥 <b>Spoiler only</b> — hidden text with shimmering dots, tap to reveal\n🔠+🫥 <b>Both</b> — catches either one\n\n⚠️ Posts with no code are <b>skipped completely</b>, and media is not forwarded. Your header and footer are still added.",
        "hidden_links_prompt": "🕵️ <b>Disable Hidden Links</b>\n\nSome messages hide the real URL behind clickable words. When ON, the hidden URL is revealed as plain text so nothing is masked.",
        "trim_words_prompt": "✂️ <b>Trim Single Words/Lines</b>\n\nSend the words or whole lines you want removed from every message.\n\nFormat: <code>Promoted, Sponsored, Ad</code>\n\n💡 If a line contains only that word, the whole line is removed.\nSend /clear to remove all.\nSend /back to cancel.",

        # ---------- SETTINGS: FILTERS ----------
        "blacklist_prompt": "🔍 Send words to <b>block</b>. Messages containing these words will be skipped.\n\nFormat: <code>spam, scam, ad</code>\nSend /clear to remove all.\nSend /back to cancel.",
        "whitelist_prompt": "🔍 Send words to <b>require</b>. Only messages containing at least one of these words will be forwarded (leave empty to allow all).\n\nFormat: <code>deal, offer, free</code>\nSend /clear to remove.\nSend /back to cancel.",
        "userfilter_prompt": "👤 <b>Sender Filter</b> — only these users' messages will be forwarded from the source; everyone else is skipped.\n\nYou can send Telegram user IDs or @usernames:\nFormat: <code>123456789, @dealkoti</code>\n\nTip: To find a user's ID, forward one of their messages to @userinfobot.\nSend /clear to allow everyone.\nSend /back to cancel.",

        # ---------- SETTINGS: REPLACEMENTS ----------
        "replace_prompt": "✏️ Send replacement rules.\n\nFormat: <code>old=new</code>\nExamples:\n• <code>sale=SALE</code>\n• <code>amazon=Amazon</code>\n\nSeparate multiple rules with commas.\nSend /clear to remove all.\nSend /back to cancel.",
        "replace_usernames_prompt": "👤 <b>Replace Usernames</b>\n\nSwap other channels' handles for your own.\n\nFormat: <code>@old=@new</code>\nExample: <code>@otherchannel=@dealkoti</code>\n\nSeparate multiple rules with commas.\nSend /clear to remove all.\nSend /back to cancel.",
        "replace_links_prompt": "🔗 <b>Replace Links</b>\n\nSwap links for your own.\n\nFormat: <code>oldlink=newlink</code>\nExample: <code>t.me/other=t.me/dealkoti</code>\n\nSeparate multiple rules with commas.\nSend /clear to remove all.\nSend /back to cancel.",

        # ---------- SETTINGS: MEDIA / WATERMARK ----------
        "watermark_text_prompt": "💧 Send the watermark text that will be drawn on forwarded images.\n\nExample: <code>@YourChannel</code>\nSend /clear to reset to default.\nSend /back to cancel.",
        "watermark_on": "✅ Watermark: <b>On</b>",
        "watermark_off": "❌ Watermark: <b>Off</b>",
        "watermark_position_prompt": "📍 <b>Watermark Position</b>\n\nChoose where the watermark sits on the image:",
        "watermark_size_prompt": "🔎 <b>Watermark Size</b>\n\nChoose how large the watermark text should be:",
        "watermark_opacity_prompt": "🌫️ <b>Watermark Opacity</b>\n\nChoose how see-through the watermark should be:",
        "watermark_style_saved": "✅ Watermark style updated — Position: <b>{position}</b>, Size: <b>{size}</b>, Opacity: <b>{opacity}%</b>",

        # ---------- SETTINGS: FORWARDING ----------
        "autodelete_prompt": "🗑️ Send the number of seconds after which forwarded messages should be auto-deleted from the destination.\n\nExample: <code>3600</code> (1 hour)\nSend <code>0</code> or /clear to turn off.\nSend /back to cancel.",
        "editsync_on": "✅ Edit Sync: <b>On</b>",
        "editsync_off": "❌ Edit Sync: <b>Off</b>",
        "editsync_prompt": "🔄 <b>Post Edit Sync</b>\n\nWhen ON, editing a message in the source chat automatically updates the copy already sent to your destinations.",
        "delay_timer_prompt": "⏱️ <b>Delay Timer Per Target</b>\n\nChoose how long to wait between sending to each destination. Useful to look natural and avoid limits.",
        "antiban_prompt": "🛡️ <b>Anti-Ban Speed</b>\n\nChoose how fast messages are pushed out. Slower is safer for your account.",

        # ---------- SETTINGS: AUTO REACTION ----------
        "reaction_prompt": "😍 <b>Auto Reaction System</b>\n\nAutomatically react to every message that gets forwarded.\n\nCurrent emoji: {emoji}\nReact on: <b>{target}</b>",
        "reaction_pick_emoji": "😍 Choose the emoji to react with:",
        "reaction_pick_target": "🎯 Where should the reaction be placed?",
        "reaction_target_source": "📥 On the source message",
        "reaction_target_destination": "📤 On the forwarded copy",
        "reaction_saved": "✅ Auto Reaction set to {emoji}.",
        "reaction_custom_prompt": "😍 Send the emoji you want to react with (one emoji only).\n\nSend /back to cancel.",
        "reaction_invalid": "⚠️ Please send a single emoji.",
        "reaction_not_allowed": "ℹ️ That chat does not allow this reaction, so it will be skipped silently.",

        # ---------- SETTINGS: TOPICS ----------
        "topics_prompt": "🧵 <b>Topics Forwarding</b>\n\nThis chat is a forum with topics. Choose which topics to forward from.\n\nNothing selected = forward from ALL topics.",
        "topics_none": "ℹ️ This chat has no topics, or your account cannot see them. Forwarding will use the whole chat.",
        "topics_saved": "✅ Topic selection saved ({count} selected).",
        "topics_cleared": "✅ Now forwarding from all topics.",
        "topics_not_forum": "ℹ️ Topics Forwarding only works on forum-enabled groups.",

        # ---------- FILE UPLOAD ----------
        "upload_prompt": "📎 <b>Upload File</b>\n\nSend me any file (as document). It will be stored securely and can be attached to your forwarded messages.\n\nMax size: {max} MB\nSend /back to cancel.",
        "upload_success": "✅ File stored: <b>{name}</b> ({size})\n\nThis file will now be attached to every forwarded message automatically.",
        "upload_too_big": "⚠️ File is larger than the {max} MB limit.",
        "upload_not_platinum": "🔒 File upload is a Platinum feature. Upgrade to use it.",
        "upload_no_channel": "⚠️ File storage is not configured yet. Contact admin.",
        "upload_replace_confirm": "⚠️ You already have a file uploaded: <b>{old_name}</b>\n\nReplace it with <b>{new_name}</b>? You can only have one stored file at a time — the old one will be deleted.",

        # ---------- HELP / MISC ----------
        "help_intro": "📖 <b>Help</b> — Try the commands below:\n\n{commands}\n\nTap a command button or send the command to see its features.",
        "help_cat_setting": "General bot settings",
        "help_cat_forwarding": "All forwarding-related settings",
        "help_cat_filters": "Manage filters and replacements",
        "help_cat_media": "Media and watermark controls",
        "help_configure_hint": "Tap a feature to configure it. 🔒 features need a plan upgrade.",
        "back_to_help": "◀️ Back to Help",
        "open_settings_btn": "⚙️ Open Settings",
        "view_plans_btn": "💎 View Plans",
        "language_current": "🌐 Current language: <b>{current}</b>",
        "language_confirm_title": "🌐 <b>Confirm Language Change</b>\n\nSwitch to <b>{new}</b>?",
        "language_confirm_yes": "✅ Yes, switch",
        "language_confirm_no": "✖️ Cancel",
        "support_intro": "📞 <b>Support</b>\n\nDon't worry — most issues (payment, setup, task errors) can be solved quickly.\n\nTap the button below to contact our support team. We usually reply within 24 hours.",
        "updates_intro": "📢 <b>Updates Channel</b>\n\nThis channel posts:\n• New features\n• Important changes\n• Announcements for all DealsKoti bots\n\nYou must stay joined to use the bot.",
        "refer_intro": "🎁 <b>Refer &amp; Earn</b>\n\nShare your referral link with friends:\n{link}\n\nUsers joined via your link: <b>{count}</b>\n\nReferral rewards are processed by admin via payouts.",
        "broadcast_preview_title": "📣 <b>Broadcast Preview</b>",
        "broadcast_sending": "📣 Sending to {count} users…",
        "broadcast_done": "✅ Broadcast complete\nSent: {sent}\nFailed: {failed}\nBlocked: {blocked}",
    },

    "hinglish": {
        # ---------- CORE / MENU ----------
        "main_menu": "Hi {name},\nDealskoti Auto Forwarder Bot me aapka swagat hai.\nAuto forwarding ab bilkul simple hai.\nHamara Bot aapke Account Data ki 100% Security deta hai.\n\nNeeche diye menu se ek option chunein:",
        "help_title": "📚 <b>Help &amp; Commands</b>\n\nYe commands aap use kar sakte hain:\n{commands}",
        "admin_help_title": "🛠️ <b>Admin Commands</b>\n\n{commands}",
        "admin_only": "⚠️ Ye command sirf Admins use kar sakte hain.",
        "support": "📞 <b>Customer Support</b>\n\nAgar aapko payment, tasks, ya setup me madad chahiye, toh yahan message karein:\n{link}\n\nHumari team aam taur par 24 ghante me reply karti hai.",
        "choose_language": "🌐 Apni bhasha (language) chunein:",
        "language_saved": "✅ Aapki language save ho gayi hai!",
        "faq_title": "❓ <b>FAQ (Page {page} of {pages})</b>",
        "faq_hint": "👇 Jawab dekhne ke liye sawal par tap karein:",
        "faq_answer": "❓ <b>{question}</b>\n\n💡 {answer}",
        "unknown_command": "⚠️ Galat command. Sahi commands ki list dekhne ke liye /help bhejein.",
        "generic_error": "⚠️ Kuch galat ho gaya. Dobara try karein.",
        "validation_invalid": "⚠️ Galat input. Kripya dobara try karein ya /back bhejein.",

        # ---------- ACCOUNT ----------
        "account_details": "👤 <b>Mera Account</b>\n\nNaam: {name}\nUsername: {username}\nUser ID: <code>{user_id}</code>\n\n💎 <b>Subscription</b>\nActive Plan: {plan}\nPlan Start Hua: {plan_started}\nPlan Expiry: {expiry}\nLast Transaction ID: <code>{txn_id}</code>\nPayment Status: {payment}\n\n⚙️ <b>Usage &amp; Status</b>\nTelegram Status: {session}\nActive Tasks: {tasks}\nAaj ke Messages: {forwarding}\nChannel Membership: {membership}\nLanguage: {user_language}",

        # ---------- LOGIN FLOW ----------
        "login_phone": "📱 <b>Connect Account</b>\n\nApna Telegram phone number country code ke sath bhejein.\n\nExample (India): <code>+919876543210</code>\n\n⚠️ Dhyan rakhein:\n• Number <code>+</code> aur country code se shuru ho\n• Number ke beech me KOI SPACE nahi hona chahiye\n• Sahi: <code>+914527896325</code>\n• Galat: <code>+91 45278 96325</code>",
        "login_pin": "🔑 <b>OTP/PIN Daalein</b>\n\nTelegram ne aapke Telegram app par verification code bheja hoga. Kripya neeche code enter karein.\n\n⚠️ <b>Zaroori:</b> Agar aapka code <code>12345</code> hai, toh use <code>PIN12345</code> ke format mein enter karein.\n\n<code>PIN</code> prefix lagana zaroori hai, warna account connect nahi hoga.",
        "login_2fa": "🔒 <b>Two-Step Verification (Cloud Password)</b>\n\nAapke account par ek extra password hai (ye aapke account password se <b>alag</b> hai).\n\n<b>Kya daalna hai:</b>\n• Telegram Settings → Privacy → Two-Step Verification me jo 'Cloud Password' set kiya hai wahi.\n• Ye aapke SMS login code se <b>different</b> hai.\n\n<b>Agar bhool gaye:</b>\n1. Phone me Telegram kholein\n2. Settings → Privacy → Two-Step Verification par jayein\n3. 'Forgot password?' dabayein aur reset karein\n4. Wapas yahan aakar /connect se retry karein\n\n🔐 Aapka password bhejte hi turant is chat se delete ho jayega.\n\nNeeche apna 2FA password daalein:",
        "login_congrats": "🎉 <b>Congrats {name},</b>\n<b>Aapka Account Successfully Connect Ho Gaya!</b>\n\n📱 Phone: <code>{phone}</code>\n👤 Logged In as - {username}\n\n📋 Task banane ke liye /tasks use karein!\n💎 Upgrade karne ke liye /plans use karein!",
        "login_success": "✅ <b>Account Successfully Connect Ho Gaya!</b>\n\nAb aap tasks bana kar auto-forwarding shuru kar sakte hain.",
        "login_cancelled": "↩️ Login process safely cancel kar diya gaya hai.",
        "login_failed": "⚠️ Login fail ho gaya ya time out ho gaya. Kripya wapas try karein.",
        "already_connected": "ℹ️ Aap already connected ho. Dobara connect karne se purana session replace ho jayega.",
        "reconnect_anyway": "🔄 Phir Se Connect Karo",
        "invalid_phone": "⚠️ Phone number ka format galat hai.\n\nCountry code ke sath, bina space number bhejein:\n• India: <code>+919876543210</code>\n• Format: <code>+&lt;country code&gt;&lt;number&gt;</code>\n\nDobara try karein:",
        "connect_required": "🔌 <b>Pehle account connect karein</b>\n\nTask banane, file upload karne, ya plan kharidne se pehle apna Telegram account connect karna zaroori hai — Free plan me bhi.\n\nNeeche tap karke connect karein:",
        "disconnect_confirm": "⚠️ <b>Telegram Disconnect Karein?</b>\n\nAapka session remove ho jayega aur tasks pause ho jayenge. Subscription aur data safe hain.",
        "disconnect_done": "✅ Telegram session disconnect ho gaya.",

        # ---------- PLANS & BILLING ----------
        "choose_plan": "💎 <b>Apna Plan Chunein</b>\n\nPoora price aur features dekhne ke liye neeche se plan select karein:",
        "plan_details": "{details}\n\n👇 Neeche se apna billing cycle chunein:",
        "plan_details_free": "{details}",
        "billing_details": "💳 <b>Checkout Summary</b>\n\nPlan: {plan}\nCycle: {cycle}\nOriginal Price: {original}\nDiscount: {discount}\n\n<b>Payable Amount: {payable}</b>\n\n💷 UPI/Card se pay — turant activate\n🪙 USDT se pay — admin verify karega\n⭐ Telegram Stars se pay — admin verify karega\n\nNeeche se payment method chunein:",
        "payment_link": "🔗 <b>Payment Link Ban Gaya Hai</b>\n\nPlan: {plan} ({cycle})\nAmount: {amount}\n\nRazorpay se payment karne ke liye neeche tap karein. Plan apne aap activate ho jayega.",
        "payment_success": "🎉 <b>Payment Successful!</b>\n\nPlan: {plan}\nDays Added: {days} din\nAmount Paid: {amount}\nTransaction ID: <code>{txn_id}</code>\nNayi Expiry: {expiry}\n\nDealsKoti se judne ke liye shukriya! Aapki nayi limits apply ho chuki hain.",
        "payment_failed": "⚠️ Payment initiate nahi ho paya ya cancel ho gaya.",
        "cycle_explainer": "🗓️ <b>Billing Cycles</b>\n\n• <b>Weekly</b>: 7 din\n• <b>Monthly</b>: 30 din\n• <b>Yearly</b>: 365 din aur 20% off\n\nFinal payable amount server-side calculate hoke payment se pehle dikhaya jata hai.",
        "next_scheduled_plan": "📅 <b>Scheduled:</b> {plan} {date} se shuru",
        "expired_notice": "⏳ Aapka {plan} plan expire ho gaya. /plans se renew karein.",
        "downgrade_scheduled_notice": "📅 Aapka current plan {date} tak active rahega. Phir {scheduled} par switch hoga.",

        # ---------- USDT (ADMIN-VERIFIED) ----------
        "usdt_unavailable": "⚠️ USDT payment abhi available nahi hai. Kripya Razorpay (INR) se pay karein.",
        "usdt_instructions": "🪙 <b>USDT Payment</b>\n\nPlan: <b>{plan}</b> ({cycle})\nAmount: <b>{amount} USDT</b>\nNetwork: <b>{network}</b>\nWallet:\n<code>{wallet}</code>\n\n1️⃣ Upar diye wallet me EXACTLY {amount} USDT ({network}) bhejein.\n2️⃣ 'I Have Paid' dabakar apna Transaction Hash (TXID) paste karein.\n3️⃣ Admin verify karega aur plan apne aap activate ho jayega.",
        "usdt_paid_btn": "✅ Pay Kar Diya — TXID Bhejo",
        "usdt_txid_prompt": "🧾 Apna USDT Transaction Hash (TXID) bhejein:\n\nCancel karne ke liye /back bhejein.",
        "usdt_submitted": "✅ TXID submit ho gaya! Admin jaldi verify karega. Approve hote hi notification milega.",
        "usdt_approved_user": "🎉 Aapka USDT payment approve ho gaya!\n\nPlan: {plan}\nDuration: {days} din\nNayi Expiry: {expiry}",
        "usdt_rejected_user": "❌ Aapka USDT payment verify nahi ho paya.\n\nTXID: <code>{txid}</code>\nKripya support se baat karein ya sahi TXID bhejein.",

        # ---------- TELEGRAM STARS (ADMIN-VERIFIED) ----------
        "stars_unavailable": "⚠️ Is plan ke liye Telegram Stars payment available nahi hai. UPI/Card ya USDT use karein.",
        "stars_instructions": "⭐ <b>Telegram Stars Payment</b>\n\nPlan: <b>{plan}</b> ({cycle})\nAmount: <b>{amount} Stars</b>\n\n1️⃣ {receiver} ko <b>{amount} Stars</b> bhejein.\n2️⃣ Neeche 'I Have Paid' dabakar screenshot ya transaction ID bhejein.\n3️⃣ Admin verify karega aur plan apne aap activate ho jayega.\n\n💡 Stars Telegram me hi kharid sakte ho: Settings → My Stars → Buy More Stars.",
        "stars_paid_btn": "✅ Pay Kar Diya — Proof Bhejo",
        "stars_proof_prompt": "🧾 Apna Stars payment ka proof bhejein.\n\nAap <b>screenshot</b> bhej sakte ho, ya transaction ID text me paste kar sakte ho.\n\nCancel karne ke liye /back bhejein.",
        "stars_submitted": "✅ Proof submit ho gaya! Admin jaldi verify karega. Approve hote hi notification milega.",
        "stars_approved_user": "🎉 Aapka Stars payment approve ho gaya!\n\nPlan: {plan}\nDuration: {days} din\nNayi Expiry: {expiry}",
        "stars_rejected_user": "❌ Aapka Stars payment verify nahi ho paya.\n\nReference: <code>{ref}</code>\nKripya support se baat karein ya sahi proof bhejein.",
        "stars_receiver_missing": "⚠️ Stars payment abhi configure nahi hua hai. Support se baat karein.",

        # ---------- ADMIN PAYMENT REVIEW ----------
        "admin_payment_review": "🧾 <b>Manual Payment Review</b>\n\nMethod: <b>{method}</b>\nUser: {name} (<code>{user_id}</code>)\nPlan: <b>{plan}</b> ({cycle})\nAmount: <b>{amount}</b>\nReference:\n<code>{ref}</code>\n\nApprove karne par plan turant activate ho jayega.",
        "admin_no_pending": "✅ Abhi koi pending manual payment nahi hai.",
        "admin_payment_approved": "✅ Approve ho gaya — {plan} activate for <code>{user_id}</code>.",
        "admin_payment_rejected": "❌ Reject ho gaya — user ko bata diya gaya hai.",
        "admin_payment_gone": "⚠️ Ye payment pehle hi handle ho chuka hai.",

        # ---------- TASKS ----------
        "tasks_title": "📋 <b>Aapke Forwarding Tasks:</b>",
        "no_tasks_short": "📋 Abhi tak koi task nahi banaya gaya hai. Naya task banane ke liye 'Create New Task' dabayein.",
        "tasks_summary": "📋 <b>Aapke Tasks</b>\n\nPlan: {plan}\nBanaye: {created}\nBache: {remaining}\nTotal allowed: {total}",
        "task_name": "📝 <b>Naya Task: Naam</b>\n\nIs task ko pehchanne ke liye ek chota naam bhejein (jaise: <code>Amazon Deals</code>):",
        "task_source": "📥 <b>Naya Task: Source Chat (Jahan se aayega)</b>\n\nSource chat se koi bhi message yahan forward karein, ya uska Username/ID bhejein.\n\nAap ek se zyada source add kar sakte hain. Ho jaye to <code>/done</code> bhejein.",
        "task_destination": "📤 <b>Naya Task: Destination Chat (Jahan bhejna hai)</b>\n\nDestination chat se koi bhi message yahan forward karein, ya uska Username/ID bhejein.\n\nAap ek se zyada destination add kar sakte hain. Ho jaye to <code>/done</code> bhejein.",
        "task_created": "✅ Task <b>{task_name}</b> successfully ban gaya hai!\n\nAb aap Tasks menu se iski settings set kar sakte hain ya isko Resume kar sakte hain.",
        "task_creation_reminder": "👋 <b>Apna forwarding task banayein</b>\n\nAapne bot start kiya tha, lekin abhi tak koi task nahi banaya. Task banakar forwarding start karein.\n\nKuch samajh na aaye to Support bot se poochhein: {support}",
        "task_renamed": "✅ Task ka naam <b>{name}</b> ho gaya.",
        "task_rename_prompt": "✏️ Task ke liye naya naam bhejein (max 120 characters).",
        "rename_empty": "⚠️ Naam khali nahi ho sakta.",
        "delete_confirm": "⚠️ Task <b>{name}</b> hamesha ke liye delete karein?",

        # ---------- CHAT PICKER ----------
        "source_list_title": "📥 <b>Source Chat Chunein</b>\n\nList se chat pick karein, ya desired chat se koi bhi message forward karein, ya public username/ID type karein.\n\nTip: Chat ko temporarily pin kar lein taaki asani se mile.",
        "dest_list_title": "📤 <b>Destination Chat Chunein</b>\n\nList se chat pick karein, ya desired chat se koi bhi message forward karein, ya public username/ID type karein.",
        "chat_page": "Page {page} / {pages}",
        "no_chats_found": "⚠️ Aapke account par abhi koi chat nahi mili. Koi message forward karein ya public @username type karein.",
        "picker_bad_number": "⚠️ Upar diye gaye buttons me se koi tap karein, ya list me se number bhejein (jaise <code>3</code>).",
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
        "picker_selected_marker": "⬅️ selected",
        "picker_instructions": "👆 Neeche number button par tap karke chat select/deselect karo\n✅ Ho jaye to <b>Done</b> dabao\n✍️ List me nahi hai? Us chat se koi message forward karo, ya uska @username bhejo",
        "picker_empty": "⚠️ Koi recent chat nahi mila. @username manually type karo ya chat se koi message forward karo.",
        "sources_updated": "✅ Sources update ho gaye.",
        "destinations_updated": "✅ Destinations update ho gaye.",
        "min_one_source": "⚠️ Pehle kam se kam ek source select karo.",
        "min_one_dest": "⚠️ Pehle kam se kam ek destination select karo.",
        "tier_limit_reached": "⚠️ Aapke paas <b>{plan}</b> plan hai — aap maximum <b>{limit}</b> {field} hi select kar sakte ho.\n\nZyada ke liye plan upgrade karo.",
        "invalid_channel_format": "⚠️ Format galat hai. Channel in formats me bhejein:\n• <code>@Dealkoti</code>\n• <code>https://t.me/Dealkoti</code>",

        # ---------- SETTINGS: NAVIGATION ----------
        "flow_cancelled": "↩️ <b>Cancel ho gaya: {flow}</b>\n\n{hint}\nWo step cancel kar diya gaya — ab aapki nayi command chal rahi hai 👇",
        "home_first_time": "🎉 <b>DealsKoti Auto Forwarder me swagat hai!</b>\n\nKisi bhi channel ki posts apne channel me copy karein — apne aap, 24/7.",
        "home_not_connected": "🏠 <b>Main Menu</b>\n\n🔌 Account: <b>connect nahi hai</b>\n\nTask banane aur forwarding shuru karne ke liye apna Telegram account connect karein.",
        "home_no_tasks": "🏠 <b>Main Menu</b>\n\n👤 {who}\n💎 Plan: <b>{plan}</b>\n📋 Tasks: abhi koi nahi\n\n👉 Forwarding shuru karne ke liye pehla task banayein.",
        "home_ready": "🏠 <b>Main Menu</b>\n\n👤 {who}\n💎 Plan: <b>{plan}</b>\n📋 Tasks: {tasks}\n📊 Aaj: {today} / {cap} forward hue\n🕐 Last forward: {last}\n\nAap kya karna chahenge?",
        "limit_reached_notice": "⚠️ <b>Aaj Ki Limit Poori Ho Gayi</b>\n\nAapne aaj ke saare <b>{cap}</b> messages use kar liye.\n\n🕛 Raat 12 baje reset hogi\n💎 Zyada limit ke liye upgrade karein",
        "destination_failed": "⚠️ <b>Forwarding Me Dikkat</b>\n\nTask: <b>{task}</b>\nYahan nahi bhej paye: <b>{dest}</b>\n\nWajah ye ho sakti hai:\n• Aapka account us channel se hata diya gaya\n• Channel delete ho gaya\n• Wahan post karne ki permission nahi hai\n\nSettings me theek karein, ya wo destination hata dein.",
        "protected_source_blocked": "🛡️ <b>Ye channel source nahi ban sakta</b>\n\n<b>{name}</b> par <b>Restrict saving content</b> laga hua hai — iske owner ne apni posts copy hone se rok rakha hai.\n\nIsse copy karna <b>aapke apne Telegram account</b> ke liye risk hai: owner report kar sakta hai, aur Telegram action aapke account par lega, humare par nahi.\n\nHum ise jaan-boojh kar block karte hain. Ek extra source se zyada zaroori aapka account hai.\n\n✅ <b>Baaki sab chalta hai</b> — private channels, invite-only groups aur bots sab theek hain.",
        "protected_source_task": "🛡️ <b>Is source se forwarding ruk gayi</b>\n\nTask: <b>{task}</b>\n\nIs task ke ek source channel par <b>Restrict saving content</b> on ho gaya hai, isliye uski posts ab copy nahi ho sakti.\n\nYe <b>aapke account ki suraksha</b> ke liye hai — protected channel se copy karne par account report ho sakta hai.\n\nAapke baaki sources normal chal rahe hain.",
        "expiry_warning": "⏳ <b>Aapka {plan} plan {days} me khatam ho raha hai</b>\n\n📅 Expiry: {expiry}\n\nUske baad aap <b>Free plan</b> par aa jayenge:\n• Sirf 1 task\n• 1 source, 1 target\n• 50 messages/day\n\nAapke extra tasks pause ho jayenge aur settings safe rahengi — kabhi bhi renew karke wapas paa sakte hain.\n\n👉 Jaari rakhne ke liye /plans use karein.",
        "expiry_today": "⚠️ <b>Aapka {plan} plan aaj khatam ho raha hai</b>\n\n📅 Expiry: {expiry}\n\nKal se aap <b>Free plan</b> par honge — 1 task, 1 source, 1 target, 50 messages/day.\n\nAapke tasks aur settings safe hain, bas pause ho jayenge.\n\n👉 Abhi renew karein: /plans",
        "expiry_done": "❌ <b>Aapka {plan} plan khatam ho gaya</b>\n\nAb aap <b>Free plan</b> par hain:\n• 1 task, 1 source, 1 target\n• 50 messages/day\n\nAapke baaki tasks pause hain, delete nahi hue — saari settings waisi hi hain.\n\n👉 Turant wapas paane ke liye /plans use karein.",
        "config_no_tasks": "📋 <b>Abhi koi task nahi hai</b>\n\nAapne koi forwarding task nahi banaya, isliye dikhane ko kuch nahi hai.\n\nPehla task banane ke liye /tasks use karein.",
        "config_select_task": "🛠️ <b>Aapki Configuration</b>\n\nSettings dekhne ke liye task chunein:",
        "config_footer": "🦾 Saari settings badalne ke liye: /settings",
        "settings_select_task": "⚙️ <b>Task Settings</b>\n\nSettings karne ke liye task chunein:",
        "settings_no_tasks": "📋 Abhi koi task nahi hai. Settings unlock karne ke liye pehle ek task banayein.",
        "settings_main_title": "⚙️ <b>Settings:</b> {task_name}\n\nCategory chunein:",
        "settings_cat_channels": "📥 <b>Source/Target Channels</b>\n\nIs task ke channels manage karo:",
        "settings_cat_messages": "💬 <b>Message Settings</b>\n\nForward hone wale message ka text customize karein:",
        "settings_cat_cleanup": "🧹 <b>Text Cleanup</b>\n\nMessage ke hisso ko hatao ya format badlo:",
        "settings_cat_filters": "🔍 <b>Filters</b>\n\nKaun se messages forward honge ye control karein:",
        "settings_cat_replace": "🔁 <b>Replacements</b>\n\nWords, usernames aur links badlein:",
        "settings_cat_media": "🖼️ <b>Media &amp; Watermark</b>\n\nMedia handling control karein:",
        "settings_cat_forwarding": "🚀 <b>Forwarding Settings</b>\n\nForwarding ka behavior control karein:",
        "settings_cat_senderfilter": "👤 <b>Sender Filter</b>\n\nSirf kuch senders ko allow karein:",
        "settings_cat_reaction": "😍 <b>Auto Reaction</b>\n\nMessages par apne aap reaction lagayein:",
        "settings_cat_topics": "🧵 <b>Topics Forwarding</b>\n\nForum ke specific topics se forward karein:",
        "feature_locked": "🔒 <b>Feature Locked</b>\n\n<b>{feature}</b> sirf <b>{required_plan}</b> plan pe available hai.\n\nUnlock karne ke liye upgrade karein.",
        "feature_locked_short": "🔒 Locked — {required_plan} chahiye",
        "feature_locked_toast": "🔒 {required_plan} plan chahiye",
        "setting_saved": "✅ <b>{feature}</b> save ho gaya.",
        "setting_cleared": "✅ <b>{feature}</b> hata diya gaya.",
        "setting_invalid_number": "⚠️ Kripya ek valid number bhejein.",
        "setting_invalid_ids": "⚠️ Kripya sirf numeric Telegram IDs ya @usernames comma se alag karke bhejein.",
        "setting_invalid_replace": "⚠️ Galat format. <code>purana=naya</code> comma se alag karke bhejein.",
        "toggle_on": "✅ <b>{feature}</b> ab <b>ON</b> hai.",
        "toggle_off": "❌ <b>{feature}</b> ab <b>OFF</b> hai.",

        # ---------- SETTINGS: HEADER / FOOTER ----------
        "header_prompt": "✏️ Naya <b>Header</b> text bhejein. Ye har forwarded message ke upar add hoga.\n\nHatane ke liye /clear.\nCancel ke liye /back.",
        "footer_prompt": "✏️ Naya <b>Footer</b> text bhejein. Ye har forwarded message ke neeche add hoga.\n\nHatane ke liye /clear.\nCancel ke liye /back.",
        "per_target_hf_prompt": "🎯 <b>Har Target Ka Alag Header/Footer</b>\n\nHar destination ke liye alag header aur footer set karo.\n\nFormat: <code>target=header|footer</code>\nExample:\n<code>@dealkoti=🔥 Hot Deal|Join @dealkoti</code>\n\nEk se zyada target ke liye nayi line me likho.\nSab hatane ke liye /clear.\nCancel ke liye /back.",

        # ---------- SETTINGS: TEXT CLEANUP ----------
        "link_preview_prompt": "🔗 <b>Link Preview</b>\n\nON hone par forwarded message me link ke neeche website ka preview card dikhega.\nOFF hone par sirf plain text jayega.",
        "remove_usernames_prompt": "🙈 <b>Remove Usernames</b>\n\nON hone par har <code>@handle</code> forwarded text se hata diya jayega.\n\n💡 Agar handle hatane ki jagah apna handle lagana hai to <b>Replace Usernames</b> use karo.",
        "remove_links_prompt": "🚫 <b>Remove Links</b>\n\nON hone par har URL forwarded text se hata diya jayega.\n\n💡 Agar link hatane ki jagah apna link lagana hai to <b>Replace Links</b> use karo.",
        "mono_text_prompt": "🎁 <b>Code Filter</b>\n\nPost me se sirf gift/coupon code forward hoga, baaki sab hat jayega.\n\nChannels code do alag formats me chhupate hain, apne source wala chuno:\n\n🔠 <b>Monospace only</b> — code jaisa text, tap se copy\n🫥 <b>Spoiler only</b> — chhupa hua text, chamakte dots wala, tap se reveal\n🔠+🫥 <b>Both</b> — dono me se koi bhi ho to chalega\n\n⚠️ Jis post me code nahi hoga wo <b>poora skip</b> ho jayega, aur media forward nahi hoga. Aapka header aur footer phir bhi lagega.",
        "hidden_links_prompt": "🕵️ <b>Disable Hidden Links</b>\n\nKuch messages me asli URL clickable words ke peeche chhupa hota hai. ON hone par chhupa hua URL plain text me dikh jayega.",
        "trim_words_prompt": "✂️ <b>Trim Single Words/Lines</b>\n\nWo words ya poori lines bhejein jo har message se hatani hain.\n\nFormat: <code>Promoted, Sponsored, Ad</code>\n\n💡 Agar kisi line me sirf wahi word hai to poori line hat jayegi.\nSab hatane ke liye /clear.\nCancel ke liye /back.",

        # ---------- SETTINGS: FILTERS ----------
        "blacklist_prompt": "🔍 Words bhejein jo <b>block</b> karne hain. In words wale messages skip ho jayenge.\n\nFormat: <code>spam, scam, ad</code>\nSab hatane ke liye /clear.\nCancel ke liye /back.",
        "whitelist_prompt": "🔍 Words bhejein jo <b>required</b> hain. Sirf in me se koi ek word hone wale messages forward honge (khali chhodein toh sab allowed).\n\nFormat: <code>deal, offer, free</code>\nHatane ke liye /clear.\nCancel ke liye /back.",
        "userfilter_prompt": "👤 <b>Sender Filter</b> — sirf in users ke messages hi source se forward honge; baaki sab skip ho jayenge.\n\nAap Telegram user IDs ya @usernames bhej sakte ho:\nFormat: <code>123456789, @dealkoti</code>\n\nTip: Kisi user ki ID jaanne ke liye uska message @userinfobot ko forward karo.\nSab allow karne ke liye /clear.\nCancel ke liye /back.",

        # ---------- SETTINGS: REPLACEMENTS ----------
        "replace_prompt": "✏️ Replacement rules bhejein.\n\nFormat: <code>purana=naya</code>\nExamples:\n• <code>sale=SALE</code>\n• <code>amazon=Amazon</code>\n\nMultiple rules ke liye comma lagayein.\nSab hatane ke liye /clear.\nCancel ke liye /back.",
        "replace_usernames_prompt": "👤 <b>Replace Usernames</b>\n\nDoosre channels ke handle ki jagah apna handle lagao.\n\nFormat: <code>@purana=@naya</code>\nExample: <code>@otherchannel=@dealkoti</code>\n\nMultiple rules ke liye comma lagayein.\nSab hatane ke liye /clear.\nCancel ke liye /back.",
        "replace_links_prompt": "🔗 <b>Replace Links</b>\n\nLinks ki jagah apne links lagao.\n\nFormat: <code>puranalink=nayalink</code>\nExample: <code>t.me/other=t.me/dealkoti</code>\n\nMultiple rules ke liye comma lagayein.\nSab hatane ke liye /clear.\nCancel ke liye /back.",

        # ---------- SETTINGS: MEDIA / WATERMARK ----------
        "watermark_text_prompt": "💧 Watermark text bhejein jo forwarded images par draw hoga.\n\nExample: <code>@AapkaChannel</code>\nDefault par reset karne ke liye /clear.\nCancel ke liye /back.",
        "watermark_on": "✅ Watermark: <b>On</b>",
        "watermark_off": "❌ Watermark: <b>Off</b>",
        "watermark_position_prompt": "📍 <b>Watermark Position</b>\n\nImage par watermark kahan lagega ye chuno:",
        "watermark_size_prompt": "🔎 <b>Watermark Size</b>\n\nWatermark text kitna bada hoga ye chuno:",
        "watermark_opacity_prompt": "🌫️ <b>Watermark Opacity</b>\n\nWatermark kitna transparent hoga ye chuno:",
        "watermark_style_saved": "✅ Watermark style update ho gaya — Position: <b>{position}</b>, Size: <b>{size}</b>, Opacity: <b>{opacity}%</b>",

        # ---------- SETTINGS: FORWARDING ----------
        "autodelete_prompt": "🗑️ Seconds ki sankhya bhejein. Utne seconds baad message destination se auto-delete ho jayega.\n\nExample: <code>3600</code> (1 ghanta)\nBand karne ke liye <code>0</code> ya /clear.\nCancel ke liye /back.",
        "editsync_on": "✅ Edit Sync: <b>On</b>",
        "editsync_off": "❌ Edit Sync: <b>Off</b>",
        "editsync_prompt": "🔄 <b>Post Edit Sync</b>\n\nON hone par source chat me message edit karte hi aapke destinations me bheji hui copy bhi apne aap update ho jayegi.",
        "delay_timer_prompt": "⏱️ <b>Har Target Ke Beech Delay</b>\n\nHar destination me bhejne ke beech kitna wait karna hai ye chuno. Natural dikhne aur limits se bachne ke liye useful hai.",
        "antiban_prompt": "🛡️ <b>Anti-Ban Speed</b>\n\nMessages kitni tezi se bheje jayenge ye chuno. Dheema rakhna aapke account ke liye zyada safe hai.",

        # ---------- SETTINGS: AUTO REACTION ----------
        "reaction_prompt": "😍 <b>Auto Reaction System</b>\n\nHar forward hone wale message par apne aap reaction lagega.\n\nAbhi ka emoji: {emoji}\nReaction yahan: <b>{target}</b>",
        "reaction_pick_emoji": "😍 Reaction ke liye emoji chuno:",
        "reaction_pick_target": "🎯 Reaction kahan lagana hai?",
        "reaction_target_source": "📥 Source message par",
        "reaction_target_destination": "📤 Forward ki hui copy par",
        "reaction_saved": "✅ Auto Reaction {emoji} set ho gaya.",
        "reaction_custom_prompt": "😍 Jis emoji se reaction karna hai wo bhejein (sirf ek emoji).\n\nCancel ke liye /back.",
        "reaction_invalid": "⚠️ Kripya sirf ek emoji bhejein.",
        "reaction_not_allowed": "ℹ️ Us chat me ye reaction allowed nahi hai, isliye chup-chaap skip ho jayega.",

        # ---------- SETTINGS: TOPICS ----------
        "topics_prompt": "🧵 <b>Topics Forwarding</b>\n\nYe chat topics wala forum hai. Chuno kis topic se forward karna hai.\n\nKuch select na karo = SAB topics se forward hoga.",
        "topics_none": "ℹ️ Is chat me koi topic nahi hai, ya aapka account unhe dekh nahi sakta. Poore chat se forwarding hogi.",
        "topics_saved": "✅ Topic selection save ho gaya ({count} selected).",
        "topics_cleared": "✅ Ab sab topics se forward hoga.",
        "topics_not_forum": "ℹ️ Topics Forwarding sirf forum wale groups par kaam karta hai.",

        # ---------- FILE UPLOAD ----------
        "upload_prompt": "📎 <b>File Upload Karo</b>\n\nMujhe koi bhi file bhejo (document ki tarah). Ye safely store hogi aur aapke forwarded messages me attach ho sakti hai.\n\nMax size: {max} MB\nCancel karne ke liye /back bhejein.",
        "upload_success": "✅ File store ho gayi: <b>{name}</b> ({size})\n\nAb ye file har forwarded message ke sath apne aap attach hogi.",
        "upload_too_big": "⚠️ File {max} MB limit se badi hai.",
        "upload_not_platinum": "🔒 File upload Platinum feature hai. Use karne ke liye upgrade karo.",
        "upload_no_channel": "⚠️ File storage abhi configure nahi hua hai. Admin se baat karein.",
        "upload_replace_confirm": "⚠️ Aapki ek file already uploaded hai: <b>{old_name}</b>\n\nIse <b>{new_name}</b> se replace karein? Ek time me sirf ek hi file rakh sakte ho — purani file delete ho jayegi.",

        # ---------- HELP / MISC ----------
        "help_intro": "📖 <b>Help</b> — Neeche di gayi commands try karo:\n\n{commands}\n\nKisi command par tap karo ya command bhejo, uske features dikhenge.",
        "help_cat_setting": "General bot settings",
        "help_cat_forwarding": "Forwarding se related sab settings",
        "help_cat_filters": "Filters aur replacements manage karo",
        "help_cat_media": "Media aur watermark controls",
        "help_configure_hint": "Feature configure karne ke liye tap karo. 🔒 features ke liye upgrade karna hoga.",
        "back_to_help": "◀️ Help Par Wapas",
        "open_settings_btn": "⚙️ Settings Kholein",
        "view_plans_btn": "💎 Plans Dekhein",
        "language_current": "🌐 Abhi ki bhasha: <b>{current}</b>",
        "language_confirm_title": "🌐 <b>Language Badlein?</b>\n\n<b>{new}</b> par switch karein?",
        "language_confirm_yes": "✅ Haan, badlein",
        "language_confirm_no": "✖️ Cancel",
        "support_intro": "📞 <b>Support</b>\n\nChinta na karein — zyada tar problems (payment, setup, task errors) jaldi solve ho jati hain.\n\nNeeche button dabakar support team se baat karein. Hum 24 ghante me reply karte hain.",
        "updates_intro": "📢 <b>Updates Channel</b>\n\nIs channel par milta hai:\n• Naye features\n• Important updates\n• Saare DealsKoti bots ki announcements\n\nBot use karne ke liye joined rehna zaroori hai.",
        "refer_intro": "🎁 <b>Refer &amp; Earn</b>\n\nApna referral link dosto ke sath share karo:\n{link}\n\nAapke link se jude users: <b>{count}</b>\n\nReferral rewards admin payout se process hote hain.",
        "broadcast_preview_title": "📣 <b>Broadcast Preview</b>",
        "broadcast_sending": "📣 {count} users ko bhej raha hai…",
        "broadcast_done": "✅ Broadcast complete\nSent: {sent}\nFailed: {failed}\nBlocked: {blocked}",
    },
}


# ==========================================
# BUTTON LABELS (shared, not per-language)
# ==========================================
# Short button captions that read the same in both languages. Keeping them
# here stops the same emoji+word pair being retyped across main.py.

BTN = {
    "back": "◀️ Back",
    "home": "🏠 Home",
    "cancel": "✖️ Cancel",
    "clear": "🗑️ Clear",
    "on": "✅ ON",
    "off": "❌ OFF",
    "save": "💾 Save",
    "pay_inr": "💷 Pay with UPI / Card",
    "pay_usdt": "🪙 Pay with USDT",
    "pay_stars": "⭐ Pay with Telegram Stars",
    "approve": "✅ Approve",
    "reject": "❌ Reject",
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
    """Generates the command help list for admins (English only, internal)."""
    lines = []
    for cmd, desc in ADMIN_COMMANDS:
        lines.append(f"{cmd} - {desc}")
    return "\n".join(lines)
