# Lyrpay

Hold, send and convert money in every currency, top up through Stripe, and follow the crypto markets.

Live: https://d2figysrc1xdl9.cloudfront.net (lyrpay.com once its DNS points here, see below)

## What works today

- **Sign-in with your Lyr account**: the Cognito pool Lyrtrip uses (`us-east-1_elNSe2VTE`), so one account works on both. Email + password, mobile number + password (the code is texted through Lyrtrip's Twilio Verify, relayed by `/api/auth/phone/*`), or Google.
- **Wallet**: balances in any of ~165 currencies, valued in the person's home currency (picked from their country).
- **Add money**: Stripe Checkout in the chosen currency. Checkout offers every payment method switched on in the Stripe dashboard (cards, Apple/Google Pay, bank debits, local methods). The balance is credited once the session is paid, at most once per session.
- **Send**: instant transfers to anyone on Lyrpay by verified email or mobile number.
- **Convert**: between currencies at the mid-market rate minus `FX_FEE_BPS` (0.5% by default), rounded down to the target's smallest unit.
- **Crypto markets** (CoinGecko) and **world exchange rates** (open.er-api.com, updated daily).
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
mobile/     Expo app from the original scaffold, not yet moved to the new API
```

## Deploy

Needs AWS credentials, the SAM CLI and Node 20+.

```bash
./deploy.sh
```

The first run creates the stack, then redeploys once so sign-in callbacks and payment redirects use the new CloudFront domain.

### Switch on card payments

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
