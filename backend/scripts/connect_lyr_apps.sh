#!/usr/bin/env bash
# Switches on "Pay with Lyrpay" for Lyrshop, Lyrtrip and Lyrcloud in one go:
#   1. registers lyrshop, lyrtrip and lyrcloud as Lyr apps in Lyrpay (new keys for an app that already exists)
#   2. saves Lyrshop's and Lyrcloud's keys in the lyrshop/lyrpay and lyrcloud/lyrpay secrets
#   3. Lyrtrip: its keys and NEXT_PUBLIC_LYRPAY=true into .env.local, then its site deploy
#   4. Lyrcloud: pushes main, deploys the domains API and the site
# The keys never appear on screen: they go from Lyrpay straight to where each app reads them.
#
#   bash backend/scripts/connect_lyr_apps.sh            # from the Lyrpay repo
#   ONLY="lyrcloud" bash backend/scripts/connect_lyr_apps.sh   # just some of them
set -euo pipefail

LYRPAY_BACKEND="$(cd "$(dirname "$0")/.." && pwd)"
LYRTRIP="${LYRTRIP_DIR:-/home/mrs/Lyrtrip/Lyrtrip}"
LYRCLOUD="${LYRCLOUD_DIR:-/home/mrs/Lyrcloud}"
ONLY="${ONLY:-lyrshop lyrtrip lyrcloud}"
REGION=us-east-1
KEYS="$(mktemp -d)"
chmod 700 "$KEYS"
trap 'rm -f "$KEYS"/*.json; rmdir "$KEYS"' EXIT
wants() { [[ " $ONLY " == *" $1 "* ]]; }

save_secret() {  # save_secret <name> <json file>
  if aws secretsmanager describe-secret --region $REGION --secret-id "$1" >/dev/null 2>&1; then
    aws secretsmanager put-secret-value --region $REGION --secret-id "$1" --secret-string "file://$2" --query VersionId --output text >/dev/null
  else
    aws secretsmanager create-secret --region $REGION --name "$1" --secret-string "file://$2" --query Name --output text >/dev/null
  fi
  echo "   saved $1"
}

echo "1/4 Registering in Lyrpay: $ONLY"
cd "$LYRPAY_BACKEND"
. .venv/bin/activate
AWS_DEFAULT_REGION=$REGION KEYS="$KEYS" ONLY="$ONLY" python - <<'EOF'
import json, os
from app import ledger, merchants

apps = [
    {"id": "lyrshop", "name": "Lyrshop", "webhookUrl": "https://lyrshop.com/orders/lyrpay/callback",
     "returnOrigins": "https://lyrshop.com https://www.lyrshop.com"},
    {"id": "lyrtrip", "name": "Lyrtrip",
     "webhookUrl": "https://73j9469lxd.execute-api.us-east-1.amazonaws.com/api/lyrpay/callback",
     "returnOrigins": "https://www.lyrtrip.com https://lyrtrip.com"},
    {"id": "lyrcloud", "name": "Lyrcloud",
     "webhookUrl": "https://uqp5mc2gq0.execute-api.us-east-1.amazonaws.com/public/lyrpay/callback",
     "returnOrigins": "https://www.lyrcloud.com https://lyrcloud.com"},
]
for app in apps:
    if app["id"] not in os.environ["ONLY"].split():
        continue
    exists = ledger.get_item(f"MERCHANT#{app['id']}", "MERCHANT") is not None
    made = merchants.save_merchant({**app, "rotate": exists}, "lyrclouds@gmail.com")
    path = os.path.join(os.environ["KEYS"], f"{app['id']}.json")
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump({"secret_key": made["secretKey"], "webhook_secret": made["webhookSecret"]}, f)
    print(f"   {app['id']}: {'new keys' if exists else 'registered'} (key ends …{made['keyHint']})")
EOF
deactivate

echo "2/4 Saving keys in Secrets Manager"
wants lyrshop && save_secret lyrshop/lyrpay "$KEYS/lyrshop.json"
wants lyrcloud && save_secret lyrcloud/lyrpay "$KEYS/lyrcloud.json"

if wants lyrtrip; then
  echo "3/4 Lyrtrip: keys into .env.local, then the site deploy"
  ENV="$LYRTRIP/.env.local"
  [[ -f "$ENV" ]] || { echo "Missing $ENV" >&2; exit 1; }
  cp "$ENV" "$ENV.before-lyrpay"
  chmod 600 "$ENV.before-lyrpay"
  grep -v -E '^(LYRPAY_SECRET_KEY|LYRPAY_WEBHOOK_SECRET|NEXT_PUBLIC_LYRPAY)=' "$ENV.before-lyrpay" > "$ENV"
  KEYS_FILE="$KEYS/lyrtrip.json" python3 - >> "$ENV" <<'EOF'
import json, os
k = json.load(open(os.environ["KEYS_FILE"]))
print(f"LYRPAY_SECRET_KEY={k['secret_key']}\nLYRPAY_WEBHOOK_SECRET={k['webhook_secret']}\nNEXT_PUBLIC_LYRPAY=true")
EOF
  echo "   .env.local updated (previous copy: .env.local.before-lyrpay)"
  (cd "$LYRTRIP" && bash infra/deploy-site.sh)
fi

if wants lyrcloud; then
  echo "4/4 Lyrcloud: push, domains API, site"
  (cd "$LYRCLOUD" && git push origin main && bash scripts/deploy-domains.sh && bash scripts/deploy-live.sh)
fi

echo
echo "Done. Lyrshop shows Pay with Lyrpay within 5 minutes; Lyrtrip and Lyrcloud show it now."
