from dataclasses import dataclass

@dataclass
class FAQItem:
    question: str
    answer: str

# 15 FAQs per language. main.py paginates them 5 per page automatically.
FAQS: dict[str, list[FAQItem]] = {
    "en": [
        FAQItem(
            "What is Dealskoti Auto Forwarder Bot?",
            "It is a Telegram bot that automatically copies messages from your selected source channels/groups to your destination channels/groups in real time. Connect your account, create a task, pick source and destination — forwarding starts instantly.",
        ),
        FAQItem(
            "How do I connect my Telegram account? Is it safe?",
            "Tap 'Connect Account' or send /connect, then send your phone number with country code (example for India: +919876543210, no spaces). You will receive an OTP in Telegram — enter it here. Your session is encrypted and stored securely; we never see or store your OTP or 2FA password.",
        ),
        FAQItem(
            "How do I create my first forwarding task?",
            "Send /newtask, give the task a name, then select a Source chat and Destination chat. You can pick chats from the list of your recent 20 chats, forward any message from the desired chat, or type its @username. Tip: pin a chat in Telegram so it appears at the top of the list.",
        ),
        FAQItem(
            "What plans are available and what do they include?",
            "Free: 1 task, 1 source + 1 destination, 50 messages/day. Silver (₹149/$1.5): 3 tasks, 5+5, 200/day, clean copy, header/footer, delay timer, anti-ban speed. Gold (₹500/$5): 5 tasks, 10+10, 1000/day, plus blacklist/whitelist and hidden-link removal. Platinum (₹1000/$10): 10 tasks, 15+15, unlimited messages, replace rules, watermark, auto-delete, live edit sync, file attach and VIP support.",
        ),
        FAQItem(
            "Which payment methods are supported?",
            "Two methods: (1) INR via Razorpay — pay by UPI/card/netbanking through the secure checkout link, activation is automatic. (2) USDT (TRC20) — choose USDT at checkout, send the exact amount to the shown wallet, then submit your TXID. An admin verifies it and your plan activates. Weekly/monthly/yearly cycles are available for both methods.",
        ),
        FAQItem(
            "Why do Free plan messages show a 'Forwarded from' tag?",
            "Free plan uses Telegram's native forward, so the original source attribution stays visible. Silver and higher plans use clean copy mode — no forwarded tag, no bot watermark — messages look like they were posted directly by you.",
        ),
        FAQItem(
            "What are Header and Footer text?",
            "Header is a line added above every forwarded message; Footer is added below it. You can set them per task from Settings → Filters &amp; Replacements. Use them for credits, promo lines, or branding.",
        ),
        FAQItem(
            "What do Blacklist and Whitelist filters do?",
            "Blacklist: messages containing any blacklisted word are skipped. Whitelist: only messages containing at least one whitelisted word are forwarded. Both are Gold &amp; Platinum features, configurable per task from Settings → Filters &amp; Replacements.",
        ),
        FAQItem(
            "What is the Delay Timer and Anti-Ban Speed?",
            "Both control forwarding speed so your account stays safe. Delay Timer waits between sending to each destination (Off/Slow/Normal/Fast). Anti-Ban Speed adds a safe gap between every forwarded message (Slow/Normal/Fast). Available from Silver upward.",
        ),
        FAQItem(
            "What is Live Edit Sync?",
            "A Platinum feature that works automatically — no toggle needed. If the source message is edited after forwarding, your destination copies are updated automatically to match.",
        ),
        FAQItem(
            "How does Auto Delete work?",
            "Platinum feature. Set a number of seconds in Settings → Media; every forwarded message is deleted from the destination automatically after that time. Send 0 or /clear to turn it off.",
        ),
        FAQItem(
            "What is the File Upload feature (/upload_file)?",
            "Platinum members can upload a file (like an APK) via /upload_file. Then: (1) attach it to every forwarded message automatically, and/or (2) when a source message contains a file of the same type (.apk etc.), it gets replaced with your uploaded file. Both options have ON/OFF toggles in Settings.",
        ),
        FAQItem(
            "Why did my forwarding stop working?",
            "Most common reasons: daily message limit reached (check /account), you left the updates channel (tasks pause automatically — rejoin and resume), plan expired, or all tasks paused. Check /tasks to resume and /account for limits.",
        ),
        FAQItem(
            "How does the referral system work?",
            "Tap 'Refer' in the main menu to get your personal link. When someone starts the bot through your link, they are recorded as your referral. Referral rewards are paid out manually by the admin — contact support for payout status.",
        ),
        FAQItem(
            "How do I contact support?",
            "Tap 'Support' in the main menu or send /support — you will get our support channel link. The team usually replies within 24 hours. For payment issues, keep your TXID or Razorpay payment ID ready.",
        ),
    ],
    "hinglish": [
        FAQItem(
            "Dealskoti Auto Forwarder Bot kya hai?",
            "Ye ek Telegram bot hai jo aapke selected source channels/groups ke messages ko real time me aapke destination channels/groups tak apne aap pahunchata hai. Account connect karo, task banao, source aur destination chuno — forwarding turant shuru.",
        ),
        FAQItem(
            "Apna Telegram account kaise connect karu? Kya ye safe hai?",
            "'Connect Account' dabao ya /connect bhejo, phir apna phone number country code ke sath bhejo (India ka example: +919876543210, bina space). Telegram me OTP aayega — use yahan daalo. Aapka session encrypted aur safe store hota hai; humara bot aapka OTP ya 2FA password kabhi nahi dekhta.",
        ),
        FAQItem(
            "Pehla forwarding task kaise banau?",
            "/newtask bhejo, task ko naam do, phir Source chat aur Destination chat select karo. Aap apke recent 20 chats ki list se chun sakte ho, koi bhi message forward kar sakte ho, ya @username type kar sakte ho. Tip: Telegram me chat ko pin karo taaki wo list me sabse upar dikhe.",
        ),
        FAQItem(
            "Kaun se plans hain aur unme kya milta hai?",
            "Free: 1 task, 1 source + 1 destination, 50 msg/day. Silver (₹149/$1.5): 3 tasks, 5+5, 200/day, clean copy, header/footer, delay timer, anti-ban speed. Gold (₹500/$5): 5 tasks, 10+10, 1000/day, plus blacklist/whitelist aur hidden links removal. Platinum (₹1000/$10): 10 tasks, 15+15, unlimited messages, replace rules, watermark, auto-delete, live edit sync, file attach aur VIP support.",
        ),
        FAQItem(
            "Payment ke liye kaun se tarike hain?",
            "Do tarike: (1) INR — Razorpay se UPI/card/netbanking, payment hote hi plan automatic activate. (2) USDT (TRC20) — checkout me USDT chuno, bataye gaye wallet par exact amount bhejo, phir apna TXID submit karo. Admin verify karega aur plan activate ho jayega. Dono me weekly/monthly/yearly cycles available hain.",
        ),
        FAQItem(
            "Free plan ke messages me 'Forwarded from' tag kyu dikhta hai?",
            "Free plan Telegram ka native forward use karta hai, isliye original source ka tag dikhta hai. Silver aur upar ke plans clean copy mode use karte hain — na forwarded tag, na bot watermark — message seedha aapki taraf se post hua dikhta hai.",
        ),
        FAQItem(
            "Header aur Footer text kya hote hain?",
            "Header har forwarded message ke upar add hota hai, Footer neeche. Settings → Filters &amp; Replacements me per task set kar sakte ho. Credits, promo line ya branding ke liye use karo.",
        ),
        FAQItem(
            "Blacklist aur Whitelist filters kya karte hain?",
            "Blacklist: jis message me blacklist ka word ho, wo skip ho jata hai. Whitelist: sirf wahi messages forward hote hain jinme whitelist ka koi word ho. Dono Gold aur Platinum features hain, Settings → Filters &amp; Replacements me per task set hote hain.",
        ),
        FAQItem(
            "Delay Timer aur Anti-Ban Speed kya hai?",
            "Dono forwarding ki speed control karte hain taaki aapka account safe rahe. Delay Timer har destination bhejne ke beech wait karta hai (Off/Slow/Normal/Fast). Anti-Ban Speed har forwarded message ke beech safe gap deta hai (Slow/Normal/Fast). Silver se upar available.",
        ),
        FAQItem(
            "Live Edit Sync kya hai?",
            "Ye Platinum ka automatic feature hai — koi toggle nahi hai. Agar source message forward hone ke baad edit hota hai, toh aapke destination wale copies bhi apne aap update ho jate hain.",
        ),
        FAQItem(
            "Auto Delete kaise kaam karta hai?",
            "Platinum feature. Settings → Media me seconds ka number set karo; har forwarded message utne time baad destination se apne aap delete ho jayega. Band karne ke liye 0 bhejo ya /clear.",
        ),
        FAQItem(
            "File Upload feature (/upload_file) kya hai?",
            "Platinum members /upload_file se file (jaise APK) upload kar sakte hain. Phir: (1) ye file har forwarded message ke sath attach hogi, aur/ya (2) jab source message me same type ki file (.apk etc.) aaye, wo aapki uploaded file se replace ho jayegi. Dono ke ON/OFF toggles Settings me hain.",
        ),
        FAQItem(
            "Mera forwarding ruk gaya, kya karu?",
            "Common reasons: daily limit poori ho gayi (/account me dekho), aap updates channel se bahar nikal gaye (tasks pause ho jate hain — wapas join karke resume karo), plan expire ho gaya, ya saare tasks paused hain. /tasks se resume karo aur /account se limits dekho.",
        ),
        FAQItem(
            "Referral system kaise kaam karta hai?",
            "Main menu me 'Refer' dabao, aapka personal link mil jayega. Jo bhi aapke link se bot start karega, wo aapka referral ban jayega. Referral rewards admin manually pay karta hai — payout ke liye support se baat karo.",
        ),
        FAQItem(
            "Support se baat kaise karu?",
            "Main menu me 'Support' dabao ya /support bhejo — support channel ka link milega. Team aam taur par 24 ghante me reply karti hai. Payment issue ke liye apna TXID ya Razorpay payment ID ready rakho.",
        ),
    ]
}
