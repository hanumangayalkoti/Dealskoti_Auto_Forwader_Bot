from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

@dataclass
class Plan:
    name: str
    monthly_rupees: int
    tasks: int
    sources_per_task: int
    destinations_per_task: int
    daily_messages: int  # 0 means no limit
    priority_forwarding: bool

# Define pricing (Modify monthly_rupees as per your actual real-world pricing)
PLANS: Dict[str, Plan] = {
    "free": Plan(
        name="Free",
        monthly_rupees=0,
        tasks=1,
        sources_per_task=1,
        destinations_per_task=1,
        daily_messages=50,
        priority_forwarding=False,
    ),
    "silver": Plan(
        name="Silver",
        monthly_rupees=149,  # Example price, you can change this
        tasks=2,
        sources_per_task=1,
        destinations_per_task=1,
        daily_messages=200,
        priority_forwarding=False,
    ),
    "gold": Plan(
        name="Gold",
        monthly_rupees=299,
        tasks=5,
        sources_per_task=3,
        destinations_per_task=3,
        daily_messages=500,
        priority_forwarding=True,
    ),
    "platinum": Plan(
        name="Platinum",
        monthly_rupees=599,
        tasks=10,
        sources_per_task=10,
        destinations_per_task=10,
        daily_messages=0,  # 0 = No normal daily cap
        priority_forwarding=True,
    ),
}

def duration_days(cycle: str) -> int:
    """Returns the exact number of days for a billing cycle."""
    cycle = cycle.lower()
    if cycle == "weekly":
        return 7
    elif cycle == "monthly":
        return 30
    elif cycle == "yearly":
        return 365
    return 30  # Fallback

def payable_amount_paise(plan_name: str, cycle: str, first_paid_order: bool = False) -> Tuple[int, int, int]:
    """
    Calculates the pricing in paise (1 INR = 100 Paise) for Razorpay.
    Returns: (original_amount, discount_amount, final_payable_amount)
    """
    if plan_name not in PLANS or plan_name == "free":
        return 0, 0, 0

    monthly_price = PLANS[plan_name].monthly_rupees
    
    # Base calculation
    if cycle == "weekly":
        # Weekly is roughly 1/4th of monthly
        base_rupees = max(9, monthly_price // 4) 
    elif cycle == "yearly":
        base_rupees = monthly_price * 12
    else:  # monthly
        base_rupees = monthly_price

    original_paise = base_rupees * 100
    discount_paise = 0

    # 1. Yearly Discount (20% Off)
    if cycle == "yearly":
        yearly_discount = int(original_paise * 0.20)
        discount_paise += yearly_discount

    # 2. First Order Discount (Example: 10% Off on first ever purchase)
    # If you don't want a first-order discount, you can remove this block.
    first_order_discount = 0
    if first_paid_order:
        remaining_after_yearly = original_paise - discount_paise
        first_order_discount = int(remaining_after_yearly * 0.10)
        discount_paise += first_order_discount

    final_payable = original_paise - discount_paise
    
    # Razorpay minimum amount restriction (₹1.00)
    if final_payable < 100:
        final_payable = 100

    return original_paise, discount_paise, final_payable

def format_paise(paise: int) -> str:
    """Converts paise to a formatted Rupee string (e.g., 14900 -> 149.00)"""
    rupees = paise / 100.0
    # Strip .00 if it's a flat amount for cleaner UI
    if rupees.is_integer():
        return f"{int(rupees)}"
    return f"{rupees:.2f}"
