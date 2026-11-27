# Lyrpay Android app

Capacitor shell around https://lyrpay.com, built the same way as the Lyrtrip and Lyrshop apps. The app loads the live
site, so a site deploy updates the app without a new build. Native pieces: splash screen, status bar, an offline
screen (`www/offline.html`), and Google sign-in through a Chrome Custom Tab (Google refuses sign-in inside WebViews):
the Hosted UI returns to `com.lyrpay.app://auth/callback`, which `MainActivity` hands to the site's `/auth/callback`.

App ID: `com.lyrpay.app`. The site can tell it's inside the app by `LyrpayApp` in the user agent or `window.Capacitor`.

## Build and publish

Needs JDK 21 and the Android SDK (API 36):

```bash
cd mobile
npm install
npm run android:debug     # android/app/build/outputs/apk/debug/app-debug.apk
./publish-apk.sh          # signed APK -> lyrpay.com/downloads/lyrpay.apk; then ../deploy.sh for the /app version line
```

Release builds are signed with the upload key in `~/.lyrpay-keys` (`lyrpay-upload.jks` and `keystore.properties`,
copied to `android/keystore.properties`, which is not in git). Back that folder up: updates to installed apps must be
signed with the same key. Bump `versionCode` (YYYYMMDD) and `versionName` in `android/app/build.gradle` for every build.

## Icons

`node make-icons.mjs && npm run assets` regenerates `assets/` and the launcher icons from `../frontend/public/favicon.svg`.

## iOS

Not added yet. `npx cap add ios` on a Mac with Xcode.
