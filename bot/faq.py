from dataclasses import dataclass


@dataclass
class FAQItem:
    question: str
    answer: str


# FAQs per language. main.py paginates them 5 per page automatically.
# Keep both lists the same length and in the same order — the callback stores
# the index, so a mismatch would show the wrong answer in the other language.
FAQS: dict[str, list[FAQItem]] = {
    "en": [
        FAQItem(
            "What is DealKoti Auto Forwarder Bot?",
            "It copies posts from your chosen source channels to your own channels automatically, in real time. Connect your account, create a task, pick a source and a destination — forwarding starts instantly and runs 24/7.",
        ),
        FAQItem(
            "How do I connect my account? Is it safe?",
            "Send /connect and choose how to sign in. With your number: send it with country code (example: +919876543210), then enter the code Telegram sends you as PIN12345. With QR: scan the code from another phone, a laptop, or web.telegram.org — no number needed. Your session is encrypted before it is stored, your OTP and 2FA password are never saved, and the 2FA password is deleted from the chat the moment you send it.",
        ),
        FAQItem(
            "How do I create my first task?",
            "Send /newtask, give it a name, then pick your source channel and your destination channel from the numbered list. If a chat is not in the list, forward any message from it, or type its @username. Forwarding starts as soon as the task is created.",
        ),
        FAQItem(
            "Nothing is being forwarded. What do I check?",
            "Open /mystats first — it shows when each task last forwarded something. If a task says 'last never', the source is usually wrong or your account is not a member of it. Also check the task is not paused, your daily limit is not used up, and that you can actually post in the destination.",
        ),
        FAQItem(
            "What is the Code Filter?",
            "It forwards ONLY the gift or coupon code from a post and drops everything else. Channels hide codes in two formats, so pick the one your source uses: Monospace, Spoiler (the hidden text with shimmering dots), or Both. Posts with no code are skipped completely.",
        ),
        FAQItem(
            "What do Remove Links and Remove Usernames do?",
            "They strip every URL or every @handle out of the forwarded text. If you want to swap them for your own instead of deleting them, use Replace Links or Replace Usernames. Your header and footer are never touched by these.",
        ),
        FAQItem(
            "What is Topics Forwarding?",
            "Some groups are forums split into topics. This lets you forward from only the topics you choose instead of the whole group. Select nothing and every topic is forwarded.",
        ),
        FAQItem(
            "What is Post Edit Sync?",
            "When the source author edits their post, your copy is updated too. Telegram only allows edits for 48 hours, so older posts stay as they were. It is an ON/OFF setting on Gold (off by default) and on by default on Platinum. Posts with Inline Buttons are updated as well, and keep their buttons.",
        ),
        FAQItem(
            "What is Auto Reaction?",
            "Your account automatically reacts with an emoji you choose — either on the source post or on your forwarded copy. If a channel does not allow that emoji, it is skipped quietly and forwarding is unaffected.",
        ),
        FAQItem(
            "How do the daily message limits work?",
            "Daily limits: Free 50 messages, Basic 100, Silver 1,500, Gold 3,000, Platinum unlimited. The limit is for ALL your tasks together, not per task. One post counts as 1 however many channels it goes to, and an album counts as 1 too. The counter resets at midnight (12:00 AM IST) every day. You are warned at 80%, told once when you hit the limit, and forwarding resumes by itself after the reset.",
        ),
        FAQItem(
            "How do I pay, and how fast does my plan activate?",
            "UPI and card payments through Razorpay activate automatically within seconds. USDT and Telegram Stars are verified by an admin first — submit your transaction ID or screenshot and you will be notified once approved.",
        ),
        FAQItem(
            "What happens if I upgrade mid-plan?",
            "Nothing is lost. The unused value of your current plan is converted into extra days on the new one. Downgrading is different — your current plan runs to its expiry date, then the lower plan starts.",
        ),
        FAQItem(
            "How does Refer & Earn work?",
            "Share your referral link. When someone joins through it and buys any plan, you earn 20% of every payment they make — not just the first one, for as long as they keep paying. Your earnings are shown on the Refer & Earn screen.",
        ),
        FAQItem(
            "Will my account get banned?",
            "The bot uses your own account, so normal Telegram limits apply. Anti-Ban Speed and the per-target Delay Timer exist to space out sending and keep it looking natural. Forwarding to a very large number of destinations very fast is the main risk — go slower if you are unsure.",
        ),
        FAQItem(
            "What happens if I disconnect my account?",
            "Your session is removed and all forwarding stops immediately. Your tasks, settings and subscription are kept safely — reconnect with /connect and everything resumes exactly where it was.",
        ),
        FAQItem(
            "One post goes to 5 of my channels — how many messages is that?",
            "One. A post counts once per task, however many channels it is delivered to. So on Silver (1,500 a day) you can forward 1,500 posts a day, even to 5 channels each. Albums also count as 1, however many photos they have. If the same source is used in two tasks, each task counts its own copy.",
        ),
        FAQItem(
            "What is Reply Sync?",
            "If a message in the source is a reply to an earlier post, Reply Sync sends it to your channel as a reply to YOUR copy of that post, exactly like the source. Turn it on per task: /settings → choose task → ↩️ Reply Sync. It is on Silver and above. Posts sent before you turned it on cannot be linked, so replies to those arrive as normal posts — the bot tells you when that happens.",
        ),
        FAQItem(
            "Why do some posts arrive without my Inline Buttons?",
            "Buttons can only be added by this bot, so the bot must be an admin in your destination channel. They are also left off when Telegram does not allow them: albums (several photos sent together), captions longer than 1024 characters, and files larger than 45 MB. The post itself always arrives — and the bot tells you once if buttons were skipped.",
        ),
        FAQItem(
            "Why did my account get disconnected?",
            "Only when Telegram itself ends the session — for example if you removed it from Telegram → Settings → Devices, or chose 'Terminate all other sessions'. Short network problems do NOT disconnect you; the bot reconnects on its own. If it does disconnect, you get a message: send /connect once. Your tasks, settings and plan are all kept.",
        ),
        FAQItem(
            "What happens when my plan expires?",
            "You move to the Free plan: 1 task, 1 source, 1 destination and 50 messages a day. Your extra tasks are paused, not deleted, and every setting is kept — renew any time with /plans and everything comes back. You get reminders 5, 3, 2 and 1 day before expiry.",
        ),
    ],
    "hinglish": [
        FAQItem(
            "DealKoti Auto Forwarder Bot kya hai?",
            "Ye aapke chune hue source channels ki posts apne channels me apne aap copy karta hai, real time me. Account connect karo, task banao, source aur destination chuno — forwarding turant shuru ho jaati hai aur 24/7 chalti hai.",
        ),
        FAQItem(
            "Account kaise connect karein? Kya ye safe hai?",
            "/connect bhejiye aur sign in ka tareeka chuniye. Number se: country code ke saath number bhejiye (jaise +919876543210), phir Telegram ka code PIN12345 ki tarah daaliye. QR se: doosre phone, laptop ya web.telegram.org se code scan kijiye — number ki zarurat nahi. Aapka session save hone se pehle encrypt hota hai, OTP aur 2FA password kabhi save nahi hote, aur 2FA password bhejte hi chat se delete ho jaata hai.",
        ),
        FAQItem(
            "Pehla task kaise banayein?",
            "/newtask bhejein, naam dein, phir numbered list me se source aur destination channel chunein. Agar koi chat list me na ho to usse koi message forward kar dein, ya uska @username type karein. Task banate hi forwarding shuru ho jaati hai.",
        ),
        FAQItem(
            "Kuch forward nahi ho raha, kya check karein?",
            "Pehle /mystats kholein — usme dikhta hai har task ne aakhri baar kab kuch forward kiya. Agar kisi task pe 'last never' likha hai to aksar source galat hai ya aapka account us channel me nahi hai. Ye bhi dekhein ki task paused to nahi, daily limit khatam to nahi, aur destination me post karne ki permission hai ya nahi.",
        ),
        FAQItem(
            "Code Filter kya hai?",
            "Ye post me se sirf gift ya coupon code forward karta hai, baaki sab hata deta hai. Channels code do formats me chhupate hain, apne source wala chunein: Monospace, Spoiler (chamakte dots wala chhupa text), ya Both. Jis post me code nahi hoga wo poora skip ho jayega.",
        ),
        FAQItem(
            "Remove Links aur Remove Usernames kya karte hain?",
            "Ye forwarded text me se har URL ya har @handle hata dete hain. Agar hatane ki jagah apna lagana hai to Replace Links ya Replace Usernames use karein. Aapka header aur footer inse kabhi nahi chhinta.",
        ),
        FAQItem(
            "Topics Forwarding kya hai?",
            "Kuch groups forum hote hain jo topics me bante hain. Isse aap poore group ki jagah sirf chune hue topics se forward kar sakte hain. Kuch na chunein to sab topics se forward hoga.",
        ),
        FAQItem(
            "Post Edit Sync kya hai?",
            "Jab source wala apni post edit karta hai, aapki copy bhi update ho jaati hai. Telegram sirf 48 ghante tak edit allow karta hai, isliye usse purani posts waisi hi rehti hain. Gold me ye ON/OFF setting hai (default OFF), aur Platinum me default ON. Inline Buttons wali posts bhi update hoti hain, aur unke buttons bache rehte hain.",
        ),
        FAQItem(
            "Auto Reaction kya hai?",
            "Aapka account apne aap aapke chune emoji se reaction karta hai — ya to source post pe, ya aapki forward ki hui copy pe. Agar koi channel wo emoji allow na kare to chup-chaap skip ho jaata hai, forwarding pe koi asar nahi.",
        ),
        FAQItem(
            "Daily message limit kaise kaam karti hai?",
            "Roz ki limit: Free 50 messages, Basic 100, Silver 1,500, Gold 3,000, Platinum unlimited. Ye limit aapke SAARE tasks milake hai, har task ki alag nahi. Ek post kitne bhi channels me jaaye, 1 hi gini jaati hai, aur album bhi 1 ginta hai. Counter roz raat 12 baje (IST) reset hota hai. 80% pe pehle chetavni milti hai, limit poori hone pe ek baar suchna aati hai, aur reset ke baad forwarding apne aap chalu ho jaati hai.",
        ),
        FAQItem(
            "Payment kaise karein, plan kitni jaldi activate hota hai?",
            "UPI aur card payment Razorpay se seconds me apne aap activate ho jaata hai. USDT aur Telegram Stars ko pehle admin verify karta hai — transaction ID ya screenshot bhejein, approve hote hi aapko notification mil jayega.",
        ),
        FAQItem(
            "Beech me upgrade karun to kya hoga?",
            "Kuch nahi jaata. Aapke current plan ka bacha hua paisa naye plan ke extra dino me badal jaata hai. Downgrade alag hai — current plan apni expiry tak chalta hai, uske baad chhota plan shuru hota hai.",
        ),
        FAQItem(
            "Refer & Earn kaise kaam karta hai?",
            "Apna referral link share karein. Jab koi uske through jud kar koi bhi plan kharide, to aapko unke har payment ka 20% milta hai — sirf pehle payment ka nahi, jab tak wo paisa dete rahenge. Aapki kamai Refer & Earn screen pe dikhti hai.",
        ),
        FAQItem(
            "Kya mera account ban ho sakta hai?",
            "Bot aapke apne account se chalta hai, isliye Telegram ki normal limits lagti hain. Anti-Ban Speed aur per-target Delay Timer isiliye hain ki messages fasle se jaayein aur natural lagein. Sabse bada risk hai bahut saare destinations pe bahut tezi se bhejna — shak ho to speed dheemi rakhein.",
        ),
        FAQItem(
            "Account disconnect karun to kya hoga?",
            "Aapka session hat jaata hai aur forwarding turant ruk jaati hai. Aapke tasks, settings aur subscription safe rehte hain — /connect se dobara connect karein aur sab wahin se chalu ho jaata hai.",
        ),
        FAQItem(
            "Ek post mere 5 channels me jaati hai — kitne messages gine jaayenge?",
            "Ek. Post kitne bhi channels me jaaye, har task me 1 hi gini jaati hai. To Silver (roz 1,500) par aap roz 1,500 posts forward kar sakte hain, chahe har post 5 channels me jaaye. Album me kitni bhi photos hon, 1 hi ginta hai. Agar same source do tasks me hai, to har task apni copy alag ginta hai.",
        ),
        FAQItem(
            "Reply Sync kya hai?",
            "Agar source me koi message kisi purani post ka reply hai, to Reply Sync use aapke channel me AAPKI us post ki copy ka reply banake bhejta hai, bilkul source jaisa. Har task me on kariye: /settings → task chunein → ↩️ Reply Sync. Ye Silver aur upar me hai. Setting on karne se pehle gayi posts ko joda nahi ja sakta, isliye unke reply normal post banke aate hain — aisa hone par bot aapko batata hai.",
        ),
        FAQItem(
            "Kuch posts mere Inline Buttons ke bina kyun aati hain?",
            "Buttons sirf ye bot laga sakta hai, isliye bot ka aapke destination channel me admin hona zaroori hai. Jahan Telegram allow nahi karta wahan bhi buttons nahi lagte: album (kai photos ek saath), 1024 characters se lamba caption, aur 45 MB se badi file. Post khud hamesha pahunchti hai — aur buttons chhoote to bot ek baar bata deta hai.",
        ),
        FAQItem(
            "Mera account disconnect kyun hua?",
            "Sirf tab, jab Telegram khud session khatam kare — jaise aapne Telegram → Settings → Devices se ise hata diya, ya 'Terminate all other sessions' chuna. Chhoti network problem se disconnect NAHI hota; bot khud dobara jud jaata hai. Agar disconnect hua, to aapko message aayega: bas ek baar /connect bhejiye. Aapke tasks, settings aur plan sab safe rehte hain.",
        ),
        FAQItem(
            "Mera plan khatam hone par kya hoga?",
            "Aap Free plan par aa jaate hain: 1 task, 1 source, 1 destination aur roz 50 messages. Aapke extra tasks pause hote hain, delete nahi, aur saari settings bachi rehti hain — /plans se kabhi bhi renew kariye, sab wapas aa jaayega. Plan khatam hone se 5, 3, 2 aur 1 din pehle reminder aata hai.",
        ),
    ],
}
