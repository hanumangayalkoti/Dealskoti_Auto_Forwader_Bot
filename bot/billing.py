from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings


class BillingError(RuntimeError):
    """A safe, user-actionable Razorpay integration error."""


@dataclass(frozen=True)
class CapturedPayment:
    order_id: str
    payment_id: str
    amount_paise: int
    event: str


@dataclass(frozen=True)
class PaymentLink:
    link_id: str
    short_url: str


class RazorpayBilling:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def configured(self) -> bool:
        return bool(self.settings.razorpay_key_id and self.settings.razorpay_key_secret)

    async def create_payment_link(
        self, *, amount_paise: int, receipt: str, plan: str, cycle: str, user_id: int
    ) -> PaymentLink:
        if not self.configured:
            raise BillingError("Razorpay keys are not configured")
        payload = {
            "amount": amount_paise,
            "currency": "INR",
            "accept_partial": False,
            "reference_id": receipt,
            "description": f"Dealskoti {plan.title()} {cycle} subscription",
            "customer": {"name": f"Telegram user {user_id}"},
            "expire_by": int(time.time()) + 24 * 60 * 60,
            "notes": {"plan": plan, "cycle": cycle, "user_id": str(user_id)},
        }
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    "https://api.razorpay.com/v1/payment_links",
                    auth=(self.settings.razorpay_key_id or "", self.settings.razorpay_key_secret or ""),
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise BillingError("Razorpay could not create the order. Try again shortly.") from exc
        link_id = body.get("id")
        short_url = body.get("short_url")
        if not isinstance(link_id, str) or not link_id or not isinstance(short_url, str):
            raise BillingError("Razorpay returned an invalid payment link response")
        return PaymentLink(link_id=link_id, short_url=short_url)

    def verify_webhook_signature(self, raw_body: bytes, signature: str) -> bool:
        secret = self.settings.razorpay_webhook_secret
        if not secret or not signature:
            return False
        expected = hmac.new(
            secret.encode("utf-8"), raw_body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def parse_captured_payment(self, payload: dict[str, Any]) -> CapturedPayment | None:
        event = str(payload.get("event", ""))
        if event not in {"payment.captured", "payment_link.paid"}:
            return None
        payment_entity = (
            payload.get("payload", {}).get("payment", {}).get("entity", {})
        )
        link_entity = (
            payload.get("payload", {}).get("payment_link", {}).get("entity", {})
        )
        order_id = (
            link_entity.get("id") if event == "payment_link.paid" else payment_entity.get("order_id")
        )
        payment_id = payment_entity.get("id")
        amount = payment_entity.get("amount") or link_entity.get("amount")
        if not isinstance(order_id, str) or not isinstance(payment_id, str):
            raise BillingError("Webhook payment is missing its payment link/order ID")
        if not isinstance(amount, int) or amount <= 0:
            raise BillingError("Webhook payment has an invalid captured amount")
        return CapturedPayment(order_id, payment_id, amount, event)

    @staticmethod
    def parse_json(raw_body: bytes) -> dict[str, Any]:
        try:
            value = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise BillingError("Webhook body is not valid JSON") from exc
        if not isinstance(value, dict):
            raise BillingError("Webhook body must be a JSON object")
        return value
