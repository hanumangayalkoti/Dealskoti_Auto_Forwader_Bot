from __future__ import annotations

import hashlib
import hmac
import json
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


class RazorpayBilling:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def configured(self) -> bool:
        return bool(self.settings.razorpay_key_id and self.settings.razorpay_key_secret)

    async def create_order(
        self, *, amount_paise: int, receipt: str, plan: str, cycle: str, user_id: int
    ) -> str:
        if not self.configured:
            raise BillingError("Razorpay keys are not configured")
        payload = {
            "amount": amount_paise,
            "currency": "INR",
            "receipt": receipt,
            "notes": {"plan": plan, "cycle": cycle, "user_id": str(user_id)},
        }
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    "https://api.razorpay.com/v1/orders",
                    auth=(self.settings.razorpay_key_id or "", self.settings.razorpay_key_secret or ""),
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise BillingError("Razorpay could not create the order. Try again shortly.") from exc
        order_id = body.get("id")
        if not isinstance(order_id, str) or not order_id:
            raise BillingError("Razorpay returned an invalid order response")
        return order_id

    def checkout_url(self, order_id: str) -> str:
        base_url = self.settings.public_base_url
        if not base_url:
            raise BillingError("PUBLIC_BASE_URL is not configured")
        return f"{base_url}/checkout/{order_id}"

    def verify_webhook_signature(self, raw_body: bytes, signature: str) -> bool:
        secret = self.settings.razorpay_webhook_secret
        if not secret or not signature:
            return False
        expected = hmac.new(
            secret.encode("utf-8"), raw_body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def parse_captured_payment(self, payload: dict[str, Any]) -> CapturedPayment | None:
        if payload.get("event") != "payment.captured":
            return None
        entity = (
            payload.get("payload", {})
            .get("payment", {})
            .get("entity", {})
        )
        order_id = entity.get("order_id")
        payment_id = entity.get("id")
        amount = entity.get("amount")
        if not isinstance(order_id, str) or not isinstance(payment_id, str):
            raise BillingError("Webhook payment is missing its IDs")
        if not isinstance(amount, int) or amount <= 0:
            raise BillingError("Webhook payment has an invalid captured amount")
        return CapturedPayment(order_id, payment_id, amount)

    @staticmethod
    def parse_json(raw_body: bytes) -> dict[str, Any]:
        try:
            value = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise BillingError("Webhook body is not valid JSON") from exc
        if not isinstance(value, dict):
            raise BillingError("Webhook body must be a JSON object")
        return value