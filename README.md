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
- **Gift cards** (Rewards tab): bought from a balance in any currency, emailed to someone or shared as a code (`LYR-XXXX-XXXX-XXXX`), redeemed once into anyone's balance; the buyer can cancel an unused card for a refund.
- **Promo codes**: made by the admin. A code adds a bonus to a top-up (a percentage up to a cap, or a fixed amount), with a minimum top-up, a usage limit, once per person and an expiry. Checked when checkout starts and again when the payment is confirmed; the bonus goes in the same transaction as the top-up.
- **Referrals**: everyone has a link (`lyrpay.com/?ref=CODE`), remembered for 30 days like Lyrtrip's. When a referred friend's first top-up of at least the minimum is paid, the referrer gets a bonus (and the friend, if set), once. Defaults: $5 to the referrer, nothing to the friend, $20 minimum; the admin changes them under Rewards settings.
- **Business accounts (B2B)**: a business applies with its registration details; the admin approves or rejects it and the applicant is emailed. Approved businesses convert at the business fee (0.25% by default), show their company name on payments, and pay up to 30 people at once, all or nothing.
- **Lyrpay Visa cards** (Card tab, through Stripe Issuing): a virtual card that spends from the person's balance in the card's currency. Stripe asks Lyrpay to approve each payment (`/api/stripe/issuing`); it's approved only if the balance covers it, and held at once. Captures, tips, reversals, expiries and refunds are settled against the hold, each Stripe event applied once and in any order. Card numbers are shown by Stripe.js in Stripe's frames, never by Lyrpay. Until Issuing is on, the tab shows the card and a waitlist.
- **Sign-up codes**: the shared account pool sends them through SES from `no-reply@lyrcloud.com`, branded Lyrpay for Lyrpay's app client (the `lyr-auth-messages` function in Lyrtrip's `infra/cognito.yml`). The sign-up screen can send a new code.
- **Super admin** (`lyrpay.com/#admin`): accounts, money held, daily volume, volume by type and the latest activity across everyone, plus Promo codes, Businesses (review applications) and Rewards settings. Only verified emails in `ADMIN_EMAILS` (default `lyrclouds@gmail.com`) see it; the API checks this on every request.
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
mobile/     Expo 50 app: sign in, sign up and reset passwords with a Lyr account (email or mobile number), balances,
            sending, top-ups (Stripe Checkout in the browser, confirmed by the app when it's back in front),
            converting, withdrawals, activity, crypto prices; Google sign-in opens the web dashboard
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

### Switch on Lyrpay cards (Stripe Issuing)

1. In the Stripe dashboard, apply for **Issuing** (Stripe reviews the business; cards go to people in the platform's country).
2. Add a webhook endpoint `https://lyrpay.com/api/stripe/issuing` for `issuing_authorization.request`, `issuing_authorization.created`, `issuing_authorization.updated`, `issuing_transaction.created` and `issuing_card.updated`, and save its signing secret:
   `aws secretsmanager create-secret --name lyrpay/stripe-issuing-webhook --secret-string 'whsec_...'`
3. Under Issuing settings, set the authorization timeout default to **decline**, and add money to the **Issuing balance**: card spending is paid from it, while Lyrpay debits people's balances.
4. Deploy with the publishable key and your program's cardholder terms: `STRIPE_PUBLISHABLE_KEY=pk_live_... CARD_TERMS_URL=https://... ./deploy.sh` (kept for later deploys).

The Card tab switches from the waitlist to sign-up within 10 minutes. A schedule pings the API every 5 minutes so authorizations, which Stripe gives 2 seconds, rarely meet a cold start.

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
