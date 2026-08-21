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
    order_id: str
    payment_id: str
    amount_paise: int
    # Razorpay copies payment-link notes onto the payment entity. Used as a
    # fallback when order_id does not match a stored payment row.
    notes: dict | None = None

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
                "name": f"User {user_id}"
                # FIX: Removed the empty "contact" field to prevent Razorpay API errors
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
            timeout = aiohttp.ClientTimeout(total=20)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload, headers=headers) as resp:
                    try:
                        data = await resp.json(content_type=None)
                    except Exception:
                        data = {}

                    if resp.status >= 400:
                        error_msg = (data.get("error") or {}).get("description") or f"HTTP {resp.status}"
                        logger.error(f"Razorpay API Error: {error_msg}")
                        raise BillingError(f"Payment gateway rejected the request: {error_msg}")

                    if not data.get("id") or not data.get("short_url"):
                        logger.error(f"Razorpay returned an unexpected payload: {data}")
                        raise BillingError("Payment gateway returned an incomplete response.")

                    return PaymentLinkResult(
                        link_id=data["id"],
                        short_url=data["short_url"],
                    )
        except BillingError:
            # Already a user-facing message — do not mask the real reason.
            raise
        except aiohttp.ClientError as e:
            logger.error(f"Razorpay request failed: {e}")
            raise BillingError("Could not connect to the payment gateway. Please try again later.")
        except TimeoutError:
            logger.error("Razorpay request timed out")
            raise BillingError("The payment gateway timed out. Please try again in a minute.")
        except Exception as e:
            logger.exception(f"Unexpected Razorpay failure: {e}")
            raise BillingError("Could not create the payment link. Please try again later.")

    def verify_webhook_signature(self, raw_body: bytes, signature: str) -> bool:
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
        event = payload.get("event")
        body = payload.get("payload") or {}
        payment_entity = ((body.get("payment") or {}).get("entity")) or {}

        if event == "payment_link.paid":
            link_entity = ((body.get("payment_link") or {}).get("entity")) or {}
            plink_id = link_entity.get("id")
            # Prefer the amount actually paid; fall back to the link amount.
            amount = payment_entity.get("amount") or link_entity.get("amount")
            payment_id = payment_entity.get("id") or "unknown_txn"
            notes = payment_entity.get("notes") or link_entity.get("notes") or {}

            if plink_id and amount:
                return CapturedPayment(
                    order_id=plink_id,
                    payment_id=payment_id,
                    amount_paise=int(amount),
                    notes=notes,
                )

        elif event == "payment.captured":
            payment_id = payment_entity.get("id")
            amount = payment_entity.get("amount")
            notes = payment_entity.get("notes") or {}
            # A payment made through a payment link has no plink id on the payment
            # entity, so fall back to the notes we attached when creating the link.
            order_id = payment_entity.get("order_id") or notes.get("link_id") or ""

            if payment_id and amount:
                return CapturedPayment(
                    order_id=order_id,
                    payment_id=payment_id,
                    amount_paise=int(amount),
                    notes=notes,
                )

        return None
