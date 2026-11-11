#!/usr/bin/env bash
# Switches on "Pay with Lyrpay" for Lyrshop and Lyrtrip in one go:
#   1. registers lyrshop and lyrtrip as Lyr apps in Lyrpay (new keys for an app that already exists)
#   2. saves Lyrshop's keys in the lyrshop/lyrpay secret (Lyrshop picks them up within 5 minutes)
#   3. puts Lyrtrip's keys and NEXT_PUBLIC_LYRPAY=true in Lyrtrip's .env.local and runs its site deploy
# The keys never appear on screen: they go from Lyrpay straight to where each app reads them.
#
#   bash backend/scripts/connect_lyr_apps.sh            # from the Lyrpay repo
set -euo pipefail

LYRPAY_BACKEND="$(cd "$(dirname "$0")/.." && pwd)"
LYRTRIP="${LYRTRIP_DIR:-/home/mrs/Lyrtrip/Lyrtrip}"
REGION=us-east-1
KEYS="$(mktemp -d)"
chmod 700 "$KEYS"
trap 'rm -f "$KEYS"/lyrshop.json "$KEYS"/lyrtrip.json; rmdir "$KEYS"' EXIT

echo "1/3 Registering lyrshop and lyrtrip in Lyrpay"
cd "$LYRPAY_BACKEND"
. .venv/bin/activate
AWS_DEFAULT_REGION=$REGION KEYS="$KEYS" python - <<'EOF'
import json, os
from app import ledger, merchants

apps = [
    {"id": "lyrshop", "name": "Lyrshop", "webhookUrl": "https://lyrshop.com/orders/lyrpay/callback",
     "returnOrigins": "https://lyrshop.com https://www.lyrshop.com"},
    {"id": "lyrtrip", "name": "Lyrtrip",
     "webhookUrl": "https://73j9469lxd.execute-api.us-east-1.amazonaws.com/api/lyrpay/callback",
     "returnOrigins": "https://www.lyrtrip.com https://lyrtrip.com"},
]
for app in apps:
    exists = ledger.get_item(f"MERCHANT#{app['id']}", "MERCHANT") is not None
    made = merchants.save_merchant({**app, "rotate": exists}, "lyrclouds@gmail.com")
    path = os.path.join(os.environ["KEYS"], f"{app['id']}.json")
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump({"secret_key": made["secretKey"], "webhook_secret": made["webhookSecret"]}, f)
    print(f"   {app['id']}: {'new keys' if exists else 'registered'} (key ends …{made['keyHint']})")
EOF
deactivate

echo "2/3 Saving Lyrshop's keys in lyrshop/lyrpay"
if aws secretsmanager describe-secret --region $REGION --secret-id lyrshop/lyrpay >/dev/null 2>&1; then
  aws secretsmanager put-secret-value --region $REGION --secret-id lyrshop/lyrpay \
    --secret-string "file://$KEYS/lyrshop.json" --query VersionId --output text >/dev/null
else
  aws secretsmanager create-secret --region $REGION --name lyrshop/lyrpay \
    --secret-string "file://$KEYS/lyrshop.json" --query Name --output text >/dev/null
fi
echo "   saved"

echo "3/3 Lyrtrip: keys into .env.local, then the site deploy"
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
cd "$LYRTRIP"
bash infra/deploy-site.sh

echo
echo "Done. Lyrshop shows Pay with Lyrpay within 5 minutes; Lyrtrip shows it now."
