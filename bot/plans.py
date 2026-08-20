from dataclasses import dataclass

@dataclass
class Plan:
    name: str
    tasks: int
    sources_per_task: int
    destinations_per_task: int
    daily_messages: int | None
    monthly_rupees: int
    usdt_price: int

# Updated Tiers matching your new limits (Basic and Premium)
PLANS = {
    "free": Plan(
        name="Free", 
        tasks=1, sources_per_task=1, destinations_per_task=1, 
        daily_messages=50, monthly_rupees=0, usdt_price=0
    ),
    "silver": Plan(
        name="Basic (Silver)", 
        tasks=5, sources_per_task=5, destinations_per_task=5, 
        daily_messages=None, monthly_rupees=299, usdt_price=5
    ),
    "gold": Plan(
        name="Gold", 
        tasks=10, sources_per_task=15, destinations_per_task=15, 
        daily_messages=None, monthly_rupees=599, usdt_price=8
    ),
    "platinum": Plan(
        name="Premium (Platinum)", 
        tasks=20, sources_per_task=50, destinations_per_task=30, 
        daily_messages=None, monthly_rupees=1000, usdt_price=10
    ),
}

def duration_days(cycle: str) -> int:
    """Returns the number of days for a given billing cycle."""
    cycle = cycle.lower()
    if cycle == "weekly":
        return 7
    if cycle == "yearly":
        return 365
    return 30  # default to monthly

def payable_amount_paise(plan_name: str, cycle: str, first_paid_order: bool = False) -> tuple[int, int, int]:
    """Returns (original_amount_paise, discount_amount_paise, payable_amount_paise)"""
    plan = PLANS.get(plan_name, PLANS["free"])
    
    base_monthly_paise = plan.monthly_rupees * 100
    original = base_monthly_paise
    discount = 0

    if cycle == "weekly":
        original = int(base_monthly_paise / 4)
    elif cycle == "yearly":
        original = base_monthly_paise * 12
        discount = int(original * 0.20)  # 20% off for yearly

    # First time user discount can be applied here if needed
    if first_paid_order and plan_name != "free" and discount == 0:
        # Example: 5% extra off on first purchase if not yearly
        pass

    payable = original - discount
    return original, discount, payable

def format_paise(paise: int) -> str:
    """Formats an amount in paise to a readable INR string."""
    return f"₹{paise / 100:.2f}"
