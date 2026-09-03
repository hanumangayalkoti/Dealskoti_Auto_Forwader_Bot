"""
Plan definitions, pricing and feature gating for the DealsKoti forwarder bot.

This module is the SINGLE SOURCE OF TRUTH for:
  * what each plan costs (INR / USDT / Telegram Stars)
  * what limits each plan has (tasks, sources, destinations, daily messages)
  * which features each plan unlocks  -> FEATURE_MATRIX / plan_has()
  * how each plan's feature list is DISPLAYED to the user -> PLAN_FEATURE_TREE

main.py and forwarding.py must never hardcode a plan name check like
`if plan == "platinum"`. Always ask `plan_has(plan_name, FEATURE_X)` instead,
so adding/moving a feature between tiers only ever needs editing THIS file.
"""

from dataclasses import dataclass

# ==========================================
# PLAN DATACLASS
# ==========================================

@dataclass
class Plan:
    name: str
    tasks: int
    sources_per_task: int
    destinations_per_task: int
    daily_messages: int | None   # None = unlimited
    monthly_rupees: int
    usdt_monthly_usd: float = 0.0
    tg_stars: int = 0            # Telegram Stars price for ONE month


# ==========================================
# PLAN DEFINITIONS
# ==========================================

PLANS: dict[str, Plan] = {
    "free": Plan(
        name="Free",
        tasks=1,
        sources_per_task=1,
        destinations_per_task=1,
        daily_messages=100,
        monthly_rupees=0,
        usdt_monthly_usd=0.0,
        tg_stars=0,
    ),
    "silver": Plan(
        name="Silver",
        tasks=5,
        sources_per_task=5,
        destinations_per_task=5,
        daily_messages=500,
        monthly_rupees=100,
        usdt_monthly_usd=2.0,
        tg_stars=130,
    ),
    "gold": Plan(
        name="Gold",
        tasks=10,
        sources_per_task=15,
        destinations_per_task=15,
        daily_messages=2000,
        monthly_rupees=400,
        usdt_monthly_usd=5.0,
        tg_stars=340,
    ),
    "platinum": Plan(
        name="Platinum",
        tasks=15,
        sources_per_task=50,
        destinations_per_task=50,
        daily_messages=None,     # unlimited
        monthly_rupees=999,
        usdt_monthly_usd=10.0,
        tg_stars=860,
    ),
}

# Rank order used for upgrade/downgrade maths. Higher = better plan.
PLAN_ORDER: list[str] = ["free", "silver", "gold", "platinum"]


# ==========================================
# FEATURE KEYS
# ==========================================
# These strings are what get stored in a task's `settings` JSONB and what
# forwarding.py checks at runtime. Never rename one without a migration.

F_AUTO_FORWARD          = "auto_forward"
F_HEADER                = "header"                   # add header text
F_FOOTER                = "footer"                   # add footer text
F_MEDIA                 = "media_forward"
F_LINK_PREVIEW          = "link_preview"             # ON/OFF toggle
F_AUTO_DELETE           = "auto_delete"
F_REMOVE_USERNAMES      = "remove_usernames"         # strip ALL @handles
F_REMOVE_LINKS          = "remove_links"             # strip ALL urls
F_MONO_TEXT             = "mono_text"                # wrap body in <code>
F_POST_EDIT_SYNC        = "post_edit_sync"           # mirror source edits
F_HIDDEN_LINKS          = "disable_hidden_links"     # unmask md hyperlinks
F_BLACKLIST             = "blacklist"
F_WHITELIST             = "whitelist"
F_REPLACE_USERNAMES     = "replace_usernames"
F_REPLACE_WORDS         = "replace_words"
F_TRIM_WORDS            = "trim_words"               # drop words / whole lines
F_REPLACE_LINKS         = "replace_links"
F_DELAY_TIMER           = "delay_timer"              # per-target delay
F_TOPICS                = "topics_forwarding"        # forum topic threads
F_NO_WATERMARK          = "no_bot_watermark"         # clean copy, no "fwd from"
F_WATERMARK_IMAGE       = "watermark_image"          # custom image watermark
F_WATERMARK_STYLE       = "watermark_style"          # position/size/opacity
F_ATTACH_FILE           = "attach_file"
F_AUTO_REACTION         = "auto_reaction"
F_SENDER_FILTER         = "sender_filter"
F_ADV_TEXT_REPLACE      = "advanced_text_replace"
F_ADV_LINK_REPLACE      = "advanced_link_replace"
F_PER_TARGET_HF         = "per_target_header_footer"
F_ANTIBAN               = "antiban_speed"
F_VIP_SUPPORT           = "vip_support"
F_FAST_DELIVERY         = "fast_delivery"


# ==========================================
# FEATURE MATRIX  (what each plan actually UNLOCKS)
# ==========================================
# Built cumulatively: every tier inherits everything below it, then adds its
# own. This makes it impossible to accidentally give Gold something Platinum
# doesn't have.

_FREE_FEATURES: set[str] = {
    F_AUTO_FORWARD,
    F_MEDIA,
}

_SILVER_ADDS: set[str] = {
    F_HEADER,
    F_FOOTER,
    F_LINK_PREVIEW,
    F_REMOVE_USERNAMES,
    F_REMOVE_LINKS,
    F_MONO_TEXT,
    F_HIDDEN_LINKS,
    F_BLACKLIST,
    F_WHITELIST,
    F_REPLACE_USERNAMES,
    F_REPLACE_WORDS,
    F_NO_WATERMARK,
    F_ANTIBAN,
    F_FAST_DELIVERY,
}

_GOLD_ADDS: set[str] = {
    F_AUTO_DELETE,
    F_POST_EDIT_SYNC,
    F_TRIM_WORDS,
    F_REPLACE_LINKS,
    F_DELAY_TIMER,
    F_TOPICS,
    F_VIP_SUPPORT,
}

_PLATINUM_ADDS: set[str] = {
    F_WATERMARK_IMAGE,
    F_WATERMARK_STYLE,
    F_ATTACH_FILE,
    F_AUTO_REACTION,
    F_SENDER_FILTER,
    F_ADV_TEXT_REPLACE,
    F_ADV_LINK_REPLACE,
    F_PER_TARGET_HF,
}

FEATURE_MATRIX: dict[str, set[str]] = {
    "free": set(_FREE_FEATURES),
    "silver": _FREE_FEATURES | _SILVER_ADDS,
    "gold": _FREE_FEATURES | _SILVER_ADDS | _GOLD_ADDS,
    "platinum": _FREE_FEATURES | _SILVER_ADDS | _GOLD_ADDS | _PLATINUM_ADDS,
}


def plan_has(plan_name: str, feature: str) -> bool:
    """The ONLY way code should ask 'can this plan do X?'."""
    return feature in FEATURE_MATRIX.get((plan_name or "free").lower(), _FREE_FEATURES)


def plan_rank(plan_name: str) -> int:
    """Position of a plan in PLAN_ORDER (free=0 ... platinum=3)."""
    try:
        return PLAN_ORDER.index((plan_name or "free").lower())
    except ValueError:
        return 0


def min_plan_for(feature: str) -> str:
    """Lowest plan that unlocks a feature — used for 'Upgrade to X' prompts."""
    for name in PLAN_ORDER:
        if feature in FEATURE_MATRIX.get(name, set()):
            return name
    return "platinum"


# ==========================================
# DISPLAY TREE  (what the user SEES on the plan detail screen)
# ==========================================
# Deliberately kept separate from FEATURE_MATRIX: the matrix is about code
# behaviour, this is marketing copy and its wording/order is fixed.
# "{limits}" and "{daily}" are filled in from PLANS at render time.

PLAN_FEATURE_TREE: dict[str, list[str]] = {
    "silver": [
        "{limits}",
        "Auto Forwarding",
        "Header Control",
        "Media Forwarding",
        "Link Preview ON/OFF",
        "Remove Usernames ON/OFF",
        "Remove Links ON/OFF",
        "Mono Text ON/OFF",
        "Disable Hidden Links ON/OFF",
        "Blacklist Keywords",
        "Whitelist Keywords",
        "Add Header Text",
        "Add Footer Text",
        "Replace Usernames",
        "Replace Words (Text)",
        "{daily}",
        "No BOT Watermark",
        "Anti-Ban Speed Forwarding",
        "Super Fast Message Delivery",
    ],
    "gold": [
        "{limits}",
        "Auto Forwarding",
        "Header & Footer Control",
        "Media Forwarding",
        "Link Preview ON/OFF",
        "Auto Delete Messages ON/OFF",
        "Remove Usernames ON/OFF",
        "Remove Links ON/OFF",
        "Mono Text ON/OFF",
        "Post Edit Sync ON/OFF",
        "Disable Hidden Links ON/OFF",
        "Blacklist Keywords",
        "Whitelist Keywords",
        "Replace Usernames",
        "Replace Words (Text)",
        "Trim Single Words/Lines",
        "Replace Links",
        "Delay Timer Per Target",
        "Topics Forwarding",
        "{daily}",
        "No BOT Watermark",
        "Anti-Ban Speed Forwarding",
        "Instant VIP Support",
        "Super Fast Message Delivery",
    ],
    "platinum": [
        "{limits}",
        "Auto Forwarding",
        "Header & Footer Control",
        "Media Forwarding",
        "Link Preview ON/OFF",
        "Auto Delete Messages ON/OFF",
        "Remove Usernames ON/OFF",
        "Remove Links ON/OFF",
        "Mono Text ON/OFF",
        "Automatic Post Edit Sync",
        "Disable Hidden Links ON/OFF",
        "Blacklist Keywords",
        "Whitelist Keywords",
        "Replace Usernames",
        "Replace Words (Text)",
        "Trim Single Words/Lines",
        "Replace Links",
        "Delay Timer Per Target",
        "Topics Forwarding",
        "{daily}",
        "Custom Image Watermark",
        "Watermark Position/Size/Opacity",
        "Attach Custom File",
        "Auto Reaction System",
        "Sender Filter",
        "Advanced Text Replacement",
        "Advanced Link Replacement",
        "Custom Header/Footer Per Target",
        "Anti-Ban Speed Forwarding",
        "Instant VIP Support",
        "Super Fast Message Delivery",
    ],
}


def daily_label(plan_name: str) -> str:
    plan = PLANS.get(plan_name)
    if plan is None or plan.daily_messages is None:
        return "Unlimited Messages/Day"
    return f"{plan.daily_messages} Messages/Day"


def limits_label(plan_name: str) -> str:
    plan = PLANS.get(plan_name)
    if plan is None:
        return "—"
    return f"{plan.sources_per_task} Sources + {plan.destinations_per_task} Targets"


def plan_feature_tree(plan_name: str) -> str:
    """Renders the ┌─ ├─ └─ feature tree exactly as shown on the plan screen."""
    plan_name = (plan_name or "free").lower()
    items = PLAN_FEATURE_TREE.get(plan_name)
    if not items:
        return ""
    rendered = [
        item.format(limits=limits_label(plan_name), daily=daily_label(plan_name))
        for item in items
    ]
    lines = [f"┌─{rendered[0]}"]
    lines += [f"├─{item}" for item in rendered[1:-1]]
    lines.append(f"└─{rendered[-1]}")
    return "\n".join(lines)


def plan_price_block(plan_name: str) -> str:
    """The 3 price lines (INR / USDT / Stars) shown above the feature tree."""
    plan = PLANS.get(plan_name)
    if plan is None or plan.monthly_rupees == 0:
        return ""
    lines = [f"💷 INR Price: ₹{plan.monthly_rupees}"]
    if plan.usdt_monthly_usd:
        lines.append(f"🪙 USDT Price: ${plan.usdt_monthly_usd:g}")
    if plan.tg_stars:
        lines.append(f"⭐ TG Stars: {plan.tg_stars}")
    return "\n".join(lines)


def plan_details_text(plan_name: str) -> str:
    """Full plan detail screen body: title + prices + feature tree."""
    plan_name = (plan_name or "free").lower()
    plan = PLANS.get(plan_name)
    if plan is None:
        return "Unknown plan."

    if plan_name == "free":
        return (
            "🆓 <b>Free Plan</b>\n"
            "━━━━━━━━━━━━━━\n"
            "💷 Price: ₹0 (Always free)\n"
            f"┌─{plan.tasks} Task, {plan.sources_per_task} Source, {plan.destinations_per_task} Target\n"
            "├─Auto Forwarding\n"
            "├─Media Forwarding\n"
            f"└─{plan.daily_messages} Messages/Day\n\n"
            "💡 Upgrade any time for filters, header/footer, "
            "text replacement and much more."
        )

    return (
        f"💎 <b>{plan.name} Plan</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"{plan_price_block(plan_name)}\n"
        f"{plan_feature_tree(plan_name)}"
    )


# ==========================================
# BILLING HELPERS
# ==========================================

def duration_days(cycle: str) -> int:
    """Number of days a billing cycle grants."""
    cycle = (cycle or "monthly").lower()
    if cycle == "weekly":
        return 7
    if cycle == "yearly":
        return 365
    return 30


def _cycle_multiplier(cycle: str) -> float:
    """How many 'months' a cycle is worth, before any discount."""
    cycle = (cycle or "monthly").lower()
    if cycle == "weekly":
        return 0.25
    if cycle == "yearly":
        return 12.0
    return 1.0


def _cycle_discount_rate(cycle: str) -> float:
    """Discount applied to a cycle. Yearly gets 20% off."""
    return 0.20 if (cycle or "").lower() == "yearly" else 0.0


def payable_amount_paise(plan_name: str, cycle: str, first_paid_order: bool = False) -> tuple[int, int, int]:
    """
    INR pricing in paise (1 INR = 100 paise) for Razorpay.
    Returns: (original_paise, discount_paise, payable_paise)
    """
    plan = PLANS.get(plan_name)
    if not plan or plan.monthly_rupees == 0:
        return 0, 0, 0

    base_monthly_paise = plan.monthly_rupees * 100
    original_paise = int(round(base_monthly_paise * _cycle_multiplier(cycle)))

    discount_paise = int(round(original_paise * _cycle_discount_rate(cycle)))

    # Optional first-time buyer bonus. Left disabled by default — flip this
    # on only if you actually want to run a welcome offer.
    FIRST_ORDER_BONUS_RATE = 0.0
    if first_paid_order and FIRST_ORDER_BONUS_RATE:
        discount_paise += int(round(original_paise * FIRST_ORDER_BONUS_RATE))

    if discount_paise > original_paise:
        discount_paise = original_paise

    return original_paise, discount_paise, original_paise - discount_paise


def usdt_amount_usd(plan_name: str, cycle: str) -> float:
    """USDT price for a plan/cycle — same cycle maths as INR."""
    plan = PLANS.get(plan_name)
    if not plan or plan.usdt_monthly_usd <= 0:
        return 0.0
    gross = plan.usdt_monthly_usd * _cycle_multiplier(cycle)
    return round(gross * (1 - _cycle_discount_rate(cycle)), 2)


def stars_amount(plan_name: str, cycle: str) -> int:
    """Telegram Stars price for a plan/cycle — same cycle maths as INR.

    Stars are whole numbers only, so the result is rounded UP to make sure a
    discounted yearly plan never ends up cheaper than intended by a fraction.
    """
    plan = PLANS.get(plan_name)
    if not plan or plan.tg_stars <= 0:
        return 0
    gross = plan.tg_stars * _cycle_multiplier(cycle)
    net = gross * (1 - _cycle_discount_rate(cycle))
    return max(1, int(net + 0.999))


# Referral commission: the referrer earns this share of every payment their
# referred user makes, for as long as that user keeps paying.
REFERRAL_RATE = 0.20

# Minimum balance before a payout can be requested. Set low enough to feel
# reachable, high enough that payouts are not a stream of tiny transfers.
MIN_WITHDRAWAL_PAISE = 50000  # ₹500


def referral_commission_paise(amount_paise: int) -> int:
    """The referrer's cut of a payment, rounded down to whole paise."""
    if amount_paise <= 0:
        return 0
    return int(amount_paise * REFERRAL_RATE)


def format_paise(amount_paise: int) -> str:
    """Formats paise into a readable INR string."""
    return f"₹{amount_paise / 100:.2f}"


def format_stars(amount: int) -> str:
    return f"⭐ {amount} Stars"
