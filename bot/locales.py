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
        "login_2fa": "  **Two-Step Verification (Cloud Password)**\n\nYour account has an extra password enabled (this is <b>not</b> your account password).\n\n<b>What to enter:</b>\n• The 'Cloud Password' or 'Two-Step Verification' password you set in Telegram Settings → Privacy → Two-Step Verification.\n• This is <b>different</b> from your SMS login code.\n\n<b>If you forgot it:</b>\n1. Open Telegram on your phone\n2. Go to Settings → Privacy → Two-Step Verification\n3. Tap 'Forgot password?' to reset it\n4. Come back here and use /connect to retry\n\nPlease enter your 2FA password below:",
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
        # ===== PHASE 2 NEW KEYS =====
        "settings_select_task": "⚙️ <b>Task Settings</b>\n\nSelect a task to configure:",
        "settings_no_tasks": "📋 You don't have any tasks yet. Create one to unlock settings.",
        "settings_main_title": "⚙️ <b>Settings for:</b> {task_name}\n\nChoose a category:",
        "settings_cat_messages": "💬 <b>Message Settings</b>\n\nCustomize the text that is forwarded:",
        "settings_cat_filters": "🔍 <b>Filters</b>\n\nControl which messages get forwarded:",
        "settings_cat_media": " ️ <b>Media Settings</b>\n\nControl media handling:",
        "settings_cat_forwarding": "🚀 <b>Forwarding Settings</b>\n\nControl forwarding behavior:",
        "settings_cat_senderfilter": "👤 <b>Sender Filter</b>\n\nAllow only specific senders (Platinum):",
        "feature_locked": "  <b>Feature Locked</b>\n\n<b>{feature}</b> is available on the <b>{required_plan}</b> plan.\n\nUpgrade to unlock this feature.",
        "feature_locked_short": "🔒 Locked — requires {required_plan}",
        "header_prompt": "✏️ Send the new <b>Header</b> text. A short line added to the top of every forwarded message.\n\nSend /clear to remove.\nSend /back to cancel.",
        "footer_prompt": "✏️ Send the new <b>Footer</b> text. A short line added to the bottom of every forwarded message.\n\nSend /clear to remove.\nSend /back to cancel.",
        "replace_prompt": " ️ Send replacement rules.\n\nFormat: <code>old word=>new word</code>\nExamples:\n• <code>sale=>SALE</code>\n• <code>amazon=>Amazon</code>\n\nSeparate multiple rules with commas.\nSend /clear to remove all.\nSend /back to cancel.",
        "blacklist_prompt": "🔍 Send words to <b>block</b>. Messages containing these words will be skipped.\n\nFormat: <code>spam, scam, ad</code>\nSend /clear to remove all.\nSend /back to cancel.",
        "whitelist_prompt": "🔍 Send words to <b>require</b>. Only messages containing at least one of these words will be forwarded (leave empty to allow all).\n\nFormat: <code>deal, offer, free</code>\nSend /clear to remove.\nSend /back to cancel.",
        "autodelete_prompt": "🗑️ Send the number of seconds after which forwarded messages should be auto-deleted from the destination.\n\nExample: <code>3600</code> (1 hour)\nSend <code>0</code> or /clear to turn off.\nSend /back to cancel.",
        "userfilter_prompt": "👤 Send the numeric Telegram user IDs allowed to send through this task.\n\nFormat: <code>123456789, 987654321</code>\nSend /clear to allow everyone.\nSend /back to cancel.",
        "watermark_text_prompt": "💧 Send the watermark text that will be drawn on forwarded images (Platinum-only).\n\nExample: <code>@YourChannel</code>\nSend /clear to reset to default.\nSend /back to cancel.",
        "watermark_on": "✅ Watermark: <b>On</b>",
        "watermark_off": "❌ Watermark: <b>Off</b>",
        "editsync_on": "✅ Edit Sync: <b>On</b>",
        "editsync_off": "❌ Edit Sync: <b>Off</b>",
        "setting_saved": "✅ <b>{feature}</b> saved.",
        "setting_cleared": "✅ <b>{feature}</b> cleared.",
        "setting_invalid_number": "⚠️ Please send a valid number.",
        "setting_invalid_ids": "⚠️ Please send only numeric Telegram IDs separated by commas.",
        "setting_invalid_replace": "⚠️ Invalid format. Use <code>old=>new</code> separated by commas.",
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
        "updates_intro": "  <b>Updates Channel</b>\n\nThis channel posts:\n• New features\n• Important changes\n• Announcements for all DealsKoti bots\n\nYou must stay joined to use the bot.",
        "plan_benefits_free": "1 task · 1 source · 1 destination · 50 messages/day · Basic forwarding · Standard priority · Forwarded tag visible",
        "plan_benefits_silver": "2 tasks · 1 source · 1 destination · 200 messages/day · Standard priority · Header · Footer · Clean copy",
        "plan_benefits_gold": "5 tasks · 3 sources · 3 destinations · 500 messages/day · Priority forwarding · Header · Footer · Blacklist · Whitelist · Text Replacement · Clean copy",
        "plan_benefits_platinum": "10 tasks · 10 sources · 10 destinations · No daily cap · Priority · Header · Footer · Blacklist · Whitelist · Replace · Watermark · Auto Delete · Edit Sync · Sender Filter · Clean copy",
        "cycle_explainer": "🗓️ <b>Billing Cycles</b>\n\n• <b>Weekly</b>: 7 days\n• <b>Monthly</b>: 30 days\n• <b>Yearly</b>: 365 days with 20% off\n\nFinal payable amount is calculated server-side and shown before payment.",
        "next_scheduled_plan": "📅 <b>Scheduled:</b> {plan} starts on {date}",
        "expired_notice": "⏳ Your {plan} plan expired. Renew from /plans.",
        "downgrade_scheduled_notice": "📅 Your current plan stays active until {date}. Then it will switch to {scheduled}.",
        "validation_invalid": "⚠️ Invalid input. Please try again or send /back.",
        "disconnect_confirm": "⚠️ <b>Disconnect Telegram?</b>\n\nYour session will be removed and tasks will be paused. Your subscription and data are kept safely.",
        "disconnect_done": "✅ Telegram session disconnected.",
        "delete_confirm": "⚠️ Delete task <b>{name}</b> permanently?",
        "broadcast_preview_title": "📣 <b>Broadcast Preview</b>",
        "broadcast_sending": "  Sending to {count} users…",
        "broadcast_done": "✅ Broadcast complete\nSent: {sent}\nFailed: {failed}\nBlocked: {blocked}",
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
        "login_2fa": "🔒 **Two-Step Verification (Cloud Password)**\n\nAapke account par ek extra password hai (ye aapke account password se <b>alag</b> hai).\n\n<b>Kya daalna hai:</b>\n• Telegram Settings → Privacy → Two-Step Verification me jo 'Cloud Password' set kiya hai wahi.\n• Ye aapke SMS login code se <b>different</b> hai.\n\n<b>Agar bhool gaye:</b>\n1. Phone me Telegram kholein\n2. Settings → Privacy → Two-Step Verification par jayein\n3. 'Forgot password?' dabayein aur reset karein\n4. Wapas yahan aakar /connect se retry karein\n\nNeeche apna 2FA password daalein:",
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
        "replace_prompt": " ️ Replacement rules bhejein.\n\nFormat: <code>purana=>naya</code>\nExamples:\n• <code>sale=>SALE</code>\n• <code>amazon=>Amazon</code>\n\nMultiple rules ke liye comma lagayein.\nSab hatane ke liye /clear.\nCancel ke liye /back.",
        "blacklist_prompt": "  Words bhejein jo <b>block</b> karne hain. In words wale messages skip ho jayenge.\n\nFormat: <code>spam, scam, ad</code>\nSab hatane ke liye /clear.\nCancel ke liye /back.",
        "whitelist_prompt": "🔍 Words bhejein jo <b>required</b> hain. Sirf in me se koi ek word hone wale messages forward honge (khali chhodein toh sab allowed).\n\nFormat: <code>deal, offer, free</code>\nHatane ke liye /clear.\nCancel ke liye /back.",
        "autodelete_prompt": "🗑️ Seconds ki sankhya bhejein. Utne seconds baad message destination se auto-delete ho jayega.\n\nExample: <code>3600</code> (1 ghanta)\nBand karne ke liye <code>0</code> ya /clear.\nCancel ke liye /back.",
        "userfilter_prompt": "👤 Numeric Telegram user IDs bhejein jinhe is task me forward karne ki anumati hai.\n\nFormat: <code>123456789, 987654321</code>\nSab allow karne ke liye /clear.\nCancel ke liye /back.",
        "watermark_text_prompt": "💧 Watermark text bhejein jo forwarded images ke neeche draw hoga (sirf Platinum).\n\nExample: <code>@AapkaChannel</code>\nDefault par reset karne ke liye /clear.\nCancel ke liye /back.",
        "watermark_on": "✅ Watermark: <b>On</b>",
        "watermark_off": "❌ Watermark: <b>Off</b>",
        "editsync_on": "✅ Edit Sync: <b>On</b>",
        "editsync_off": "❌ Edit Sync: <b>Off</b>",
        "setting_saved": "✅ <b>{feature}</b> save ho gaya.",
        "setting_cleared": "✅ <b>{feature}</b> hata diya gaya.",
        "setting_invalid_number": "⚠️ Kripya ek valid number bhejein.",
        "setting_invalid_ids": "⚠️ Kripya sirf numeric Telegram IDs comma se alag karke bhejein.",
        "setting_invalid_replace": "⚠️ Galat format. <code>purana=>naya</code> comma se alag karke bhejein.",
        "tasks_summary": "📋 <b>Aapke Tasks</b>\n\nPlan: {plan}\nBanaye: {created}\nBache: {remaining}\nTotal allowed: {total}",
        "task_renamed": "✅ Task ka naam <b>{name}</b> ho gaya.",
        "task_rename_prompt": "✏️ Task ke liye naya naam bhejein (max 120 characters).",
        "rename_empty": " ️ Naam khali nahi ho sakta.",
        "source_list_title": "  <b>Source Chat Chunein</b>\n\nList se chat pick karein, ya desired chat se koi bhi message forward karein, ya public username/ID type karein.\n\nTip: Chat ko temporarily pin kar lein taaki asani se mile.",
        "dest_list_title": "📤 <b>Destination Chat Chunein</b>\n\nList se chat pick karein, ya desired chat se koi bhi message forward karein, ya public username/ID type karein.",
        "chat_page": "Page {page} / {pages}",
        "no_chats_found": "⚠️ Aapke account par abhi koi chat nahi mili. Koi message forward karein ya public @username type karein.",
        "language_current": "🌐 Abhi ki bhasha: <b>{current}</b>",
        "language_confirm_title": "🌐 <b>Language Badlein?</b>\n\n<b>{new}</b> par switch karein?",
        "language_confirm_yes": "✅ Haan, badlein",
        "language_confirm_no": "✖️ Cancel",
        "support_intro": "📞 <b>Support</b>\n\nChinta na karein — zyada tar problems (payment, setup, task errors) jaldi solve ho jati hain.\n\nNeeche button dabakar support team se baat karein. Hum 24 ghante me reply karte hain.",
        "updates_intro": "📢 <b>Updates Channel</b>\n\nIs channel par milta hai:\n• Naye features\n• Important updates\n• Saare DealsKoti bots ki announcements\n\nBot use karne ke liye joined rehna zaroori hai.",
        "plan_benefits_free": "1 task · 1 source · 1 destination · 50 msg/day · Basic forwarding · Standard priority · Forwarded tag visible",
        "plan_benefits_silver": "2 tasks · 1 source · 1 destination · 200 msg/day · Standard priority · Header · Footer · Clean copy",
        "plan_benefits_gold": "5 tasks · 3 sources · 3 destinations · 500 msg/day · Priority · Header · Footer · Blacklist · Whitelist · Replace · Clean copy",
        "plan_benefits_platinum": "10 tasks · 10 sources · 10 destinations · No daily cap · Priority · Header · Footer · Blacklist · Whitelist · Replace · Watermark · Auto Delete · Edit Sync · Sender Filter · Clean copy",
        "cycle_explainer": "🗓️ <b>Billing Cycles</b>\n\n• <b>Weekly</b>: 7 din\n• <b>Monthly</b>: 30 din\n• <b>Yearly</b>: 365 din aur 20% off\n\nFinal payable amount server-side calculate hoke payment se pehle dikhaya jata hai.",
        "next_scheduled_plan": "📅 <b>Scheduled:</b> {plan} {date} se shuru",
        "expired_notice": "  Aapka {plan} plan expire ho gaya. /plans se renew karein.",
        "downgrade_scheduled_notice": "📅 Aapka current plan {date} tak active rahega. Phir {scheduled} par switch hoga.",
        "validation_invalid": "⚠️ Galat input. Kripya dobara try karein ya /back bhejein.",
        "disconnect_confirm": "⚠️ <b>Telegram Disconnect Karein?</b>\n\nAapka session remove ho jayega aur tasks pause ho jayenge. Subscription aur data safe hain.",
        "disconnect_done": "✅ Telegram session disconnect ho gaya.",
        "delete_confirm": "⚠️ Task <b>{name}</b> hamesha ke liye delete karein?",
        "broadcast_preview_title": "📣 <b>Broadcast Preview</b>",
        "broadcast_sending": "📣 {count} users ko bhej raha hai…",
        "broadcast_done": "✅ Broadcast complete\nSent: {sent}\nFailed: {failed}\nBlocked: {blocked}",
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
