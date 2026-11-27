#!/usr/bin/env bash
# Build the signed Android APK and publish it as lyrpay.com's "Download for Android" file (/downloads/lyrpay.apk),
# then update the version line on frontend/src/components/GetApp.tsx.
# Usage: mobile/publish-apk.sh [stack-name]   (then ./deploy.sh so lyrpay.com/app shows the new version line)
set -euo pipefail
cd "$(dirname "$0")"

STACK="${1:-lyrpay}"
export ANDROID_HOME="${ANDROID_HOME:-$HOME/Android/Sdk}"
JDK="$HOME/.local/jdk/jdk-21.0.12.1+1"
[ -d "$JDK" ] && export JAVA_HOME="$JDK" PATH="$JDK/bin:$PATH"
# The upload key lives outside the repo; back up ~/.lyrpay-keys, it can't be recreated
[ -f android/keystore.properties ] || cp "$HOME/.lyrpay-keys/keystore.properties" android/keystore.properties

npx cap sync android
(cd android && ./gradlew --quiet assembleRelease)

APK=android/app/build/outputs/apk/release/app-release.apk
APKSIGNER="$(ls -d "$ANDROID_HOME"/build-tools/*/ | sort -V | tail -1)apksigner"
"$APKSIGNER" verify "$APK"   # refuses an unsigned or broken build

VERSION="$(sed -n 's/.*versionName "\(.*\)".*/\1/p' android/app/build.gradle | head -1)"
SIZE="$(awk -v b="$(stat -c %s "$APK")" 'BEGIN { printf "%.1f MB", b / 1048576 }')"
cp "$APK" "$HOME/Desktop/Lyrpay-$VERSION.apk" 2>/dev/null || true

output() { aws cloudformation describe-stacks --stack-name "$STACK" --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue | [0]" --output text; }
# deploy.sh leaves downloads/* alone when it syncs the site with --delete
aws s3 cp "$APK" "s3://$(output SiteBucketName)/downloads/lyrpay.apk" --only-show-errors \
  --content-type application/vnd.android.package-archive \
  --content-disposition 'attachment; filename="lyrpay.apk"' \
  --cache-control "public, max-age=300"
aws cloudfront create-invalidation --distribution-id "$(output DistributionId)" --paths "/downloads/lyrpay.apk" >/dev/null

sed -i "s#data-app-meta>Version [^·]* · [^·]* · #data-app-meta>Version $VERSION · $SIZE · #" ../frontend/src/components/GetApp.tsx
echo "Published Lyrpay $VERSION ($SIZE) to https://lyrpay.com/downloads/lyrpay.apk"
echo "GetApp.tsx now says version $VERSION; run ./deploy.sh to show that line on lyrpay.com/app."
