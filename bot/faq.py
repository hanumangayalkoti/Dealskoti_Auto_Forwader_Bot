from dataclasses import dataclass

@dataclass
class FAQItem:
    question: str
    answer: str

# You can add or modify questions here. The main.py will automatically paginate them!
FAQS: dict[str, list[FAQItem]] = {
    "en": [
        FAQItem("What is DealsKoti Forwarder?", "It's a bot that automatically forwards messages from source channels to destination channels without any delay."),
        FAQItem("How many tasks can I create?", "Free users get 1 task. You can upgrade to Silver, Gold, or Platinum for more tasks, features, and limits."),
        FAQItem("What is Edit Sync?", "A Platinum-only feature. If the admin edits the original message in the source channel, the bot will automatically update the forwarded message in your destination."),
        FAQItem("Can I use multiple Telegram accounts?", "No, your subscription and tasks are tied directly to your connected Telegram account."),
        FAQItem("My forwarding stopped, what do I do?", "Check if your plan expired, or if you reached your daily limit. Use /account to verify your limits."),
        FAQItem("How does the Auto-Delete work?", "Available on Platinum. You can set a timer (in seconds). The bot will automatically delete the forwarded message from your destination chat after that time."),
    ],
    "hinglish": [
        FAQItem("DealsKoti Forwarder kya hai?", "Ye ek auto-forwarder bot hai jo aapke source channel se messages utha kar turant destination channel me bhejta hai."),
        FAQItem("Main kitne tasks bana sakta hu?", "Free plan me aap 1 task bana sakte hain. Zyada tasks aur features ke liye aap Silver, Gold ya Platinum me upgrade kar sakte hain."),
        FAQItem("Edit Sync kya hai?", "Ye Platinum plan ka special feature hai. Agar original message me koi edit hota hai, toh bot aapke destination channel me bhi us message ko apne aap update kar dega."),
        FAQItem("Kya main multiple accounts use kar sakta hu?", "Nahi, aapka plan aur tasks aapke connected Telegram account se jude hote hain."),
        FAQItem("Mera forwarding ruk gaya hai, main kya karu?", "Check karein ki kahin aapka plan expire toh nahi ho gaya ya daily limit cross toh nahi hui. /account command bhej kar status check karein."),
        FAQItem("Auto-Delete kaise kaam karta hai?", "Ye Platinum feature hai. Aap ek time (seconds me) set kar sakte hain, aur bot utne time baad us message ko aapke group se automatically delete kar dega."),
    ]
}
