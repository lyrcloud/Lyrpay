# Lyrpay

Hold, send and convert money in every currency, top up through Stripe, and follow the crypto markets.

Live: https://lyrpay.com

## What works today

- **Sign-in with your Lyr account**: the Cognito pool Lyrtrip uses (`us-east-1_elNSe2VTE`), so one account works on both. Email + password, mobile number + password (the code is texted through Lyrtrip's Twilio Verify, relayed by `/api/auth/phone/*`), or Google.
- **Wallet**: balances in any of ~165 currencies, valued in the person's home currency (picked from their country).
- **Add money**: Stripe Checkout in the chosen currency. Checkout offers every payment method switched on in the Stripe dashboard (cards, Apple/Google Pay, bank debits, local methods). The balance is credited once the session is paid, at most once per session.
- **Send**: instant transfers to anyone on Lyrpay by verified email or mobile number.
- **Withdraw**: to the person's own bank account through Stripe Connect. They add bank and identity details on a Stripe-hosted page (an Express account, created in their country); Lyrpay then transfers from the platform balance and Stripe pays out to their bank. Withdrawals are in the platform's settlement currency, so other balances are converted first. The balance is debited before the transfer and restored if Stripe refuses it. If Stripe doesn't answer, the withdrawal stays pending until it's settled, either when the person next opens their wallet or by `lyrpay-withdrawal-sweep` every 15 minutes. Both first look for a Stripe transfer tagged with the withdrawal, so a retry never pays twice. Requires Connect to be enabled on the Stripe account, and enough available platform balance.
- **Convert**: between currencies at the mid-market rate minus `FX_FEE_BPS` (0.5% by default), rounded down to the target's smallest unit.
- **Live markets on the home page**: a scrolling ticker, the top 50 coins with 7-day sparklines (refreshed every minute) and every world currency (open.er-api.com, updated daily) with a quick converter, before anyone signs in.
- **Super admin** (`lyrpay.com/#admin`): accounts, money held, daily volume, volume by type and the latest activity across everyone. Only verified emails in `ADMIN_EMAILS` (default `lyrclouds@gmail.com`) see it; the API checks this on every request.
- **Web3 wallet**: connects a browser wallet and shows its address, chain and native balance.

PayPal, Wise, Western Union, Ria, Robinhood, Kalshi, Gemini, Coinbase and Kraken are listed under Connections as *coming soon*: each needs a partner account and API approval before money can move through it.

## Layout

```
backend/    FastAPI on Lambda (Mangum): auth.py checks Cognito ID tokens, ledger.py keeps balances in DynamoDB
            with transactional debits/credits, stripe_client.py, rates.py
frontend/   React + Vite dashboard; lib/auth.ts is the Cognito helper (same flows as Lyrtrip)
template.yaml  DynamoDB ledger, API Lambda + Function URL, Lyrpay's app client in the Lyr account pool,
               S3 + CloudFront (serves /api/* from the Lambda, so the site and API share an origin)
deploy.sh   builds and deploys all of it
mobile/     Expo 50 app: sign in with a Lyr account (email or mobile number), balances, sending, activity,
            crypto prices; sign-up, Google, top-ups and withdrawals open the web dashboard
```

## Deploy

Needs AWS credentials, the SAM CLI and Node 20+.

```bash
./deploy.sh
```

The first run creates the stack, then redeploys once so sign-in callbacks and payment redirects use the new CloudFront domain.

### Emails

Sent through SES from `support@lyrpay.com` (`backend/app/mail.py`); each has a signed one-click unsubscribe link (`/api/unsubscribe`).

- **Welcome**: once per person, the first time their wallet opens with an email on it.
- **Updates**: announcements in `UPDATES`, sent by the `lyrpay-send-update` Lambda to every Lyr account that finished signing up (and every Lyrpay profile), minus anyone who unsubscribed. Each address gets a campaign at most once, so a rerun only reaches people added since. Preview the list first, then send:

```bash
aws lambda invoke --function-name lyrpay-send-update --cli-binary-format raw-in-base64-out \
  --payload '{"campaign": "lyrpay-live-2026-10", "dryRun": true}' /dev/stdout
# then "dryRun": false; add "only": ["you@example.com"] to try it on one address
```

To announce something new, add a campaign to `UPDATES`, deploy, and invoke it the same way.

### Switch on card payments and withdrawals

The API reads the Stripe secret key from Secrets Manager:

```bash
aws secretsmanager create-secret --name lyrpay/stripe --secret-string 'sk_live_or_test_...'
```

Warm Lambdas cache the key, so update the function or wait a few minutes before checking that `/api/config` shows `"cardPayments": true`.

### Point lyrpay.com here

lyrpay.com's DNS is on Cloudflare. Request an ACM certificate in us-east-1 for `lyrpay.com` and `www.lyrpay.com`, add its validation CNAMEs in Cloudflare, add the domains as CloudFront aliases, CNAME them to the distribution, then:

```bash
PRIMARY_ORIGIN=https://lyrpay.com ./deploy.sh
```

## Run locally

```bash
cd backend && python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp .env.example .env   # fill COGNITO_CLIENT_ID from the stack output UserPoolClientId
.venv/bin/uvicorn app.main:app --reload --port 8000 --env-file .env
.venv/bin/python -m pytest

cd frontend && npm install && cp .env.example .env.local && npm run dev   # proxies /api to :8000
```
