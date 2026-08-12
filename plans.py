from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


@dataclass(frozen=True)
class Plan:
    name: str
    monthly_rupees: Decimal
    tasks: int
    sources_per_task: int
    destinations_per_task: int
    daily_messages: int | None


PLANS: dict[str, Plan] = {
    "free": Plan("Free", Decimal("0"), 1, 1, 1, 50),
    "silver": Plan("Silver", Decimal("59"), 2, 1, 1, 200),
    "gold": Plan("Gold", Decimal("199"), 5, 3, 3, 500),
    "platinum": Plan("Platinum", Decimal("499"), 10, 10, 10, None),
}


def _paise(amount: Decimal) -> int:
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def duration_days(cycle: str) -> int:
    if cycle == "weekly":
        return 7
    if cycle == "monthly":
        return 30
    if cycle == "yearly":
        return 365
    raise ValueError("Billing cycle must be weekly, monthly, or yearly")


def cycle_amount_paise(plan_name: str, cycle: str) -> int:
    plan = PLANS[plan_name]
    if cycle == "weekly":
        amount = plan.monthly_rupees / Decimal("4")
    elif cycle == "monthly":
        amount = plan.monthly_rupees
    elif cycle == "yearly":
        amount = plan.monthly_rupees * Decimal("12") * Decimal("0.80")
    else:
        raise ValueError("Billing cycle must be weekly, monthly, or yearly")
    return _paise(amount)


def payable_amount_paise(
    plan_name: str, cycle: str, *, first_paid_order: bool
) -> tuple[int, int, int]:
    original = cycle_amount_paise(plan_name, cycle)
    yearly_discount = (
        _paise(PLANS[plan_name].monthly_rupees * Decimal("12")) - original
        if cycle == "yearly"
        else 0
    )
    first_discount = (
        int(
            (Decimal(original) * Decimal("0.40")).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        if first_paid_order
        else 0
    )
    payable = max(0, original - first_discount)
    return original, yearly_discount + first_discount, payable


def format_paise(paise: int) -> str:
    return f"₹{(Decimal(paise) / Decimal('100')).quantize(Decimal('0.01'))}"