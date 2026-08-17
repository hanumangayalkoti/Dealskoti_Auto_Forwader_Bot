import base64
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass

import aiohttp

from .config import Settings

logger = logging.getLogger("dealskoti.billing")

class BillingError(Exception):
    """Raised when payment gateway operations fail."""
    pass

@dataclass
class PaymentLinkResult:
    link_id: str
    short_url: str

@dataclass
class CapturedPayment:
    order_id: str  # In our case, this will be the payment_link ID (plink_...)
    payment_id: str
    amount_paise: int

class RazorpayBilling:
    def __init__(self, settings: Settings):
        self.key_id = settings.razorpay_key_id
        self.key_secret = settings.razorpay_key_secret
        self.webhook_secret = settings.razorpay_webhook_secret

    async def create_payment_link(self, amount_paise: int, receipt: str, plan: str, cycle: str, user_id: int) -> PaymentLinkResult:
        url = "https://api.razorpay.com/v1/payment_links"
        
        auth_string = f"{self.key_id}:{self.key_secret}"
        encoded_auth = base64.b64encode(auth_string.encode()).decode()
        
        headers = {
            "Authorization": f"Basic {encoded_auth}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "amount": amount_paise,
            "currency": "INR",
            "accept_partial": False,
            "reference_id": receipt,
            "description": f"DealsKoti {plan.title()} Plan - {cycle.title()}",
            "customer": {
                "name": f"User {user_id}",
                "contact": ""
            },
            "notify": {
                "sms": False,
                "email": False
            },
            "reminder_enable": False,
            "notes": {
                "user_id": str(user_id),
                "plan": plan,
                "cycle": cycle
            }
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers, timeout=15) as resp:
                    data = await resp.json()
                    
                    if resp.status >= 400:
                        error_msg = data.get("error", {}).get("description", "Unknown error")
                        logger.error(f"Razorpay API Error: {error_msg}")
                        raise BillingError(f"Failed to create payment link: {error_msg}")
                        
                    return PaymentLinkResult(
                        link_id=data["id"],  # Looks like 'plink_xxx...'
                        short_url=data["short_url"]
                    )
        except Exception as e:
            logger.error(f"Razorpay request failed: {e}")
            raise BillingError("Could not connect to the payment gateway. Please try again later.")

    def verify_webhook_signature(self, raw_body: bytes, signature: str) -> bool:
        """Verifies that the webhook genuinely came from Razorpay."""
        if not signature or not raw_body:
            return False
            
        expected_signature = hmac.new(
            self.webhook_secret.encode('utf-8'),
            raw_body,
            hashlib.sha256
        ).hexdigest()
        
        return hmac.compare_digest(expected_signature, signature)

    def parse_json(self, raw_body: bytes) -> dict:
        try:
            return json.loads(raw_body.decode('utf-8'))
        except json.JSONDecodeError:
            raise ValueError("Invalid JSON body in webhook")

    def parse_captured_payment(self, payload: dict) -> CapturedPayment | None:
        """
        Safely extracts order ID, payment ID, and amount from Razorpay webhook events.
        It prioritizes 'payment_link.paid' because our DB uses link_id as order_id.
        """
        event = payload.get("event")
        
        # Primary method: Webhook triggered directly by a Payment Link
        if event == "payment_link.paid":
            link_entity = payload.get("payload", {}).get("payment_link", {}).get("entity", {})
            plink_id = link_entity.get("id")  # This matches the link.link_id saved in our DB
            amount = link_entity.get("amount")
            
            # Attempt to extract the actual payment ID as well, if available
            payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
            payment_id = payment_entity.get("id", "unknown_txn")
            
            if plink_id and amount:
                return CapturedPayment(
                    order_id=plink_id, 
                    payment_id=payment_id, 
                    amount_paise=amount
                )

        # Fallback method: Generic payment capture event
        # (This is processed in case 'payment_link.paid' webhook fails but 'payment.captured' succeeds)
        elif event == "payment.captured":
            payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
            
            # The actual payment might have been generated by a payment link. 
            # If so, Razorpay puts the payment link ID in the notes or order_id field sometimes.
            # We first try to check if the notes contain the payment link ID.
            notes = payment_entity.get("notes", {})
            
            order_id = payment_entity.get("order_id")
            payment_id = payment_entity.get("id")
            amount = payment_entity.get("amount")
            
            # If there's no order_id natively, it was likely an invoice or pure link.
            # We skip generic captures if they don't map to our DB to avoid spamming ignored webhooks.
            if order_id and payment_id and amount:
                return CapturedPayment(
                    order_id=order_id, 
                    payment_id=payment_id, 
                    amount_paise=amount
                )
                
        return None
