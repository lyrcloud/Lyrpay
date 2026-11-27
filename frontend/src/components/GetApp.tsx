import { inApp } from '../lib/auth';
import { LogoMark, SiteFooter } from './Logo';

// mobile/publish-apk.sh uploads the signed APK to this path and rewrites the version line below
const APK_URL = '/downloads/lyrpay.apk';

/** lyrpay.com/app: download the Android app. */
export default function GetApp() {
  return (
    <div className="shell">
      <main className="main get-app">
        <section className="card">
          <div className="get-app-head">
            <LogoMark size={56} />
            <h1>Get the Lyrpay app</h1>
          </div>
          <p>Your wallet on your phone: add money, send and receive, convert currencies, withdraw to your bank and pay in the Lyr apps.</p>
          {inApp() ? (
            <p className="info">You're already using the Lyrpay app.</p>
          ) : (
            <>
              <a className="button primary" href={APK_URL} download="lyrpay.apk">Download for Android</a>
              <p className="muted small" data-app-meta>Version 2.0 · 3.3 MB · Android 7.0 or newer</p>
            </>
          )}
          <h2>How to install</h2>
          <ol>
            <li>Tap <strong>Download for Android</strong> above.</li>
            <li>Open the downloaded <strong>lyrpay.apk</strong> from your notifications or Downloads.</li>
            <li>If Android asks, allow your browser to <strong>install unknown apps</strong>, then tap <strong>Install</strong>.</li>
            <li>Open Lyrpay and sign in with the same Lyr account you use here.</li>
          </ol>
          <p className="muted">The app is coming to Google Play. On iPhone, open lyrpay.com in Safari, tap Share, then <strong>Add to Home Screen</strong>.</p>
          <a className="link" href="/">← Back to Lyrpay</a>
        </section>
      </main>
      <SiteFooter />
    </div>
  );
}
