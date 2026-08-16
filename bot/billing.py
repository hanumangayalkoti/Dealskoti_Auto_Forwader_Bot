from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass

import aiohttp

from .config import Settings

logger = logging.getLogger("dealskoti.billing")

class BillingError(Exception):
    """Custom exception for billing and Razorpay API errors."""
    pass

@dataclass
class PaymentLinkObject:
    link_id: str
    short_url: str

@dataclass
class CapturedPayment:
    order_id: str
    payment_id: str
    amount_paise: int

class RazorpayBilling:
    def __init__(self, settings: Settings):
        self.key_id = settings.razorpay_key_id
        self.key_secret = settings.razorpay_key_secret
        self.webhook_secret = settings.razorpay_webhook_secret

    async def create_payment_link(
        self, amount_paise: int, receipt: str, plan: str, cycle: str, user_id: int
    ) -> PaymentLinkObject:
        """
        Creates a payment link securely using Razorpay's API.
        Runs asynchronously to prevent blocking the Telegram bot.
        """
        url = "https://api.razorpay.com/v1/payment_links"
        auth = aiohttp.BasicAuth(self.key_id, self.key_secret)
        
        payload = {
            "amount": amount_paise,
            "currency": "INR",
            "accept_partial": False,
            "reference_id": receipt,
            "description": f"DealsKoti {plan.title()} Plan ({cycle})",
            "customer": {
                "name": str(user_id)
            },
            "notify": {
                "sms": False,
                "email": False
            },
            "reminder_enable": False,
            "notes": {
                "plan": plan,
                "cycle": cycle,
                "user_id": str(user_id)
            }
        }
        
        try:
            async with aiohttp.ClientSession(auth=auth) as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status >= 400:
                        error_text = await resp.text()
                        logger.error(f"Razorpay API Error ({resp.status}): {error_text}")
                        raise BillingError("Failed to create payment link.")
                        
                    data = await resp.json()
                    return PaymentLinkObject(
                        link_id=data["id"], 
                        short_url=data["short_url"]
                    )
        except aiohttp.ClientError as e:
            logger.exception("Network error while communicating with Razorpay.")
            raise BillingError("Network error while creating payment link.") from e

    def verify_webhook_signature(self, raw_body: bytes, signature: str) -> bool:
        """
        Verifies the authenticity of the Razorpay webhook to prevent fake payment triggers.
        """
        if not self.webhook_secret or not signature:
            return False
            
        expected_signature = hmac.new(
            self.webhook_secret.encode('utf-8'),
            raw_body,
            hashlib.sha256
        ).hexdigest()
        
        return hmac.compare_digest(expected_signature, signature)

    def parse_json(self, raw_body: bytes) -> dict:
        """Safely parses the JSON payload from the webhook body."""
        try:
            return json.loads(raw_body.decode('utf-8'))
        except json.JSONDecodeError as e:
            raise BillingError("Invalid JSON in webhook payload") from e

    def parse_captured_payment(self, payload: dict) -> CapturedPayment | None:
        """
        Smartly parses both 'payment_link.paid' and 'payment.captured' events.
        Extracts the correct 'plink_xxx' or 'order_xxx' ID required by the database.
        """
        event = payload.get("event")
        
        # 1. Primary Priority: Payment Link Paid Event
        if event == "payment_link.paid":
            pl_entity = payload.get("payload", {}).get("payment_link", {}).get("entity", {})
            pay_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
            
            link_id = pl_entity.get("id")  # This is the plink_xxx saved in the DB
            payment_id = pay_entity.get("id", "")
            amount = pay_entity.get("amount") or pl_entity.get("amount_paid", 0)
            
            if link_id:
                return CapturedPayment(
                    order_id=link_id, 
                    payment_id=payment_id, 
                    amount_paise=amount
                )
                
        # 2. Secondary Fallback: Standard Payment Captured Event
        elif event == "payment.captured":
            pay_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
            
            payment_id = pay_entity.get("id", "")
            amount = pay_entity.get("amount", 0)
            order_id = pay_entity.get("order_id")
            
            # Note: For payment links, the standard 'payment.captured' event doesn't always 
            # put the plink_xxx in the order_id. But if someone uses standard orders, this handles it.
            if order_id:
                return CapturedPayment(
                    order_id=order_id, 
                    payment_id=payment_id, 
                    amount_paise=amount
                )
        
        # If it's an unhandled event or missing IDs, safely ignore
        return None
