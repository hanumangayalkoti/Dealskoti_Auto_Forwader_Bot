from __future__ import annotations

from dataclasses import dataclass

from .locales import Language


@dataclass(frozen=True)
class FAQ:
    question: str
    answer: str


_ENGLISH = (
    ("What does the bot do?", "It copies messages from an accessible Telegram source to a configured destination."),
    ("Why must I join the Updates Channel?", "The channel gate is required before bot use. Leaving later pauses access and forwarding, but does not cancel your plan or delete your data. Rejoin and press I've Joined."),
    ("How do I connect my Telegram account?", "Use /connect, enter your phone number with country code, then enter the Telegram code in PIN123 format. OTP and 2FA passwords are not stored."),
    ("Why must the OTP use PIN123 format?", "The wrapper avoids asking for a bare numeric code. Only the digits are extracted for the login attempt."),
    ("What happens if 2FA is enabled?", "The bot asks for the 2FA password only during login. It is never saved or shown to admins."),
    ("How do I set a source and destination?", "Use a public username or chat ID after creating a task. The connected account's access and destination permissions are validated."),
    ("Which Telegram entities are supported?", "Accessible public channels, groups, supergroups, users, and bots are supported."),
    ("Can I use private or restricted chats?", "Only entities the connected account can resolve and use are accepted. Restricted or inaccessible entities are rejected."),
    ("How do I manage a task?", "Use /newtask, /tasks, /pause, /resume, or /deletetask."),
    ("What are the plans and limits?", "Free: 1 task and 50 messages/day. Silver: 2 and 200. Gold: 5 and 500. Platinum: 10 and no normal product cap."),
    ("How do trials and billing cycles work?", "Each connected account gets one seven-day Silver-level trial. Weekly, monthly, and yearly cycles are supported."),
    ("How do first-order discounts work?", "The first paid order receives 40% off. Yearly pricing also has its 20% discount. Renewals do not receive the first-order discount."),
    ("How do Gold and Platinum forwarding work?", "They receive priority forwarding. Telegram FloodWait is respected with a wait and retry, and safety warnings are shown for unusual activity."),
    ("What are filters and watermarking?", "Plan settings control filters, headers, link replacement, watermarking, auto-delete, and live edit-sync. Image watermarking is Platinum-only."),
    ("How do referrals and privacy work?", "First paid orders generate a 50% verified-paid-amount commission. Disconnect removes only the encrypted session; retained product records are not deleted."),
)

_HINGLISH = (
    ("Bot kya karta hai?", "Ye accessible Telegram source se configured destination par messages ki copies bhejta hai."),
    ("Updates Channel join karna kyun zaroori hai?", "Gate required hai. Baad me channel leave karoge to access aur forwarding pause hogi, plan cancel ya data delete nahi hoga. Rejoin karke I've Joined dabao."),
    ("Telegram account kaise connect karun?", "/connect use karo, country code ke saath phone number do, phir code PIN123 format me bhejo. OTP aur 2FA password save nahi honge."),
    ("OTP PIN123 format me kyun bhejna hai?", "Bot bare numeric code nahi maangta. Login ke liye sirf digits extract hote hain."),
    ("2FA enabled ho to kya hoga?", "Login ke waqt 2FA password manga jayega. Ye save nahi hoga aur admin ko nahi dikhega."),
    ("Source aur destination kaise set karun?", "Task banane ke baad public username ya chat ID do. Connected account ka access aur destination permission verify hogi."),
    ("Kaunse Telegram entities supported hain?", "Accessible public channels, groups, supergroups, users aur bots supported hain."),
    ("Private ya restricted chats use kar sakta hoon?", "Sirf wahi entities accept hongi jinko connected account resolve aur use kar sakta hai."),
    ("Task kaise manage karun?", "/newtask, /tasks, /pause, /resume ya /deletetask use karo."),
    ("Plans aur limits kya hain?", "Free: 1 task/50 messages daily. Silver: 2/200. Gold: 5/500. Platinum: 10 aur normal product cap nahi."),
    ("Trial aur billing cycles kaise work karte hain?", "Har connected account ko ek baar seven-day Silver trial milega. Weekly, monthly aur yearly cycles available hain."),
    ("First-order discount ka rule kya hai?", "First paid order par 40% off milega. Yearly par 20% discount bhi rahega. Renewal par first-order discount nahi milega."),
    ("Gold aur Platinum kaise work karte hain?", "Inko priority forwarding milegi. FloodWait par required wait ke baad retry hoga aur unusual activity par safety warning milegi."),
    ("Filters aur watermark kya hain?", "Plan ke hisaab se filters, headers, links, watermark, auto-delete aur edit-sync milte hain. Image watermark Platinum-only hai."),
    ("Referral aur privacy kaise work karte hain?", "First paid order par verified paid amount ka 50% commission. Disconnect sirf encrypted session remove karta hai; product records safe rehte hain."),
)


FAQS: dict[Language, tuple[FAQ, ...]] = {
    "en": tuple(FAQ(*item) for item in _ENGLISH),
    "hinglish": tuple(FAQ(*item) for item in _HINGLISH),
}