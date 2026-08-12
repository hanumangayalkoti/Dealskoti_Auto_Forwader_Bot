Dealskoti Message Forwarder
Telegram userbot-based message forwarding service for affiliates. The product contract is documented in the uploaded build specification.
What is implemented
Single-process Railway entrypoint: `python -m bot`
PostgreSQL schema bootstrap and durable user, session, task, payment, referral, usage, and audit tables
English and Hinglish user text with exactly 15 localized FAQ entries
Updates-channel membership gate with rejoin verification and task pausing
Fernet encryption for Telethon session strings
Short-lived phone-code and 2FA login state; OTP and 2FA passwords are never persisted
Public entity validation for task sources and destinations
Real-time message copy worker with bounded concurrency and FloodWait wait/retry
Decimal/integer-paise plan pricing with first-order and yearly discounts
Razorpay order creation, hosted checkout page, signed `payment.captured` webhook verification, idempotent activation, and captured-amount matching
`/health` endpoint for Railway
Local run
```bash
cp .env.example .env
# fill the values in .env
python -m bot
```
The service listens on `PORT` (default `8080`).
Railway variables
Set every variable in `.env.example`. `PUBLIC_BASE_URL` must be the public Railway URL without a trailing slash, for example:
```text
PUBLIC_BASE_URL=https://your-service.up.railway.app
```
Configure the Razorpay webhook at:
```text
https://your-service.up.railway.app/webhooks/razorpay
```
Use the same `RAZORPAY_WEBHOOK_SECRET` in Railway and Razorpay. Enable `payment.captured`.
Keep `SESSION_ENCRYPTION_KEY` unchanged after users connect accounts. Never commit `.env`, Telethon session files, OTPs, 2FA passwords, or payment secrets.
Important deployment order
Add PostgreSQL to the same Railway project and use its `DATABASE_URL`.
Create the Telegram bot, Telegram API credentials, and public updates channel.
Promote the control bot in the updates channel so membership checks work.
Generate and save one Fernet key for `SESSION_ENCRYPTION_KEY`.
Add all environment variables, including `PUBLIC_BASE_URL`.
Deploy and confirm `GET /health`.
Configure and test the Razorpay webhook.
Test `/start`, channel gate, `/connect`, task validation, test payment, and forwarding with test accounts.
Do not replace a production database with a new empty database during redeploy.
