from dataclasses import dataclass

@dataclass
class Plan:
    name: str
    tasks: int
    sources_per_task: int
    destinations_per_task: int
    daily_messages: int | None
    monthly_rupees: int
    usdt_monthly_usd: float = 0.0

# ==========================================
# PLAN DEFINITIONS (Strictly as per Master Prompt)
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
    ),
    "silver": Plan(
        name="Silver",
        tasks=5,
        sources_per_task=5,
        destinations_per_task=5,
        daily_messages=200,
        monthly_rupees=100,
        usdt_monthly_usd=1.5,
    ),
    "gold": Plan(
        name="Gold",
        tasks=10,
        sources_per_task=10,
        destinations_per_task=10,
        daily_messages=1000,
        monthly_rupees=300,
        usdt_monthly_usd=5.0,
    ),
    "platinum": Plan(
        name="Platinum",
        tasks=15,
        sources_per_task=15,
        destinations_per_task=15,
        daily_messages=None,  # None means unlimited forwards/day
        monthly_rupees=800,
        usdt_monthly_usd=10.0,
    ),
}

# ==========================================
# BILLING HELPERS
# ==========================================

def duration_days(cycle: str) -> int:
    """Returns the number of days for a given billing cycle."""
    cycle = cycle.lower()
    if cycle == "weekly":
        return 7
    elif cycle == "yearly":
        return 365
    # Default is monthly
    return 30

def payable_amount_paise(plan_name: str, cycle: str, first_paid_order: bool = False) -> tuple[int, int, int]:
    """
    Calculates the pricing in paise (1 INR = 100 Paise) for Razorpay.
    Returns: (original_amount_paise, discount_amount_paise, payable_amount_paise)
    """
    plan = PLANS.get(plan_name)
    if not plan or plan.monthly_rupees == 0:
        return 0, 0, 0
        
    base_monthly_paise = plan.monthly_rupees * 100
    
    # Calculate base price depending on the cycle
    if cycle == "weekly":
        original_paise = int(base_monthly_paise / 4)
    elif cycle == "yearly":
        original_paise = base_monthly_paise * 12
    else:
        original_paise = base_monthly_paise
        
    discount_paise = 0
    
    # 20% discount on Yearly cycle as per prompt
    if cycle == "yearly":
        discount_paise += int(original_paise * 0.20)
        
    # Apply an extra 10% welcome discount for the very first order if you want
    # (Uncomment the lines below if you want to give a first-time buyer discount)
    # if first_paid_order:
    #     discount_paise += int(original_paise * 0.10)
        
    # Ensure discount doesn't exceed original price
    if discount_paise > original_paise:
        discount_paise = original_paise
        
    payable_paise = original_paise - discount_paise
    
    return original_paise, discount_paise, payable_paise

def format_paise(amount_paise: int) -> str:
    """Formats paise into a readable INR string."""
    return f"₹{amount_paise / 100:.2f}"

def usdt_amount_usd(plan_name: str, cycle: str) -> float:
    """USDT price for a plan/cycle. Same cycle ratios as INR pricing
    (weekly = monthly/4, yearly = 12 months with 20% off)."""
    plan = PLANS.get(plan_name)
    if not plan or plan.usdt_monthly_usd <= 0:
        return 0.0
    if cycle == "weekly":
        return round(plan.usdt_monthly_usd / 4, 2)
    if cycle == "yearly":
        return round(plan.usdt_monthly_usd * 12 * 0.80, 2)
    return round(plan.usdt_monthly_usd, 2)
