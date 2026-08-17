from dataclasses import dataclass

@dataclass
class Plan:
    name: str
    tasks: int
    sources_per_task: int
    destinations_per_task: int
    daily_messages: int | None
    monthly_rupees: int

# ==========================================
# PLAN DEFINITIONS (Strictly as per Master Prompt)
# ==========================================

PLANS: dict[str, Plan] = {
    "free": Plan(
        name="Free",
        tasks=1,
        sources_per_task=1,
        destinations_per_task=1,
        daily_messages=50,
        monthly_rupees=0,
    ),
    "silver": Plan(
        name="Silver",
        tasks=2,
        sources_per_task=1,
        destinations_per_task=1,
        daily_messages=200,
        monthly_rupees=199,  # You can adjust these prices as needed
    ),
    "gold": Plan(
        name="Gold",
        tasks=5,
        sources_per_task=3,
        destinations_per_task=3,
        daily_messages=500,
        monthly_rupees=499,
    ),
    "platinum": Plan(
        name="Platinum",
        tasks=10,
        sources_per_task=10,
        destinations_per_task=10,
        daily_messages=None,  # None means "No normal daily product cap"
        monthly_rupees=999,
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
