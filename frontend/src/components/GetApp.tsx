import { inApp } from '../lib/auth';
import Logo, { LogoMark, SiteFooter } from './Logo';

// mobile/publish-apk.sh uploads the signed APK to this path and rewrites the version line below
const APK_URL = '/downloads/lyrpay.apk';

const FEATURES: [string, string, string][] = [
  ['＋', 'Add money', 'By card, Apple Pay, Google Pay or PayPal'],
  ['↗', 'Send & receive', 'To anyone by email or mobile number, instantly'],
  ['⇄', 'Convert', '160+ currencies at the live mid-market rate'],
  ['🏦', 'Withdraw', 'To your bank, tracked live until it lands'],
  ['🛍', 'Pay in Lyr apps', 'Lyrshop, Lyrtrip and Lyrcloud in one tap'],
  ['🎁', 'Rewards', 'Points, gift cards and Bitcoin rewards'],
];

const STEPS: [string, string][] = [
  ['Download', 'Tap Download for Android. The file is lyrpay.apk, about 3 MB.'],
  ['Allow & install', 'Open it from your notifications. If Android asks, allow your browser to install apps, then tap Install.'],
  ['Sign in', 'Open Lyrpay and use the email and password of any Lyr app. Your wallet is already there.'],
];

/** lyrpay.com/app: download the Android app. */
export default function GetApp() {
  const already = inApp();
  return (
    <div className="dl-page">
      <header className="topbar">
        <Logo />
        <span className="spacer" />
        <a className="button" href="/">← Back to Lyrpay</a>
      </header>

      <main className="dl-main">
        <section className="dl-hero">
          <div className="dl-copy">
            <span className="hero-badge"><span className="live-dot" /> Free · Android 7.0 or newer</span>
            <h1>Lyrpay in your <span className="gradient-text">pocket</span></h1>
            <p className="dl-lead">Your wallet on your phone: add money, send and receive, convert 160+ currencies, withdraw to your bank and pay across the Lyr apps.</p>
            {already ? (
              <p className="info">You're already using the Lyrpay app. 🎉</p>
            ) : (
              <div className="dl-cta">
                <a className="dl-button" href={APK_URL} download="lyrpay.apk">
                  <span className="dl-button-icon" aria-hidden="true">
                    <svg viewBox="0 0 24 24" width="26" height="26"><path fill="currentColor" d="M6 18c0 .55.45 1 1 1h1v3.5a1.5 1.5 0 0 0 3 0V19h2v3.5a1.5 1.5 0 0 0 3 0V19h1c.55 0 1-.45 1-1V8H6v10zM3.5 8A1.5 1.5 0 0 0 2 9.5v7a1.5 1.5 0 0 0 3 0v-7A1.5 1.5 0 0 0 3.5 8zm17 0A1.5 1.5 0 0 0 19 9.5v7a1.5 1.5 0 0 0 3 0v-7A1.5 1.5 0 0 0 20.5 8zm-4.97-5.84 1.3-1.3a.5.5 0 0 0-.7-.71l-1.48 1.48A5.96 5.96 0 0 0 12 1c-.96 0-1.86.23-2.66.63L7.85.15a.5.5 0 0 0-.7.7l1.31 1.32A5.97 5.97 0 0 0 6 7h12a5.97 5.97 0 0 0-2.47-4.84zM10 5H9V4h1v1zm5 0h-1V4h1v1z" /></svg>
                  </span>
                  <span><small>Download for</small><b>Android</b></span>
                </a>
                <p className="muted small" data-app-meta>Version 2.0 · 3.3 MB · Android 7.0 or newer</p>
              </div>
            )}
            <ul className="dl-trust">
              <li>🔒 Signed by Lyrpay</li>
              <li>🔑 One login for every Lyr app</li>
              <li>⚡ Live balances and tracking</li>
            </ul>
          </div>

          <div className="dl-visual" aria-hidden="true">
            <div className="dl-phone">
              <div className="dl-notch" />
              <div className="dl-screen">
                <div className="dl-screen-top"><LogoMark size={22} /><span>Lyrpay</span></div>
                <div className="dl-balance"><small>Total balance</small><strong>$1,284.50</strong></div>
                <div className="dl-actions"><span>＋<i>Add</i></span><span>↗<i>Send</i></span><span>⇄<i>Convert</i></span><span>🏦<i>Withdraw</i></span></div>
                <ul className="dl-rows">
                  <li><b className="in">↙</b><span>From Sam</span><em className="positive">+$40.00</em></li>
                  <li><b className="fx">⇄</b><span>USD → EUR</span><em>€91.60</em></li>
                  <li><b className="out">🛍</b><span>Lyrshop</span><em>-$24.99</em></li>
                  <li><b className="bank">🏦</b><span>To bank</span><em>-$100.00</em></li>
                </ul>
              </div>
            </div>
            <span className="dl-float f1">✓ Sent instantly</span>
            <span className="dl-float f2">🎁 +120 points</span>
          </div>
        </section>

        <section className="dl-features">
          {FEATURES.map(([icon, title, text], i) => (
            <div key={title} className={`dl-feature c${(i % 6) + 1}`}>
              <span className="dl-feature-icon">{icon}</span>
              <strong>{title}</strong>
              <span className="muted small">{text}</span>
            </div>
          ))}
        </section>

        <section className="card dl-install">
          <h2>Install in three steps</h2>
          <ol className="dl-steps">
            {STEPS.map(([title, text], i) => (
              <li key={title}><span className="dl-step-num">{i + 1}</span><div><strong>{title}</strong><p className="muted">{text}</p></div></li>
            ))}
          </ol>
        </section>

        <section className="card dl-iphone">
          <span className="dl-feature-icon">🍎</span>
          <div>
            <h2>On iPhone?</h2>
            <p className="muted">Open lyrpay.com in Safari, tap <b>Share</b>, then <b>Add to Home Screen</b>. Lyrpay opens like an app. The Android app is also coming to Google Play.</p>
          </div>
        </section>
      </main>
      <SiteFooter />
    </div>
  );
}
