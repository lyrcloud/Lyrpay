# Lyrpay Web3 Crypto Trading Platform

This repository contains a full-stack scaffold for a multifunction money and crypto trading platform.

Components:
- `backend/` — Python FastAPI backend with crypto trading, wallet, and third-party API integration points.
- `frontend/` — React + Vite web application for trading dashboards and Web3 wallet interaction.
- `mobile/` — React Native Expo mobile app with basic wallet and market views.

## Setup

### Backend
1. Navigate to `backend/`
2. Create a virtual environment and install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill API keys.
4. Run the app:
   ```bash
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

### Frontend
1. Navigate to `frontend/`
2. Install dependencies:
   ```bash
   npm install
   ```
3. Start development server:
   ```bash
   npm run dev
   ```

### Mobile
1. Navigate to `mobile/`
2. Install dependencies:
   ```bash
   npm install
   ```
3. Run Expo:
   ```bash
   npm start
   ```

## Notes
- The backend includes integration stubs for major finance endpoints (Binance, Coinbase, Plaid, Stripe).
- Replace the placeholder API keys with real credentials for live integrations.
- This project is a starting point for building a larger Web3 trading platform.

## AWS Lambda Deployment
1. Ensure AWS credentials are configured and valid.
2. Install AWS SAM CLI.
3. Set the S3 bucket name and run:
   ```bash
   S3_BUCKET=my-bucket ./deploy-lambda.sh
   ```
4. The SAM template uses `backend/app/lambda_handler.py` and deploys the function behind API Gateway.
