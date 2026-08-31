from dataclasses import dataclass


@dataclass
class FAQItem:
    question: str
    answer: str


# 15 FAQs per language. main.py paginates them 5 per page automatically.
# Keep both lists the same length and in the same order — the callback stores
# the index, so a mismatch would show the wrong answer in the other language.
FAQS: dict[str, list[FAQItem]] = {
    "en": [
        FAQItem(
            "What is DealsKoti Auto Forwarder Bot?",
            "It copies posts from your chosen source channels to your own channels automatically, in real time. Connect your account, create a task, pick a source and a destination — forwarding starts instantly and runs 24/7.",
        ),
        FAQItem(
            "How do I connect my account? Is it safe?",
            "Send /connect, then your phone number with country code (example: +919876543210, no spaces). Telegram sends you a code — enter it here as PIN12345. Your session is encrypted before it is stored, and your OTP and 2FA password are never saved. Your 2FA password is deleted from the chat the moment you send it.",
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
            "When the source author edits their post, your copy is updated too. Telegram only allows edits for 48 hours, so older posts stay as they were. It is a toggle on Gold and automatic on Platinum.",
        ),
        FAQItem(
            "What is Auto Reaction?",
            "Your account automatically reacts with an emoji you choose — either on the source post or on your forwarded copy. If a channel does not allow that emoji, it is skipped quietly and forwarding is unaffected.",
        ),
        FAQItem(
            "How do the daily message limits work?",
            "Free gives 100 messages a day, Silver 500, Gold 2000, and Platinum is unlimited. The counter resets at midnight. You get one notification the day you hit the limit, and messages resume automatically the next day.",
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
    ],
    "hinglish": [
        FAQItem(
            "DealsKoti Auto Forwarder Bot kya hai?",
            "Ye aapke chune hue source channels ki posts apne channels me apne aap copy karta hai, real time me. Account connect karo, task banao, source aur destination chuno — forwarding turant shuru ho jaati hai aur 24/7 chalti hai.",
        ),
        FAQItem(
            "Account kaise connect karein? Kya ye safe hai?",
            "/connect bhejein, phir apna phone number country code ke saath (jaise: +919876543210, bina space). Telegram aapko code bhejega — use yahan PIN12345 format me daalein. Aapka session encrypt karke store hota hai, aur OTP ya 2FA password kabhi save nahi hota. 2FA password to bhejte hi chat se delete ho jaata hai.",
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
            "Jab source wala apni post edit karta hai, aapki copy bhi update ho jaati hai. Telegram sirf 48 ghante tak edit allow karta hai, isliye purani posts waisi hi rehti hain. Gold pe ye toggle hai, Platinum pe automatic.",
        ),
        FAQItem(
            "Auto Reaction kya hai?",
            "Aapka account apne aap aapke chune emoji se reaction karta hai — ya to source post pe, ya aapki forward ki hui copy pe. Agar koi channel wo emoji allow na kare to chup-chaap skip ho jaata hai, forwarding pe koi asar nahi.",
        ),
        FAQItem(
            "Daily message limit kaise kaam karti hai?",
            "Free me 100 messages roz, Silver me 500, Gold me 2000, aur Platinum me unlimited. Counter raat 12 baje reset hota hai. Jis din limit khatam hoti hai us din ek notification aata hai, aur agle din messages apne aap chalu ho jaate hain.",
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
    ],
}
