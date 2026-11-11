import { useEffect, useRef, useState } from 'react';
import {
  confirmForgotPassword,
  confirmSignUp,
  forgotPassword,
  phoneLoginEmail,
  registerPhone,
  resendConfirmation,
  resetPhonePassword,
  sendPhoneCode,
  signIn,
  signInWithGoogle,
  signUp,
} from '../lib/auth';
import { COUNTRIES, currencyOf, flag, formatMoney, guessCountry, toE164 } from '../lib/format';
import { pendingReferral } from '../lib/referral';
import LiveMarkets, { Ticker } from './LiveMarkets';
import Logo, { SiteFooter } from './Logo';
import { AiPulse, MarketClocks } from './StockMarkets';
import { type CheckoutSummary } from '../lib/api';

type Mode = 'signin' | 'signup';
type Step = 'form' | 'confirm' | 'reset';

// The account pool's password rules, checked before a code is sent so a rejected password doesn't use it up
const PASSWORD_RULES = /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{8,}$/;
const PASSWORD_HINT = 'At least 8 characters with upper and lower case letters, a number and a symbol.';

function message(error: unknown) {
  const { name, message } = error as Error;
  if (name === 'NotAuthorizedException') return 'Incorrect email, mobile number or password.';
  if (name === 'UserNotConfirmedException') return 'Confirm your email first.';
  if (name === 'UsernameExistsException') return 'An account already uses this email. Sign in instead.';
  if (name === 'CodeMismatchException' || name === 'ExpiredCodeException') return 'That code is wrong or has expired.';
  if (name === 'LimitExceededException' || name === 'TooManyRequestsException') return 'Too many tries. Please wait a few minutes.';
  return message || 'Something went wrong. Please try again.';
}

/** Counts up to the number once it scrolls into view. */
function CountUp({ to, suffix = '', decimals = 0 }: { to: number; suffix?: string; decimals?: number }) {
  const ref = useRef<HTMLElement>(null);
  const [value, setValue] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) return setValue(to);
    let frame = 0;
    const observer = new IntersectionObserver(([entry]) => {
      if (!entry.isIntersecting) return;
      observer.disconnect();
      const start = performance.now();
      const step = (t: number) => {
        const p = Math.min(1, (t - start) / 1400);
        setValue(to * (1 - Math.pow(1 - p, 3)));
        if (p < 1) frame = requestAnimationFrame(step);
      };
      frame = requestAnimationFrame(step);
    });
    observer.observe(el);
    return () => { observer.disconnect(); cancelAnimationFrame(frame); };
  }, [to]);
  return <strong ref={ref}>{value.toFixed(decimals)}{suffix}</strong>;
}

/** Fades sections in as they scroll into view. */
function useReveal() {
  useEffect(() => {
    const els = document.querySelectorAll('.reveal');
    if (!('IntersectionObserver' in window)) return els.forEach(el => el.classList.add('in'));
    const observer = new IntersectionObserver(entries => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          entry.target.classList.add('in');
          observer.unobserve(entry.target);
        }
      }
    }, { rootMargin: '0px 0px -8% 0px' });
    els.forEach(el => observer.observe(el));
    return () => observer.disconnect();
  }, []);
}

/** A soft glow that follows the pointer across the hero. */
function spotlight(event: React.PointerEvent<HTMLElement>) {
  const r = event.currentTarget.getBoundingClientRect();
  event.currentTarget.style.setProperty('--sx', `${event.clientX - r.left}px`);
  event.currentTarget.style.setProperty('--sy', `${event.clientY - r.top}px`);
}

const FEATURES = [
  { icon: '🤖', title: 'AI shares, live', text: 'NVIDIA, Microsoft, Alphabet, AMD, Palantir and more, with a live pulse of the whole AI market.' },
  { icon: '📊', title: 'ETFs and indices', text: 'S&P 500, Nasdaq, Nifty, Nikkei, DAX and popular funds from SPY and QQQ to AI and chip ETFs.' },
  { icon: '🪙', title: 'Crypto and commodities', text: 'The top 50 coins by market cap, plus gold, silver, oil and copper futures.' },
  { icon: '💱', title: '160+ currencies', text: 'Hold, send and convert at the live mid-market rate with a flat 0.5% fee.' },
  { icon: '⚡', title: 'Pay anyone instantly', text: 'Send money to any Lyrpay user by email or mobile number in seconds.' },
  { icon: '🎁', title: 'Rewards and gift cards', text: 'Earn for inviting friends, send gift cards and run a business account.' },
];

export default function AuthPage({ onSignedIn, checkout }: { onSignedIn: () => void; checkout?: CheckoutSummary | null }) {
  const [invited] = useState(() => !!pendingReferral());
  const gift = new URLSearchParams(location.search).get('gift');
  // Most people paying a Lyr app are new to Lyrpay: start them on Create account, with the shop's email filled in
  const [mode, setMode] = useState<Mode>(invited || checkout ? 'signup' : 'signin');
  const [step, setStep] = useState<Step>('form');
  const [via, setVia] = useState<'email' | 'phone'>('email');
  const [country, setCountry] = useState(guessCountry);
  const [number, setNumber] = useState('');
  const [email, setEmail] = useState(checkout?.email ?? '');
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [info, setInfo] = useState('');
  // Seconds until another code can be asked for, so a double tap doesn't burn the sending limit
  const [wait, setWait] = useState(0);
  useEffect(() => {
    if (!wait) return;
    const timer = setTimeout(() => setWait(w => w - 1), 1000);
    return () => clearTimeout(timer);
  }, [wait]);

  const phone = () => {
    const e164 = toE164(number, country);
    if (!e164) throw new Error('Enter a valid mobile number for the selected country.');
    return e164;
  };
  const username = () => (via === 'phone' ? phoneLoginEmail(phone()) : email.trim().toLowerCase());

  const run = (action: () => Promise<void>) => async (event?: React.FormEvent) => {
    event?.preventDefault();
    setBusy(true);
    setError('');
    try {
      await action();
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  };

  const openStep = (next: Step, text: string) => {
    setCode('');
    setStep(next);
    setInfo(text);
    setWait(30);
  };

  const resend = run(async () => {
    if (step === 'reset') {
      if (via === 'phone') await sendPhoneCode(phone(), 'reset');
      else await forgotPassword(email.trim().toLowerCase());
    } else if (via === 'phone') {
      await sendPhoneCode(phone(), 'signup');
    } else {
      await resendConfirmation(email.trim().toLowerCase());
    }
    setInfo(via === 'phone' ? `We texted a new code to ${phone()}.` : `We emailed a new code to ${email}. It can take a minute; check your spam folder too.`);
    setWait(30);
  });

  const submit = run(async () => {
    if (mode === 'signin') {
      try {
        await signIn(username(), password);
      } catch (err) {
        if ((err as Error).name !== 'UserNotConfirmedException') throw err;
        await resendConfirmation(email);
        return openStep('confirm', `Your email isn't confirmed yet. We sent a new code to ${email}.`);
      }
      return onSignedIn();
    }
    if (!PASSWORD_RULES.test(password)) throw new Error(PASSWORD_HINT);
    if (via === 'phone') {
      await sendPhoneCode(phone(), 'signup');
      return openStep('confirm', `We texted a 6-digit code to ${phone()}.`);
    }
    await signUp(email.trim().toLowerCase(), password, name.trim());
    openStep('confirm', `We emailed a 6-digit code to ${email}.`);
  });

  const confirm = run(async () => {
    if (via === 'phone') await registerPhone(phone(), code.trim(), password, name.trim());
    else await confirmSignUp(email.trim().toLowerCase(), code.trim());
    await signIn(username(), password);
    onSignedIn();
  });

  const startReset = run(async () => {
    if (via === 'phone') {
      await sendPhoneCode(phone(), 'reset');
      openStep('reset', `We texted a code to ${phone()}. Choose a new password below.`);
    } else {
      if (!email) throw new Error('Enter your email first.');
      await forgotPassword(email.trim().toLowerCase());
      openStep('reset', `If ${email} has an account, we emailed it a code. Choose a new password below.`);
    }
  });

  const finishReset = run(async () => {
    if (!PASSWORD_RULES.test(password)) throw new Error(PASSWORD_HINT);
    if (via === 'phone') await resetPhonePassword(phone(), code.trim(), password);
    else await confirmForgotPassword(email.trim().toLowerCase(), code.trim(), password);
    await signIn(username(), password);
    onSignedIn();
  });

  const switchMode = (next: Mode) => {
    setMode(next);
    setStep('form');
    setError('');
    setInfo('');
  };

  useReveal();

  return (
    <div className="home">
      <Ticker />
      <div className="auth hero" onPointerMove={spotlight}>
        {checkout ? (
        <section className="auth-intro pay-intro">
          <Logo size={44} large />
          <p className="muted">{checkout.merchant} asks you to pay</p>
          <h1 className="pay-amount">{formatMoney(checkout.amount, checkout.currency)}</h1>
          {checkout.description && <p>{checkout.description}</p>}
          <ol className="pay-steps">
            <li><b>Create your free Lyrpay account</b>, or sign in if you have one</li>
            <li><b>Add the amount by card</b> if your balance is short (Apple Pay and Google Pay work too)</li>
            <li><b>Pay</b> and go straight back to {checkout.merchant}</li>
          </ol>
          <p className="muted small">Refunds come straight back to your Lyrpay balance. <a href={checkout.cancelUrl}>Cancel and return to {checkout.merchant}</a></p>
        </section>
        ) : (
        <section className="auth-intro">
          <Logo size={44} large />
          <span className="hero-badge"><span className="live-dot" /> Live: AI shares · ETFs · indices · crypto · FX</span>
          <h1>Your money and the <span className="gradient-text">world’s markets,</span> in one place.</h1>
          <p>
            Hold, send and convert 160+ currencies, and follow AI shares, ETFs, world indices, crypto and commodities
            live, from one Lyr account.
          </p>
          <ul className="auth-points">
            <li>Pay anyone on Lyrpay by email or mobile number</li>
            <li>Live mid-market exchange rates</li>
            <li>One Lyr account for Lyrpay and Lyrtrip</li>
          <li>Gift cards, referral rewards and business accounts</li>
          </ul>
          <div className="auth-stats">
            <div><CountUp to={160} suffix="+" /><span>currencies</span></div>
            <div><CountUp to={68} suffix="+" /><span>shares, ETFs and indices</span></div>
            <div><CountUp to={50} /><span>live crypto prices</span></div>
            <div><CountUp to={0.5} decimals={1} suffix="%" /><span>conversion fee</span></div>
          </div>
          <div className="hero-links">
            <a href="#ai" className="button primary">AI market pulse</a>
            <a href="#markets" className="button">All live markets ↓</a>
          </div>
        </section>
        )}

        <section className="card auth-card">
        {checkout && <p className="info invite">Create a free Lyrpay account, or sign in, to pay {checkout.merchant}.</p>}
        {!checkout && (invited || gift) && (
          <p className="info invite">{gift ? '🎁 Someone sent you a Lyrpay gift card. Sign in or create a free wallet to redeem it.' : '🎉 A friend invited you to Lyrpay. Create your free wallet to accept.'}</p>
        )}
          {step === 'form' ? (
            <>
              <div className="tabs" role="tablist">
                <button role="tab" aria-selected={mode === 'signin'} onClick={() => switchMode('signin')}>Sign in</button>
                <button role="tab" aria-selected={mode === 'signup'} onClick={() => switchMode('signup')}>Create account</button>
              </div>

              <button className="button google" type="button" disabled={busy} onClick={run(signInWithGoogle)}>
                <svg viewBox="0 0 48 48" width="18" height="18" aria-hidden="true">
                  <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.6 5.4 2.7 13.3l7.9 6.1C12.5 13.6 17.8 9.5 24 9.5z" />
                  <path fill="#4285F4" d="M46.5 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.7c-.6 3-2.3 5.5-4.8 7.2l7.7 6c4.5-4.2 6.9-10.3 6.9-17.7z" />
                  <path fill="#FBBC05" d="M10.6 28.6c-.5-1.4-.8-3-.8-4.6s.3-3.2.8-4.6l-7.9-6.1C1 16.6 0 20.2 0 24s1 7.4 2.7 10.7l7.9-6.1z" />
                  <path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.7-6c-2.1 1.4-4.9 2.3-8.2 2.3-6.2 0-11.5-4.1-13.4-9.9l-7.9 6.1C6.6 42.6 14.6 48 24 48z" />
                </svg>
                Continue with Google
              </button>

              <div className="divider"><span>or</span></div>

              <div className="segmented">
                <button type="button" aria-pressed={via === 'email'} onClick={() => setVia('email')}>Email</button>
                <button type="button" aria-pressed={via === 'phone'} onClick={() => setVia('phone')}>Mobile number</button>
              </div>

              <form onSubmit={submit} className="form">
                {mode === 'signup' && (
                  <label>Full name<input value={name} onChange={e => setName(e.target.value)} autoComplete="name" /></label>
                )}
                {via === 'email' ? (
                  <label>Email
                    <input type="email" required value={email} onChange={e => setEmail(e.target.value)} autoComplete="email" />
                  </label>
                ) : (
                  <label>Mobile number
                    <div className="phone-row">
                      <select value={country} onChange={e => setCountry(e.target.value)} aria-label="Country code">
                        {COUNTRIES.map(c => <option key={c.code} value={c.code}>{flag(c.code)} {c.name} +{c.dial}</option>)}
                      </select>
                      <input type="tel" required value={number} onChange={e => setNumber(e.target.value)} autoComplete="tel-national" />
                    </div>
                  </label>
                )}
                <label>Password
                  <input
                    type="password" required value={password} onChange={e => setPassword(e.target.value)}
                    autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
                  />
                </label>
                {mode === 'signup' && <p className="hint">{PASSWORD_HINT}</p>}
                {error && <p className="error" role="alert">{error}</p>}
                <button className="button primary" disabled={busy}>
                  {busy ? 'Please wait…' : mode === 'signin' ? 'Sign in' : 'Create account'}
                </button>
                {mode === 'signin' && (
                  <button type="button" className="link" disabled={busy} onClick={() => startReset()}>Forgot password?</button>
                )}
              </form>
              <p className="hint center">Already use Lyrtrip? Sign in with the same account.</p>
            </>
          ) : (
            <form onSubmit={step === 'confirm' ? confirm : finishReset} className="form">
              <h2>{step === 'confirm' ? 'Enter your code' : 'Reset your password'}</h2>
              {info && <p className="info">{info}</p>}
              <label>6-digit code
                <input inputMode="numeric" autoComplete="one-time-code" required value={code} onChange={e => setCode(e.target.value)} />
              </label>
              {step === 'reset' && (
                <label>New password
                  <input type="password" required value={password} onChange={e => setPassword(e.target.value)} autoComplete="new-password" />
                </label>
              )}
              {error && <p className="error" role="alert">{error}</p>}
              <button className="button primary" disabled={busy}>{busy ? 'Please wait…' : 'Continue'}</button>
              <p className="hint center">
                {via === 'email' ? "Didn't get it? Check your spam folder, or " : "Didn't get it? "}
                <button type="button" className="link" disabled={busy || wait > 0} onClick={() => resend()}>
                  {wait > 0 ? `send a new code in ${wait}s` : 'send a new code'}
                </button>
              </p>
              <button type="button" className="link" onClick={() => switchMode(mode)}>Back</button>
            </form>
          )}
        </section>
      </div>
      <div className="home-section"><MarketClocks /></div>
      <div className="home-section"><AiPulse /></div>
      <div className="home-section">
        <div className="features">
          {FEATURES.map((f, i) => (
            <article className="feature reveal" key={f.title} style={{ transitionDelay: `${i * 60}ms` }}>
              <span className="feature-icon" aria-hidden="true">{f.icon}</span>
              <h3>{f.title}</h3>
              <p className="muted">{f.text}</p>
            </article>
          ))}
        </div>
      </div>
      <div className="home-markets reveal"><LiveMarkets home={currencyOf(country)} /></div>
      <SiteFooter />
    </div>
  );
}
