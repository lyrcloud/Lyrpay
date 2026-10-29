import { useState } from 'react';
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
import { COUNTRIES, currencyOf, flag, guessCountry, toE164 } from '../lib/format';
import LiveMarkets, { Ticker } from './LiveMarkets';
import Logo from './Logo';

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

export default function AuthPage({ onSignedIn }: { onSignedIn: () => void }) {
  const [mode, setMode] = useState<Mode>('signin');
  const [step, setStep] = useState<Step>('form');
  const [via, setVia] = useState<'email' | 'phone'>('email');
  const [country, setCountry] = useState(guessCountry);
  const [number, setNumber] = useState('');
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [info, setInfo] = useState('');

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
  };

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

  return (
    <div className="home">
      <Ticker />
      <div className="auth">
        <section className="auth-intro">
          <Logo size={44} large />
          <h1>Money for every country, <span className="gradient-text">in every currency.</span></h1>
          <p>
            Hold, send and convert {`160+`} currencies, top up with cards and local payment methods, and follow the crypto
            markets from one account.
          </p>
          <ul className="auth-points">
            <li>Pay anyone on Lyrpay by email or mobile number</li>
            <li>Live mid-market exchange rates</li>
            <li>One Lyr account for Lyrpay and Lyrtrip</li>
          </ul>
          <div className="auth-stats">
            <div><strong>160+</strong><span>currencies</span></div>
            <div><strong>50</strong><span>live crypto prices</span></div>
            <div><strong>0.5%</strong><span>conversion fee</span></div>
          </div>
          <a href="#markets" className="link scroll-hint">See live markets ↓</a>
        </section>

        <section className="card auth-card">
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
              <button type="button" className="link" onClick={() => switchMode(mode)}>Back</button>
            </form>
          )}
        </section>
      </div>
      <div className="home-markets"><LiveMarkets home={currencyOf(country)} /></div>
      <footer className="site-footer"><Logo size={22} /><span>© {new Date().getFullYear()} Lyrpay · Prices from CoinGecko and open.er-api.com</span></footer>
    </div>
  );
}
