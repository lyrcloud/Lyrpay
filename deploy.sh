#!/usr/bin/env bash
# Builds and deploys everything: API + ledger + sign-in client (template.yaml), then the web dashboard.
# Usage: ./deploy.sh
# Custom domain (once): CERTIFICATE_ARN=<issued lyrpay.com cert> ./deploy.sh, then point lyrpay.com at CloudFront
# Cards (once Stripe Issuing is on): STRIPE_PUBLISHABLE_KEY=pk_live_... CARD_TERMS_URL=https://... ./deploy.sh
set -euo pipefail
cd "$(dirname "$0")"

STACK=${STACK_NAME:-lyrpay}
REGION=${AWS_REGION:-us-east-1}
# Hosted UI of the Lyr account pool shared with Lyrtrip
COGNITO_DOMAIN=${COGNITO_DOMAIN:-lyrtrip-auth.auth.us-east-1.amazoncognito.com}

output() {
  aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text 2>/dev/null || true
}

previous() {
  local value
  value=$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
    --query "Stacks[0].Parameters[?ParameterKey=='$1'].ParameterValue" --output text 2>/dev/null || true)
  [ "$value" = "None" ] && value=""
  echo "$value"
}

# Settings given once are kept across plain redeploys: the custom domain's certificate and the card settings
CERTIFICATE_ARN=${CERTIFICATE_ARN:-$(previous CertificateArn)}
STRIPE_PUBLISHABLE_KEY=${STRIPE_PUBLISHABLE_KEY:-$(previous StripePublishableKey)}
CARD_TERMS_URL=${CARD_TERMS_URL:-$(previous CardTermsUrl)}
KEPT=()
[ -n "$CERTIFICATE_ARN" ] && KEPT+=("CertificateArn=$CERTIFICATE_ARN")
[ -n "$STRIPE_PUBLISHABLE_KEY" ] && KEPT+=("StripePublishableKey=$STRIPE_PUBLISHABLE_KEY")
[ -n "$CARD_TERMS_URL" ] && KEPT+=("CardTermsUrl=$CARD_TERMS_URL")

deploy_stack() {
  sam deploy --stack-name "$STACK" --region "$REGION" --resolve-s3 --capabilities CAPABILITY_IAM \
    --no-confirm-changeset --no-fail-on-empty-changeset --parameter-overrides "PrimaryOrigin=$1" "${KEPT[@]}"
}

echo "Building API..."
sam build --template-file template.yaml

ORIGIN=${PRIMARY_ORIGIN:-$(output SiteUrl)}
deploy_stack "${ORIGIN:-https://app.lyrpay.com}"

# First deploy: the CloudFront domain only exists now, so sign-in callbacks and payment redirects are pointed at it
SITE_URL=$(output SiteUrl)
if [ -z "${PRIMARY_ORIGIN:-}" ] && [ "$ORIGIN" != "$SITE_URL" ]; then
  deploy_stack "$SITE_URL"
fi

echo "Building web dashboard..."
(
  cd frontend
  npm ci
  VITE_COGNITO_REGION="$REGION" \
  VITE_COGNITO_CLIENT_ID="$(output UserPoolClientId)" \
  VITE_COGNITO_DOMAIN="$COGNITO_DOMAIN" \
    npm run build
)

BUCKET=$(output SiteBucketName)
aws s3 sync frontend/dist/assets "s3://$BUCKET/assets" --cache-control "public,max-age=31536000,immutable"
aws s3 sync frontend/dist "s3://$BUCKET" --delete --exclude "assets/*" --cache-control "no-cache"
aws cloudfront create-invalidation --distribution-id "$(output DistributionId)" --paths "/*" >/dev/null

echo "Lyrpay is live at $SITE_URL"
